"""知识库检索的边界与敌意输入。

## 这个文件在防什么

FTS5 的查询是一门**有自己的语法**的小语言。把用户输入直接拼进去，就等于把
「一个中文句子的标点」翻译成了「一个语法错误」，然后那条错误会一路冒到用户面前，
而它的成因（他打了一个英文左括号）从错误信息里完全看不出来。

`_quote_token` 的解法是给每个词加上双引号，让它退化成普通字符串字面量。
**下面这张 44 条的敌意输入表就是它的验收标准** —— 只要有一条非空表达式让
SQLite 抛 `OperationalError`，说明哪里漏了。

（这张表最早是一个临时探针，跑出来的结论是：报错的 9 条**全部**是空表达式，
非空表达式一条都没炸。所以「引号包裹」是有效的，而唯一还需要处理的情形就是
「一个可检索的词都没有」—— 那由 `search_passages` 在碰 SQLite 之前就返回
`KB_QUERY_SYNTAX`。）
"""
from __future__ import annotations

import sqlite3

import pytest

from mcp_server import kb_tools

# 44 条敌意输入。它们覆盖：空与纯空白、FTS5 的每一个元字符单独出现、
# 布尔/邻近运算符单独出现、中英混合、以及常见的真实用户输入（`C++`、`1+1`、`say "hi"`）。
HOSTILE = [
    "", "  ", "(", ")", '"', "()", "---", "。", "_", "__", "a_b", "a-b", "*", "^*",
    "数据库 AND", "数据库 OR 茶", "NOT 茶", "茶 NEAR 水", 'say "hi"', "a:b", "^茶",
    "{茶}", "[茶]", "<茶>", "~茶", "\\", "茶\\", "1+1", "C++", "!!!", "??", "…",
    "%", "$", "@", "#", "&", "/", "|", "=", "茶 AND", "OR", "AND", "NOT",
]

# 这些输入里一个可检索的词都没有，必须被前置挡下（而不是丢给 SQLite 炸）
NO_WORD = ["", "  ", "(", ")", '"', "()", "---", "。", "_", "__", "*", "^*",
           "\\", "!!!", "??", "…", "%", "$", "@", "#", "&", "/", "|", "="]


