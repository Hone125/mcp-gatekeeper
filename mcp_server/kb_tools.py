"""文本知识库：建索引、检索、按位置取原文。

## 为什么不用向量库

检索质量当然可以更好，但这一版的约束是「**零外部依赖、零费用、确定性、可离线**」：

- 向量检索要调 embedding 接口 → 要花钱、要联网、clone 下来跑不通；
- 本地向量库要装 `milvus-lite` 之类 → 二进制体积大，本机内存只有 1 GB；
- 而倒排索引在「给定关键词找段落」这个任务上本来就够用，且**结果完全确定** ——
  同一份语料、同一个查询，跑一万遍结果一模一样。确定性对一份要报数字的评测很重要。

jieba（中文切词）+ SQLite FTS5（倒排索引）就满足全部四条约束，两个依赖都已经是现成的。

## 坐标系统一（这里最容易出隐蔽 bug）

**偏移量只有一个来源：`documents.text`。** 检索命中的块只记 `char_start/char_end`，
要展示原文时一律用 `text[char_start:char_end]` 现场切片。

为什么不把块正文单独存一份？因为存两份就有两个真相，一旦构建期和查询期对
「怎么切、怎么归一化」的理解差一点点，返回的就会是**看起来合理、其实是别处**的文本 ——
这种错误不会报异常，只会静默给出错的内容，是最难查的一类。

`tests/test_passage_offsets.py` 用随机往返断言把这条钉死。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from pathlib import Path

from mcp_server import paths

# 每块的目标字符数。太小则一块里没有完整语义，太大则返回的片段里噪音多。
CHUNK_CHARS = 500
DEFAULT_TOP_K = 5
MAX_TOP_K = 20
DEFAULT_PASSAGE_LEN = 800
MAX_PASSAGE_LEN = 4000

# 建索引的 SQL。`UNINDEXED` 的列只存不索引 —— 它们要跟着命中行回来，
# 但不该参与关键词匹配（没人会去搜"500"这个 char_start）。
SCHEMA_SQL = """
CREATE TABLE documents (
    doc_id       TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    author       TEXT NOT NULL DEFAULT '',
    work         TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    license      TEXT NOT NULL DEFAULT '',
    release_date TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL,
    n_chars      INTEGER NOT NULL,
    sha256       TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    doc_id UNINDEXED,
    ord UNINDEXED,
    char_start UNINDEXED,
    char_end UNINDEXED,
    body_tok,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class KBError(Exception):
    def __init__(self, err_code: str, detail: str):
        super().__init__(f"{err_code}: {detail}")
        self.err_code = err_code
        self.detail = detail


E_NO_DB = "KB_NO_DB"
E_QUERY_SYNTAX = "KB_QUERY_SYNTAX"
E_NO_SUCH_DOC = "KB_NO_SUCH_DOC"
E_BAD_RANGE = "KB_BAD_RANGE"


# ---------------------------------------------------------------- 切词
_jieba = None


def _cut(text: str) -> list[str]:
    """中文切词。jieba 首次调用会加载词典（约 1 秒），所以延迟初始化。"""
    global _jieba
    if _jieba is None:
        import jieba
        jieba.setLogLevel(logging.WARNING)      # 别把 "Building prefix dict" 打到 stderr
        _jieba = jieba
    return [t for t in _jieba.lcut(text, cut_all=False) if t.strip()]


def tokenize(text: str) -> str:
    """切词后用空格拼成一列，供 FTS5 建索引。

    unicode61 分词器不认识中文 —— 它会把一整句连续汉字当成一个 token，
    于是「桃花源」这样的查询永远匹配不上。先切开再索引，是这套方案能work的关键一步。
    """
    return " ".join(_cut(text))


# 至少含一个字母或数字（下划线不算 —— 在 unicode61 里它是分隔符）。
# 纯标点/纯空白的词被它挡掉。
_RE_HAS_WORDCHAR = re.compile(r"[^\W_]", re.UNICODE)


def _quote_token(tok: str) -> str:
    """把一个词包成 FTS5 的字符串字面量。

    FTS5 的查询语法里 `"` `*` `(` `)` `:` `^` `-` `AND` `OR` `NOT` `NEAR` 都有特殊含义。
    把每个词用双引号包起来（内部的双引号翻倍转义），这些含义就全部失效、退化成普通词：

        数据库 AND    →  "数据库" OR "AND"     AND 不再是布尔运算符，是个普通词
        say "hi"     →  "say" OR "hi"        裸引号不再是语法
        茶 NEAR 水    →  "茶" OR "NEAR" OR "水"  NEAR 同上

    **这是实测过的，不是推理**：`.tmp` 下的探针拿 44 条敌意输入（空串、裸引号、
    括号、星号、反斜杠、`^`、`{}`、`~`、`C++`、`1+1` …）逐条真的丢给 FTS5 执行，
    结果是 —— **只要表达式非空，一条都没报错**；报错的 9 条全部是空表达式。
    也就是说「引号包裹」确实把语法错误消灭干净了，剩下唯一要处理的就是「一个词都没有」。
    """
    return '"' + tok.replace('"', '""') + '"'


def build_match_query(query: str) -> str:
    """把自然语言查询编成 FTS5 的 MATCH 表达式。**没有可检索的词时返回空串。**

    调用方必须把空串转成一个明确的错误（本模块用 `KB_QUERY_SYNTAX`），
    **不能直接丢给 SQLite** —— 实测空表达式会让 FTS5 抛
    `OperationalError: fts5: syntax error near ""`，而这条错误信息对用户毫无指导意义。
    """
    toks = [t for t in _cut(unicodedata.normalize("NFC", query or "")) if t.strip()]
    # 纯标点词（`。`、`---`、`!!!`）搜不到任何东西。留着它们只会让查询
    # "看起来执行成功了但零命中"，不如在这里挡掉，让调用方收到
    # 「你的查询里没有可检索的词」——这个信息比「没有结果」有用得多。
    toks = [t for t in toks if _RE_HAS_WORDCHAR.search(t)]
    if not toks:
        return ""
    # 用 OR 而不是 AND：中文口语提问里总会有「的」「是」这类无信息量的词，
    # 用 AND 会让整条查询一个结果都匹配不上。靠 bm25 排序把真正相关的顶到前面。
    return " OR ".join(_quote_token(t) for t in toks)


# ---------------------------------------------------------------- 分块
_RE_LINE = re.compile(r"[^\n]+")


def chunk_spans(text: str, target: int = CHUNK_CHARS) -> list[tuple[int, int, int]]:
    """把正文按行打包成块，返回 `[(ord, char_start, char_end)]`。

    偏移量直接取自每一行在原文里的位置，所以 `text[char_start:char_end]`
    就是这一块的内容 —— 不是"约等于"，是逐字符相等。
    """
    spans: list[tuple[int, int, int]] = []
    start: int | None = None
    end = 0
    for m in _RE_LINE.finditer(text):
        if start is None:
            start = m.start()
        end = m.end()
        if end - start >= target:
            spans.append((len(spans), start, end))
            start = None
    if start is not None:
        spans.append((len(spans), start, end))
    return spans


# ---------------------------------------------------------------- 连接
def connect(db_path: Path | None = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path is not None else paths.kb_db()
    if not p.is_file():
        raise KBError(E_NO_DB, f"知识库索引不存在：{p}；先跑 python experiments/02_build_kb.py")
    return sqlite3.connect(p)


# ---------------------------------------------------------------- 建索引
def build(db_path: Path | None = None, manifest_path: Path | None = None,
          force: bool = False) -> dict:
    """从 `data/kb/raw/*.txt` + `manifest.json` 建出 FTS5 索引。可重复运行。"""
    db = Path(db_path) if db_path is not None else paths.kb_db()
    man = Path(manifest_path) if manifest_path is not None else paths.kb_manifest()
    raw_dir = man.parent / "raw"

    if not man.is_file():
        raise KBError(E_NO_DB, f"清单不存在：{man}；先跑 python experiments/00_fetch_corpus.py")

    manifest = json.loads(man.read_text(encoding="utf-8"))
    docs = manifest["docs"]

    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        if not force:
            # 已有索引就只核验，不重建 —— 让这个脚本可以无脑重跑
            existing = verify(db)
            if existing["ok"] and existing["n_docs"] == len(docs):
                return {"rebuilt": False, **existing}
        db.unlink()

    conn = sqlite3.connect(db)
    try:
        conn.executescript(SCHEMA_SQL)
        n_chunks = 0
        for d in docs:
            text = (raw_dir / d["file"]).read_text(encoding="utf-8")
            conn.execute(
                "INSERT INTO documents (doc_id,title,author,work,source_url,license,"
                "release_date,text,n_chars,sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d["doc_id"], d.get("title", ""), d.get("author", ""), d.get("work", ""),
                 d.get("source_url", ""), d.get("license", ""), d.get("release_date", ""),
                 text, len(text), d.get("sha256", "")))
            for ord_, cs, ce in chunk_spans(text):
                conn.execute(
                    "INSERT INTO chunks_fts (doc_id,ord,char_start,char_end,body_tok) "
                    "VALUES (?,?,?,?,?)",
                    (d["doc_id"], ord_, cs, ce, tokenize(text[cs:ce])))
                n_chunks += 1
        conn.commit()
    finally:
        conn.close()

    result = verify(db)
    return {"rebuilt": True, "n_chunks": n_chunks, **result}


def verify(db_path: Path | None = None) -> dict:
    """核对索引与清单是否一致。**构建脚本和自检脚本共用这一份判定。**"""
    db = Path(db_path) if db_path is not None else paths.kb_db()
    conn = sqlite3.connect(db)
    try:
        n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_chunks = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        chars = conn.execute("SELECT COALESCE(SUM(n_chars),0) FROM documents").fetchone()[0]
        empty = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE body_tok = ''").fetchone()[0]
        # 偏移量是否落在正文范围内 —— 越界说明构建期和存储期的坐标算法不一致
        bad = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts c JOIN documents d ON d.doc_id=c.doc_id "
            "WHERE c.char_start < 0 OR c.char_end > d.n_chars OR c.char_start >= c.char_end"
        ).fetchone()[0]
    finally:
        conn.close()
    problems = []
    if empty:
        problems.append(f"{empty} 个块切词后为空")
    if bad:
        problems.append(f"{bad} 个块的偏移量越界或为空区间")
    return {"ok": not problems, "n_docs": n_docs, "n_chunks": n_chunks,
            "total_chars": chars, "problems": problems}


