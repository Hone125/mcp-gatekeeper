"""交付物核验的**纯检查**部分：文档说的和仓库里的对不对得上。

## 为什么这些不能写在 `experiments/25_deliverable_check.py` 里

同 `DECISIONS.md` D-20 / D-29：`experiments/` 下的文件名以数字开头，
**以数字开头的模块没法被 import**。写在里面的检查只能靠「跑一遍 25、看它绿不绿」
来测 —— 而这些检查恰恰是最需要单独测的一类：**它们判的是别人，自己错了不会崩，
只会给出一张全绿的表。**

所以分成两半：

- **本模块（纯）**：每个检查都是 `f(root) -> verify_kit.Result`，不写盘、不打印、
  不读环境变量。所以能被 `tests/test_deliverable_kit.py` 拿一个临时目录当家，
  往里面**故意放坏东西**，逐条证明它会红。
- **`experiments/25_deliverable_check.py`（有副作用）**：解析参数、拼报告、落盘、
  把判定换成退出码。

## 检查的口气是「一个不认识这个仓库的人照着文档能不能跑通」

所以查的全是**文档与仓库之间**的关系，不是代码内部对不对：

| id | 查什么 |
|---|---|
| G1 | 数据文件在不在；清单 JSON 可不可解析；清单与文件**双向**一致 |
| G2 | 逐篇 sha256 与清单一致；Chinook 另与 `NOTICE.md` 里手写的那行一致 |
| G3 | `NOTICE.md` 覆盖清单里每一篇的出处（分发公版内容的义务） |
| G4 | 文档里的 `python 某某.py --某某`：脚本在不在、flag 在不在源码里 |
| G5 | README 的工具表与 `auth.TOOL_SCOPES` 双向一致 |
| G6 | README 里的仓库内路径都存在 |
| G7 | 文档引用的 `reports/*` 都存在，或当场说明它还没生成 |
| G8 | `mcp_server/` 的第三方 import 都在 `requirements.txt` 里声明 |

## 一条贯穿的教训：**生成物不参与生成它的那条检查**

`reports/deliverable_check.md` 自己会被 `_docs()` 排除。理由不是好看，
而是一个会失控的回路：报告里「命中的行」一节是**引用**，把引用当成新的声明来判，
报告就会自己触发自己，而且每轮多套一层反引号（实测长过三代）。
红线的覆盖没有缺口 —— `19_verify.py` 的 S1~S9 是全仓范围的，包含这一份。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

from mcp_server import auth
from mcp_server import verify_kit as vk

PASS, FAIL, SKIP, ERROR = vk.PASS, vk.FAIL, vk.SKIP, vk.ERROR

# import 名 → 在 `requirements.txt` 里的**发行包名**。两者不一致的正是最容易漏的：
# `import jwt` 装的是 PyJWT，`import dotenv` 装的是 python-dotenv。
IMPORT_TO_DIST = {"jwt": "PyJWT", "dotenv": "python-dotenv", "mcp": "mcp"}

# 扫描哪些文档。`reports/` 下的是程序生成的，也一并扫 ——
# 报告里写着「重跑命令」的地方，就是这个模块该管的地方。
DOC_GLOBS = ("*.md", "docs/*.md", "reports/*.md")

# 本脚本自己的报告名（相对 root）。见文件头那一段。
SELF_REPORT = "reports/deliverable_check.md"


def _rel(root: Path, p: Path) -> str:
    """仓库根起算的相对路径。

    报告里**一律不许出现绝对路径** —— 那会把作者机器的目录结构带进对外产物，
    别人 clone 下来看到的是一串和他无关的东西（`tests/test_repo_hygiene.py`
    有一条检查专门扫这个）。所以这里宁可退化成一个不好看的相对路径，
    也不写绝对路径。
    """
    try:
        return Path(p).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(p).name


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _paragraphs(text: str) -> list[list[tuple[int, str]]]:
    """按空行切自然段，返回 `[(行号, 该行原文), …]` 的列表。

    用它而不用单行，是因为文档是折行写的：一句话会被折到两三行上。
    按行判会把「（尚未生成）」判到路径的下一行去，报一堆假红（实测踩过）。
    """
    blocks: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip():
            cur.append((i, line))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _docs(root: Path) -> list[Path]:
    out: list[Path] = []
    for pat in DOC_GLOBS:
        out += sorted(root.glob(pat))
    skip = (root / SELF_REPORT).resolve()
    return [p for p in out if p.is_file() and p.resolve() != skip]


def _load_docs(root: Path) -> tuple[list | None, str]:
    """读语料清单。返回 `(docs, 错误说明)`；`docs is None` 表示清单读不出来。

    收成一个函数是为了让「清单坏了」在 G1/G2/G3 里表现**一致**：
    都是 ERROR + 同一句说明，而不是三条检查各抛各的异常。
    清单坏了是 ERROR 不是 FAIL —— 修的人不同（一个是改数据，一个是改清单）。
    """
    manifest = root / "data" / "kb" / "manifest.json"
    if not manifest.is_file():
        return None, "data/kb/manifest.json 不存在"
    try:
        docs = json.loads(_read(manifest))["docs"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return None, f"data/kb/manifest.json 读不出来：{type(e).__name__}: {e}"
    if not isinstance(docs, list) or not docs:
        return None, "data/kb/manifest.json 里没有 docs，或 docs 是空的"
    return docs, ""


def _hits(rows: list[tuple[str, int, str, str]]) -> list[dict]:
    """`(文件, 行号, 一句话问题, 那一行原文)` → 报告用的命中结构。"""
    return [{"file": f, "line": ln, "needle": why, "text": text[:160]}
            for f, ln, why, text in rows]


def _res(cid: str, title: str, caliber: str, bad: list, ok_detail: str,
         bad_detail: str) -> vk.Result:
    return vk.Result(cid, title, caliber, PASS if not bad else FAIL,
                     ok_detail if not bad else bad_detail, _hits(bad) if bad else [])


# ---------------------------------------------------------------- 数据
def check_data_files(root: Path) -> vk.Result:
    """数据文件必须在。**随仓库分发**是这个仓库的承诺：clone 下来不联网就要能跑。"""
    bad: list[tuple[str, int, str, str]] = []
    need = ["data/chinook/Chinook_Sqlite.sql", "data/kb/manifest.json"]
    for rel in need:
        if not (root / rel).is_file():
            bad.append((rel, 0, "文件不存在", ""))
    docs, err = _load_docs(root)
    if err:
        return vk.Result("G1", "数据文件齐备且清单可解析",
                         "清单 JSON 可解析；清单里的每篇都要有文件，"
                         "文件也要都在清单里（双向，防止有东西混进来没交代出处）",
                         ERROR, err)
    n_docs = len(docs)
    for d in docs:
        if not (root / "data" / "kb" / "raw" / d["file"]).is_file():
            bad.append((f"data/kb/raw/{d['file']}", 0, "清单里有、文件不在", ""))
    extra = sorted({p.name for p in (root / "data" / "kb" / "raw").glob("*.txt")}
                   - {d["file"] for d in docs})
    for name in extra:
        bad.append((f"data/kb/raw/{name}", 0, "文件在、清单里没有（来源不明）", ""))
    return _res("G1", "数据文件齐备且清单可解析",
                "清单 JSON 可解析；清单里的每篇都要有文件，"
                "文件也要都在清单里（双向，防止有东西混进来没交代出处）",
                bad, f"{len(need)} 个数据文件 + {n_docs} 篇语料，双向一致",
                f"{len(bad)} 处不一致")


def check_checksums(root: Path) -> vk.Result:
    """逐篇 sha256 与清单一致 —— 这是「你拿到的东西就是我描述的东西」的**唯一**机械证据。

    数据是随仓库分发的第三方内容：一旦被换掉、被截断、被谁顺手编辑过，
    所有靠它跑出来的数字就都不作数了。校验和是这条链上唯一能自证的一环。
    """
    docs, err = _load_docs(root)
    if err:
        return vk.Result("G2", "数据校验和与文档声明一致",
                         "逐篇重算 sha256 与清单比；Chinook 另与 `NOTICE.md` 里"
                         "手写的那一行比（manifest 管不到那一行）",
                         ERROR, err)
    bad: list[tuple[str, int, str, str]] = []
    n = 0
    for d in docs:
        p = root / "data" / "kb" / "raw" / d["file"]
        if not p.is_file():
            continue
        n += 1
        got = _sha256(p)
        if got != d.get("sha256"):
            bad.append((f"data/kb/raw/{d['file']}", 0,
                        f"清单记的是 {str(d.get('sha256'))[:12]}…，实测 {got[:12]}…", ""))

    # `NOTICE.md` 里也写了一个 Chinook 的校验和。它是**手写进文档的声明**，
    # 与文件对不对得上必须单独验 —— manifest 管不到它。
    sql = root / "data" / "chinook" / "Chinook_Sqlite.sql"
    notice = _read(root / "NOTICE.md")
    if sql.is_file() and notice:
        m = re.search(r"\| sha256 \| `([0-9a-f]{64})` \|", notice)
        if not m:
            bad.append(("NOTICE.md", 0, "没找到 Chinook 的 sha256 声明行", ""))
        elif m.group(1) != _sha256(sql):
            bad.append(("NOTICE.md", 0,
                        f"声明的 sha256 {m.group(1)[:12]}… 与文件实测 {_sha256(sql)[:12]}… 不一致",
                        ""))
    return _res("G2", "数据校验和与文档声明一致",
                "逐篇重算 sha256 与 manifest 比；Chinook 另与 `NOTICE.md` 里"
                "手写的那一行比（manifest 管不到那一行）",
                bad, f"{n} 篇语料 + 1 个数据库，校验和全部吻合",
                f"{len(bad)} 处对不上")


def check_notice_coverage(root: Path) -> vk.Result:
    """`NOTICE.md` 要覆盖每一篇的出处。**这是分发公版内容的义务，不是礼貌。**

    做法：清单里每一篇的 `source_url` 都必须在 `NOTICE.md` 里出现。
    按链接逐条比，不按「有没有写一段话介绍来源」这种模糊印象。
    """
    bad: list[tuple[str, int, str, str]] = []
    notice = _read(root / "NOTICE.md")
    if not notice:
        return vk.Result("G3", "NOTICE 覆盖逐篇出处", "逐篇比对清单里的 source_url",
                         FAIL, "NOTICE.md 不存在或读不出来")
    docs, err = _load_docs(root)
    if err:
        return vk.Result("G3", "NOTICE 覆盖逐篇出处", "逐篇比对清单里的 source_url",
                         ERROR, err)
    n = 0
    for d in docs:
        n += 1
        if d.get("source_url") and d["source_url"] not in notice:
            bad.append(("NOTICE.md", 0, f"缺出处：{d.get('doc_id')}", d["source_url"]))
    for must, why in (("Chinook", "示例数据库的出处"),
                      ("Public domain", "公版声明"),
                      ("MIT", "代码许可")):
        if must not in notice:
            bad.append(("NOTICE.md", 0, f"缺{why}", must))
    return _res("G3", "NOTICE 覆盖逐篇出处",
                "清单里每一篇的 source_url 都要在 `NOTICE.md` 里找得到；"
                "另外检查示例库出处、公版声明、代码许可三句话在不在",
                bad, f"{n} 篇出处全部列明", f"{len(bad)} 处缺失")


# ---------------------------------------------------------------- 文档
_CMD_RE = re.compile(r"\bpython(?:\.exe)?\s+([^\s`\"']+\.py)([^\n`\"']*)")


def check_doc_commands(root: Path) -> vk.Result:
    """文档里的每一条 `python 某某.py --某某` 都要真的成立。

    ## 这条检查有来历

    早先的文档里写着「把 `.env.example` 复制成 `.env` 再填值」，
    而**当时没有任何一处代码读过 `.env`** —— 照着做的人填完值，
    程序报的是「凭据未配置」，然后开始怀疑自己。文档与代码对不上就是这么伤人的：
    代码那边全绿，文档那边每一个字都在指着不存在的东西。

    所以这里把「脚本存在」和「flag 存在」都查一遍。查的是**源码文本里有没有这个字符串**，
    不是把脚本 import 进来问 argparse —— `experiments/` 下的文件名以数字开头，
    import 不进来（见 `DECISIONS.md` D-20）。
    """
    bad: list[tuple[str, int, str, str]] = []
    n_cmds = 0
    for doc in _docs(root):
        text = _read(doc)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in _CMD_RE.finditer(line):
                script, rest = m.group(1), m.group(2)
                if script.startswith("-"):        # `python -m pytest` 之类
                    continue
                if any(ch in script for ch in "<>…*"):
                    # 带占位符的是**给人看的模板**（`python <脚本名>.py`），
                    # 不是一条可执行的指令 —— 它没有承诺那个文件存在。
                    continue
                n_cmds += 1
                sp = root / script
                if not sp.is_file():
                    bad.append((_rel(root, doc), i, f"脚本不存在：{script}", line.strip()))
                    continue
                src = _read(sp)
                for flag in re.findall(r"--[A-Za-z][A-Za-z0-9-]+", rest):
                    if flag not in src:
                        bad.append((_rel(root, doc), i,
                                    f"{script} 里没有 {flag} 这个参数", line.strip()))
    return _res("G4", "文档里的命令都成立",
                "扫描全部 `.md` 里的 `python 某某.py …`：脚本要在、"
                "用到的 `--flag` 要在源码文本里出现",
                bad, f"{n_cmds} 条命令，脚本与参数全部对得上",
                f"{len(bad)} 处对不上")


# ★ 括号的位置很讲究：scope 要**整体**捕获（`(?:db|kb):[a-z]+`）。
# 第一版写成 `(db|kb):[a-z]+`，于是只捕到 `db`，六行工具全被判成
# 「scope 写的是 db，代码里是 db:read」—— 这是单元测试逼出来的，见
# `tests/test_deliverable_kit.py`。
_TOOL_ROW_RE = re.compile(r"^\|\s*`?([a-z][a-z0-9_]*)`?\s*\|\s*`?((?:db|kb):[a-z]+)`?\s*\|")


def check_readme_tools(root: Path) -> vk.Result:
    """README 里那张工具表必须和 `auth.TOOL_SCOPES` 是同一批名字（双向）。

    单向查漏（表里少写一个）是不够的：**表里多写一个**同样有害 ——
    对方会去调一个不存在的工具，然后怀疑自己参数写错了。
    """
    from mcp_server import auth

    readme = root / "README.md"
    if not readme.is_file():
        return vk.Result("G5", "README 工具表与 TOOL_SCOPES 一致",
                         "双向比对：README 表里的名字集合 == `auth.TOOL_SCOPES` 的键集合",
                         SKIP, "README.md 尚未创建（阶段 6）")
    bad: list[tuple[str, int, str, str]] = []
    listed: dict[str, str] = {}
    for i, line in enumerate(_read(readme).splitlines(), 1):
        m = _TOOL_ROW_RE.match(line)
        if m:
            listed[m.group(1)] = m.group(2)
    if not listed:
        return vk.Result("G5", "README 工具表与 TOOL_SCOPES 一致",
                         "双向比对：README 表里的名字集合 == `auth.TOOL_SCOPES` 的键集合",
                         FAIL, "README 里没找到工具表（表格行的格式：`| 工具名 | db:read | …`）")
    for name, scope in listed.items():
        if name not in auth.TOOL_SCOPES:
            bad.append(("README.md", 0, f"表里有、代码里没有：{name}", ""))
        elif auth.TOOL_SCOPES[name] != scope:
            bad.append(("README.md", 0,
                        f"{name} 的 scope 写的是 {scope}，代码里是 {auth.TOOL_SCOPES[name]}", ""))
    for name in auth.TOOL_SCOPES:
        if name not in listed:
            bad.append(("README.md", 0, f"代码里有、表里没写：{name}", ""))
    return _res("G5", "README 工具表与 TOOL_SCOPES 一致",
                "双向比对：README 表里的名字集合 == `auth.TOOL_SCOPES` 的键集合，"
                "且每行的 scope 也要一致",
                bad, f"{len(listed)} 个工具，名字与 scope 全部一致",
                f"{len(bad)} 处不一致")


_PATH_RE = re.compile(r"`((?:mcp_server|experiments|tests|tools|data|reports|docs)/"
                      r"[A-Za-z0-9_./一-鿿-]*)`")
_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def check_readme_paths(root: Path) -> vk.Result:
    """README 里点名的仓库内路径都要存在。

    两类都查：**反引号里的路径**（`data/kb/raw/2090.txt`）和**markdown 链接的目标**
    （`](./docs/design.md)`）。外链、锚点、带通配符的、带占位符的一律跳过 ——
    那些本来就不是「必须存在的文件」。
    """
    readme = root / "README.md"
    if not readme.is_file():
        return vk.Result("G6", "README 提到的仓库内路径都存在",
                         "只查仓库内的相对路径；外链/锚点/通配符/占位符跳过",
                         SKIP, "README.md 尚未创建（阶段 6）")
    bad: list[tuple[str, int, str, str]] = []
    n = 0
    for i, line in enumerate(_read(readme).splitlines(), 1):
        cands = _PATH_RE.findall(line) + _LINK_RE.findall(line)
        for c in cands:
            c = c.strip()
            if c.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if any(ch in c for ch in "*?<>"):        # 通配符 / 占位符
                continue
            if not (c.startswith(("mcp_server/", "experiments/", "tests/", "tools/",
                                  "data/", "reports/", "docs/")) or c.endswith(".md")):
                continue
            n += 1
            if not (root / c).exists():
                bad.append(("README.md", i, f"路径不存在：{c}", line.strip()))
    return _res("G6", "README 提到的仓库内路径都存在",
                "只查仓库内的相对路径；外链/锚点/通配符/占位符跳过",
                bad, f"{n} 个仓库内路径，全部存在", f"{len(bad)} 个不存在")


def check_report_refs(root: Path) -> vk.Result:
    """文档里提到的 `reports/xxx` 要真的在 —— **除非同一行就说了它还没生成**。

    ## 为什么允许「说了没生成」这一条例外

    文档里确实会正当地提到还不存在的产物：「这份报告要模型凭据，尚未生成」、
    「阶段 6 才写的自检报告」。把这些句子一并判红，结果是**没人再敢提任何
    还没生成的东西**，而读者反而更糊涂。

    所以规则是：**指一个不存在的文件可以，但必须当场说明**。
    判据是**同一个自然段**里出现「它现在不在」这类说法之一
    （`未生成 / 尚未 / 未完成 / 阶段 6 / 不存在 / 被创建 / 生成后 / 将由`）。

    为什么按自然段而不是按行：文档是**折行**写的。实测踩过 —— 路径在行末、
    「（阶段 6 产物，尚未生成）」被折到了下一行，按行判就报了假红。
    按自然段判与折行无关，而且要求仍然是「就近交代」，不是「全文随便哪里提一句」。

    「不存在」也在判据里，理由很直接：这句话本身就是最明确的那种交代。
    而它对**真的存在**的文件根本不起作用 —— 那种情况在上一行就 `continue` 了。
    """
    PENDING = ("未生成", "尚未", "未完成", "阶段 6", "不存在", "被创建",
               "生成后", "将由")
    ref_re = re.compile(r"reports/[A-Za-z0-9_.-]+\.(?:md|json|jsonl)(?![A-Za-z0-9])")
    bad: list[tuple[str, int, str, str]] = []
    n = 0
    n_ok_pending = 0
    seen: set[str] = set()
    for doc in _docs(root):
        for block in _paragraphs(_read(doc)):
            blob = "\n".join(t for _, t in block)
            pending = any(w in blob for w in PENDING)
            for i, line in block:
                # 边界 `(?![A-Za-z0-9])`：否则 `usage_ledger.jsonl` 会被
                # 截成 `usage_ledger.json` 而报一个假的不存在。
                for m in ref_re.finditer(line):
                    ref = m.group(0)
                    if ref in seen:
                        continue
                    seen.add(ref)
                    n += 1
                    if (root / ref).is_file():
                        continue
                    if pending:
                        n_ok_pending += 1
                        continue
                    bad.append((_rel(root, doc), i, f"报告不存在，也没说它还没生成：{ref}",
                                line.strip()))
    return _res("G7", "文档引用的报告都存在（或当场说明未生成）",
                "全仓文档里出现的 `reports/*.md|json|jsonl` 逐条查存在性；"
                "不存在的，若**同一个自然段**里写了「未生成 / 尚未 / 未完成 / "
                "阶段 6 / 不存在 / 被创建 / 生成后 / 将由」之一则算已交代",
                bad, f"{n} 处引用：{n - n_ok_pending} 处文件存在、"
                     f"{n_ok_pending} 处当场说明未生成",
                f"{len(bad)} 处指向不存在的文件且没交代")


# ---------------------------------------------------------------- 依赖
def _imported_top_level(py: Path) -> set[str]:
    try:
        tree = ast.parse(_read(py))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module.split(".")[0])
    return out


def check_requirements(root: Path) -> vk.Result:
    """`mcp_server/` 里 import 的第三方包都要在 `requirements.txt` 里声明。

    这条防的是「在我机器上能跑」：装过的东西多了，漏声明一个照样能跑，
    别人 clone 下来就 ImportError，而且报错位置在代码深处、不像环境问题。

    ★ 反方向（声明了但没人 import）**只记录、不判失败** ——
    `pytest` 是只在测试里用的，按「没人 import」把它判红就错了。
    这个方向的真正判据不在这份文件里，硬编一个只会得到一条假检查。
    """
    stdlib = set(sys.stdlib_module_names)
    req = _read(root / "requirements.txt")
    if not req:
        return vk.Result("G8", "requirements.txt 覆盖代码里的第三方 import",
                         "解析 `mcp_server/*.py` 的顶层 import，"
                         "去掉标准库与本包自身，其余必须能在 requirements.txt 里找到声明",
                         FAIL, "requirements.txt 不存在或读不出来")
    declared = {line.split("#", 1)[0].split("=")[0].split(">")[0].strip().lower()
                for line in req.splitlines() if "=" in line}
    used: set[str] = set()
    for p in sorted((root / "mcp_server").glob("*.py")):
        used |= _imported_top_level(p)
    third = sorted(x for x in used
                   if x and x != "mcp_server" and x not in stdlib)
    bad: list[tuple[str, int, str, str]] = []
    for pkg in third:
        dist = IMPORT_TO_DIST.get(pkg, pkg).lower()
        if dist not in declared:
            bad.append(("requirements.txt", 0, f"缺声明：import {pkg}（发行包名 {dist}）", ""))
    unused = sorted(d for d in declared
                    if d and d not in {IMPORT_TO_DIST.get(x, x).lower() for x in used})
    return _res("G8", "requirements.txt 覆盖代码里的第三方 import",
                "解析 `mcp_server/*.py` 的顶层 import，去掉标准库与本包自身；"
                "`import jwt` → PyJWT、`import dotenv` → python-dotenv 这两个"
                "名字不一致的单独映射。反方向（声明了没用到）只记录不判失败",
                bad, f"{len(third)} 个第三方包全部已声明"
                     + (f"；未被 import 的声明：{'、'.join(unused)}" if unused else ""),
                f"{len(bad)} 个包没声明")


CHECKS = (check_data_files, check_checksums, check_notice_coverage,
          check_doc_commands, check_readme_tools, check_readme_paths,
          check_report_refs, check_requirements)