@pytest.fixture
def scratch_fts():
    """一个内存里的 FTS5 表，用真实的 tokenize 参数，供上面那张表做执行测试。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5("
                 "body_tok, tokenize='unicode61 remove_diacritics 2')")
    for body in ["用兵之道 地形 敵情 知彼知己", "茶 神農氏 山水 江水 井水"]:
        conn.execute("INSERT INTO t (body_tok) VALUES (?)", (body,))
    yield conn
    conn.close()


# ---------------------------------------------------------------- 表达式构造
def test_no_nonempty_expression_is_a_syntax_error(scratch_fts):
    """★ 核心断言：**只要表达式非空，就一定不是语法错误。**

    空表达式单独算 —— 它由 `search_passages` 前置拦截，本就不该到达 SQLite。
    """
    nonempty = 0
    for q in HOSTILE:
        expr = kb_tools.build_match_query(q)
        if not expr:
            continue
        nonempty += 1
        try:
            scratch_fts.execute("SELECT COUNT(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()
        except sqlite3.OperationalError as e:      # pragma: no cover - 失败时才有意义
            pytest.fail(f"输入 {q!r} 编出的表达式 {expr!r} 让 FTS5 报语法错误：{e}")
    assert nonempty >= 15, f"有效的敌意输入太少（{nonempty} 条），这个测试没覆盖到东西"


def test_empty_expression_is_exactly_when_there_is_no_word():
    """空表达式 ⟺ 输入里没有可检索的词。两边必须严格对应，不能多也不能少。"""
    for q in HOSTILE:
        expr = kb_tools.build_match_query(q)
        if q in NO_WORD:
            assert expr == "", f"{q!r} 应当被判为「没有可检索的词」，却编出了 {expr!r}"
        else:
            assert expr != "", f"{q!r} 含真实词汇，不该被判为空"


def test_boolean_and_near_operators_become_literal_words():
    """`AND` / `OR` / `NOT` / `NEAR` 被引号包住后退化成普通词，不再是运算符。

    这是「加引号」这个做法的直接证据：一个查询里同时出现「数据库」和「AND」，
    结果应当是「匹配数据库这个词」而不是「语法错误」或「布尔运算」。
    """
    assert kb_tools.build_match_query("数据库 AND") == '"数据库" OR "AND"'
    assert kb_tools.build_match_query("茶 NEAR 水") == '"茶" OR "NEAR" OR "水"'
    assert kb_tools.build_match_query("say \"hi\"") == '"say" OR "hi"'


def test_inner_double_quote_is_doubled(scratch_fts):
    """FTS5 里字符串字面量的转义写法是双写。内部带引号的词必须被转义，不能破出来。

    **这里直接打 `_quote_token`，不经过 `build_match_query`。** 原因是实测过：
    jieba 会把 `茶"AND"水` 里的引号切成独立的词，随后被「无实义字符」过滤掉 ——
    也就是说走公开接口永远走不到转义那条分支。用公开接口写这个测试，
    会得到一个「绿着的、但什么都没测到」的假覆盖，比没有测试更危险。
    """
    expr = kb_tools._quote_token('茶"AND"水')
    assert expr == '"茶""AND""水"', expr
    # 转义对不对不看字符串长相，看 FTS5 认不认
    scratch_fts.execute("SELECT COUNT(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()


# ---------------------------------------------------------------- 检索接口
def test_empty_and_punctuation_queries_report_a_clear_code(tiny_kb):
    """空/纯标点查询返回**可读的错误码**，而不是 `OperationalError`。"""
    for q in NO_WORD:
        r = kb_tools.search_passages(q, db_path=tiny_kb)
        assert r["ok"] is False, f"{q!r} 应当被拒绝，实际返回 {r}"
        assert r["err_code"] == kb_tools.E_QUERY_SYNTAX, f"{q!r} -> {r}"
        assert r["hits"] == []


def test_search_never_raises_on_hostile_input(tiny_kb):
    """44 条敌意输入全部走一遍公开接口，**一条都不许抛异常**。"""
    known = {kb_tools.E_QUERY_SYNTAX, kb_tools.E_NO_DB}
    for q in HOSTILE:
        r = kb_tools.search_passages(q, db_path=tiny_kb)
        assert isinstance(r, dict), f"{q!r} 返回了非字典：{type(r)}"
        assert "ok" in r, f"{q!r} 的返回体没有 ok 字段：{r}"
        if not r["ok"]:
            assert r["err_code"] in known, f"{q!r} 报了未预期的错误码：{r}"


def test_search_finds_the_right_document_and_not_the_other(tiny_kb):
    """两篇语料的用词刻意不重叠，所以「命中了谁」是可以严格断言的。"""
    r1 = kb_tools.search_passages("地形", db_path=tiny_kb)
    assert r1["ok"] and r1["n_hits"] >= 1, r1
    assert {h["doc_id"] for h in r1["hits"]} == {"d1"}, r1["hits"]

    r2 = kb_tools.search_passages("神農氏", db_path=tiny_kb)
    assert r2["ok"] and r2["n_hits"] >= 1, r2
    assert {h["doc_id"] for h in r2["hits"]} == {"d2"}, r2["hits"]


def test_hit_text_is_sliced_from_the_stored_document(tiny_kb):
    """★ 命中块返回的正文，必须**逐字符等于**原文切片。

    这条如果挂了，表现是「检索返回了一段通顺但错位的文字」—— 不报错，只是内容错了。
    """
    conn = sqlite3.connect(tiny_kb)
    try:
        texts = {r[0]: r[1] for r in conn.execute("SELECT doc_id, text FROM documents")}
    finally:
        conn.close()

    r = kb_tools.search_passages("用兵 地形 茶 水", top_k=20, db_path=tiny_kb)
    assert r["ok"] and r["n_hits"] > 0, r
    for h in r["hits"]:
        assert h["text"] == texts[h["doc_id"]][h["char_start"]:h["char_end"]], h


@pytest.mark.parametrize("top_k,expected_max", [(1, 1), (2, 2), (999, kb_tools.MAX_TOP_K),
                                                (0, 1), (-5, 1)])
def test_top_k_is_clamped(tiny_kb, top_k, expected_max):
    r = kb_tools.search_passages("用兵 地形 茶 水", top_k=top_k, db_path=tiny_kb)
    assert r["ok"], r
    assert len(r["hits"]) <= expected_max, r


def test_missing_index_reports_no_db_not_a_crash(tmp_path):
    """索引还没建时报 `KB_NO_DB` 并说清楚该跑哪个脚本 —— 不是抛异常。"""
    r = kb_tools.search_passages("茶", db_path=tmp_path / "nope.db")
    assert r["ok"] is False
    assert r["err_code"] == kb_tools.E_NO_DB
    assert "02_build_kb" in r["detail"], r


# ---------------------------------------------------------------- 取原文
def test_get_passage_rejects_bad_ranges(tiny_kb):
    assert kb_tools.get_passage("d1", offset="甲", db_path=tiny_kb)["err_code"] == kb_tools.E_BAD_RANGE
    assert kb_tools.get_passage("d1", length=0, db_path=tiny_kb)["err_code"] == kb_tools.E_BAD_RANGE
    assert kb_tools.get_passage("d1", length=-3, db_path=tiny_kb)["err_code"] == kb_tools.E_BAD_RANGE


def test_get_passage_on_unknown_doc_is_a_clear_code(tiny_kb):
    r = kb_tools.get_passage("没有这篇", db_path=tiny_kb)
    assert r["ok"] is False and r["err_code"] == kb_tools.E_NO_SUCH_DOC


def test_list_documents_lists_both_fixture_docs(tiny_kb):
    r = kb_tools.list_documents(db_path=tiny_kb)
    assert r["ok"] and r["n_docs"] == 2
    assert {d["doc_id"] for d in r["documents"]} == {"d1", "d2"}