# ---------------------------------------------------------------- 参数归一
def _as_int(value, default: int) -> int:
    """把参数转成整数。`None` 用默认值，其余一律 `int()`。

    **刻意不写 `value or default`。** 那个写法会把 `0` 当成「没传」，
    于是 `top_k=0` 变成 5、`length=0` 变成 800 —— 调用方要的是一个东西，
    拿到的是另一个，而且**没有任何提示**。这类静默替换在数字接口上特别危险：
    调用方看到的返回体完全合法，只是不是它要的。
    """
    if value is None:
        return default
    return int(value)


# ---------------------------------------------------------------- 检索
def search_passages(query: str, top_k: int = DEFAULT_TOP_K,
                    db_path: Path | None = None) -> dict:
    """检索相关段落。**不抛异常**，失败降级成 `ok=False` + `err_code`。"""
    try:
        top_k = max(1, min(_as_int(top_k, DEFAULT_TOP_K), MAX_TOP_K))
    except (TypeError, ValueError):
        return {"ok": False, "err_code": E_BAD_RANGE,
                "detail": f"top_k 必须是整数，收到 {top_k!r}", "hits": []}
    match = build_match_query(query)
    if not match:
        return {"ok": False, "err_code": E_QUERY_SYNTAX,
                "detail": "查询里没有可检索的词（空串或全是标点）", "hits": []}

    try:
        conn = connect(db_path)
    except KBError as e:
        return {"ok": False, "err_code": e.err_code, "detail": e.detail, "hits": []}

    try:
        rows = conn.execute(
            "SELECT chunks_fts.doc_id, documents.title, chunks_fts.char_start, "
            "       chunks_fts.char_end, bm25(chunks_fts) AS score, documents.text "
            "FROM chunks_fts JOIN documents ON documents.doc_id = chunks_fts.doc_id "
            "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (match, top_k)).fetchall()
    except sqlite3.OperationalError as e:
        # 理论上到不了这里（每个词都加过引号），但保留兜底：
        # 万一 FTS5 的语法在将来变了，也只会变成一个可读的错误码，而不是崩掉工具。
        return {"ok": False, "err_code": E_QUERY_SYNTAX, "detail": f"{e}", "hits": []}
    finally:
        conn.close()

    hits = []
    for doc_id, title, cs, ce, score, text in rows:
        hits.append({
            "doc_id": doc_id,
            "title": title,
            # 正文只从 documents.text 现场切片 —— 全仓唯一的坐标来源
            "text": text[cs:ce],
            "char_start": cs,
            "char_end": ce,
            "score": round(float(score), 6),
        })
    return {"ok": True, "query": query, "match_expr": match, "n_hits": len(hits), "hits": hits}


def get_passage(doc_id: str, offset: int = 0, length: int = DEFAULT_PASSAGE_LEN,
                db_path: Path | None = None) -> dict:
    """按 `(doc_id, offset, length)` 取原文片段。**不抛异常**。"""
    try:
        offset = max(0, _as_int(offset, 0))
        length = _as_int(length, DEFAULT_PASSAGE_LEN)
    except (TypeError, ValueError):
        return {"ok": False, "err_code": E_BAD_RANGE,
                "detail": f"offset/length 必须是整数，收到 {offset!r}/{length!r}"}
    if length <= 0:
        # 不把 0 当成「没传」而替换成默认值 —— 调用方要 0 个字符就是 0 个字符，
        # 但「返回 0 个字符」对调用方毫无用处，所以这是一个错误而不是一次空返回。
        return {"ok": False, "err_code": E_BAD_RANGE, "detail": f"length 必须为正数，收到 {length}"}
    length = min(length, MAX_PASSAGE_LEN)

    try:
        conn = connect(db_path)
    except KBError as e:
        return {"ok": False, "err_code": e.err_code, "detail": e.detail}

    try:
        row = conn.execute(
            "SELECT title, author, n_chars, text FROM documents WHERE doc_id = ?",
            (str(doc_id),)).fetchone()
    finally:
        conn.close()

    if row is None:
        return {"ok": False, "err_code": E_NO_SUCH_DOC, "detail": f"没有编号为 {doc_id!r} 的文档"}

    title, author, n_chars, text = row
    text = text[offset:offset + length]
    return {"ok": True, "doc_id": str(doc_id), "title": title, "author": author,
            "offset": offset, "length": len(text), "n_chars": n_chars, "text": text}


def list_documents(db_path: Path | None = None) -> dict:
    """列出知识库里的文档。供 `get_schema` 那一类"先看看有什么"的场景使用。"""
    try:
        conn = connect(db_path)
    except KBError as e:
        return {"ok": False, "err_code": e.err_code, "detail": e.detail, "documents": []}
    try:
        rows = conn.execute(
            "SELECT doc_id, title, author, n_chars FROM documents ORDER BY doc_id").fetchall()
    finally:
        conn.close()
    return {"ok": True, "n_docs": len(rows),
            "documents": [{"doc_id": r[0], "title": r[1], "author": r[2], "n_chars": r[3]}
                          for r in rows]}
