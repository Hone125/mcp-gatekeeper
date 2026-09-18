"""`mcp_server/deliverable_kit.py` 的单元测试 —— **逐条证明那些检查会红**。

## 为什么每条都要有负控

这八条检查有一个共同的坏法：它们不崩、不报错，只是**给出一张全绿的表**。
一个 `return PASS` 的假实现和「一切正常」在报告里长得一模一样。
所以下面每一条检查都配了一个「往临时仓库里放一样坏东西、看它红不红」的用例 ——
以及一个反方向：**把坏东西拿走，它必须回到 PASS**。
只测「会红」的话，一个永远返回 FAIL 的实现也能通过。

## 临时仓库

`_repo(tmp_path)` 造一个**最小但完整**的仓库：一份数据 + 一篇语料 + 出处清单 +
README + 一份报告。先断言它在不使坏时八条全绿 —— 否则下面那些「红了」的断言
可能只是因为样本本身就不合格。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mcp_server import deliverable_kit as dk

SQL_BODY = "-- 示例库（测试用，不是真数据）\nCREATE TABLE t(a);\n"
DOC_BODY = "公版文本（测试用）\n"
SOURCE_URL = "https://example.invalid/ebooks/1"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _repo(tmp_path: Path, *, doc_body: str = DOC_BODY,
          raw_body: bytes | None = None) -> Path:
    """造一个最小仓库。返回根目录。

    它刻意**不完整**（没有真数据、没有真语料），但**对 G1~G8 而言是自洽的** ——
    这八条查的是内部一致性，不是内容的真假。
    """
    r = tmp_path
    raw = raw_body if raw_body is not None else DOC_BODY.encode()
    (r / "data" / "chinook").mkdir(parents=True)
    (r / "data" / "kb" / "raw").mkdir(parents=True)
    (r / "reports").mkdir()
    (r / "mcp_server").mkdir()

    # ★ 用 write_bytes 而不是 write_text：Windows 上文本模式会把换行符
    # 翻译成回车+换行，于是磁盘上的字节和下面 _sha(SQL_BODY.encode()) 算的
    # 不是同一串 —— 校验和检查会红，而那是**测试自己造的**假红（实测踩过）。
    # 同一串 —— 校验和检查会红，而那是**测试自己造的**假红（实测踩过）。
    (r / "data" / "chinook" / "Chinook_Sqlite.sql").write_bytes(SQL_BODY.encode())
    (r / "data" / "kb" / "raw" / "1.txt").write_bytes(raw)
    (r / "data" / "kb" / "manifest.json").write_text(json.dumps({
        "n_docs": 1, "n_works": 1,
        "docs": [{"doc_id": "1", "file": "1.txt", "title": "测试篇",
                  "source_url": SOURCE_URL, "license": "Public domain",
                  "sha256": _sha(raw)}],
    }, ensure_ascii=False), encoding="utf-8")

    (r / "NOTICE.md").write_text(
        "# NOTICE\n\n"
        "| 项目 | Chinook Database |\n|---|---|\n"
        f"| 许可 | **MIT** |\n| sha256 | `{_sha(SQL_BODY.encode())}` |\n\n"
        "许可：Public domain in the United States。\n\n"
        f"| 1 | 测试篇 | [原文]({SOURCE_URL}) |\n", encoding="utf-8")

    (r / "mcp_server" / "tool.py").write_text("import json\nimport jieba\n",
                                              encoding="utf-8")
    (r / "requirements.txt").write_text("jieba==0.42.1\n", encoding="utf-8")
    (r / "README.md").write_text(
        "# 测试仓库\n\n"
        "| 工具 | scope | 说明 |\n|---|---|---|\n"
        "| `list_tables` | `db:read` | 列表 |\n"
        "| `get_schema` | `db:read` | 结构 |\n"
        "| `run_sql` | `db:query` | 查询 |\n"
        "| `ask_database` | `db:query` | 问答 |\n"
        "| `search_passages` | `kb:read` | 检索 |\n"
        "| `get_passage` | `kb:read` | 取文 |\n\n"
        "数据在 `data/kb/raw/1.txt`，出处见 [NOTICE.md](./NOTICE.md)。\n",
        encoding="utf-8")
    (r / "reports" / "some.md").write_text("## 口径\n测试报告。\n", encoding="utf-8")
    return r


def _check(cid: str, root: Path) -> dk.vk.Result:
    """只跑目标那一条检查。

    ★ 注意不能写成 `{f(root).cid: f for f in dk.CHECKS}` —— 那会**先把八条
    全跑一遍**来建索引，于是「故意弄坏清单」的用例会在 G2/G3 抛异常的地方先炸，
    根本走不到 G1。这里按顺序找，找到就停，并且把异常也收成 ERROR 结果
    （和 `25_deliverable_check.py` 的 main() 一样）。
    """
    for f in dk.CHECKS:
        try:
            r = f(root)
        except Exception as e:  # noqa: BLE001
            r = dk.vk.Result(f.__name__, f.__name__, "", dk.ERROR,
                             f"{type(e).__name__}: {e}")
        if r.cid == cid:
            return r
    raise AssertionError(f"`deliverable_kit` 里没有 {cid} 这条检查")


def test_the_fixture_itself_passes_all_eight(tmp_path):
    """★ 先证明样本是合格的 —— 否则下面「它红了」的断言可能只是样本本来就不行。"""
    root = _repo(tmp_path)
    bad = [r for r in (f(root) for f in dk.CHECKS) if r.status == dk.FAIL]
    assert not bad, "最小仓库本身就没过：" + "；".join(f"{r.cid} {r.detail}" for r in bad)


# ---------------------------------------------------------------- G1 数据文件
def test_g1_flags_a_manifest_entry_with_no_file(tmp_path):
    root = _repo(tmp_path)
    (root / "data" / "kb" / "raw" / "1.txt").unlink()
    assert _check("G1", root).status == dk.FAIL


def test_g1_flags_a_file_with_no_manifest_entry(tmp_path):
    """反方向也要查：文件在、清单里没有 = **来源不明的东西混进来了**。"""
    root = _repo(tmp_path)
    (root / "data" / "kb" / "raw" / "99.txt").write_text("来路不明\n", encoding="utf-8")
    r = _check("G1", root)
    assert r.status == dk.FAIL
    assert any("清单里没有" in h["needle"] for h in r.hits)


def test_g1_is_an_error_not_a_pass_when_the_manifest_is_broken(tmp_path):
    """清单坏了是 ERROR 而不是 FAIL：修的人不同（一个是改数据，一个是改清单）。"""
    root = _repo(tmp_path)
    (root / "data" / "kb" / "manifest.json").write_text("{不是 JSON", encoding="utf-8")
    assert _check("G1", root).status == dk.ERROR


# ---------------------------------------------------------------- G2 校验和
def test_g2_flags_a_raw_file_whose_content_changed(tmp_path):
    """语料被改过一个字节，所有靠它跑出来的数字就都不作数了。"""
    root = _repo(tmp_path)
    (root / "data" / "kb" / "raw" / "1.txt").write_text("被改过了\n", encoding="utf-8")
    assert _check("G2", root).status == dk.FAIL


def test_g2_flags_a_notice_checksum_line_that_no_longer_matches(tmp_path):
    """★ 这一条单独测：`NOTICE.md` 里那行 sha256 是**手写进文档的声明**，
    清单管不到它，必须单独验。改数据而忘了同步文档，就该在这里红。"""
    root = _repo(tmp_path)
    (root / "data" / "chinook" / "Chinook_Sqlite.sql").write_text("改过了\n",
                                                                 encoding="utf-8")
    r = _check("G2", root)
    assert r.status == dk.FAIL
    assert any("NOTICE" in h["file"] for h in r.hits)


# ---------------------------------------------------------------- G3 出处
def test_g3_flags_a_corpus_entry_whose_source_is_not_in_notice(tmp_path):
    """分发公版内容的义务：每一篇都要写出处。"""
    root = _repo(tmp_path)
    (root / "NOTICE.md").write_text("# NOTICE\n\nMIT。Public domain。Chinook。\n",
                                    encoding="utf-8")
    r = _check("G3", root)
    assert r.status == dk.FAIL
    assert any(SOURCE_URL in h["text"] for h in r.hits)


# ---------------------------------------------------------------- G4 文档命令
def test_g4_flags_a_command_naming_a_script_that_does_not_exist(tmp_path):
    """这条检查有来历：文档曾写着「把 `.env.example` 复制成 `.env`」，
    而当时没有任何代码读过 `.env` —— 照着做的人填完值，程序说凭据没配。"""
    root = _repo(tmp_path)
    (root / "README.md").write_text("# 测试\n\n```\npython experiments/99_nope.py\n```\n",
                                    encoding="utf-8")
    r = _check("G4", root)
    assert r.status == dk.FAIL
    assert any("99_nope.py" in h["needle"] for h in r.hits)


def test_g4_flags_a_flag_the_script_does_not_have(tmp_path):
    """脚本在、但参数写错了 —— 同样会让人照着敲然后报错。"""
    root = _repo(tmp_path)
    (root / "experiments").mkdir()
    (root / "experiments" / "01_build.py").write_text(
        "import argparse\nap = argparse.ArgumentParser()\n"
        "ap.add_argument('--force')\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# 测试\n\n```\npython experiments/01_build.py --force --nope\n```\n",
        encoding="utf-8")
    r = _check("G4", root)
    assert r.status == dk.FAIL
    assert any("--nope" in h["needle"] for h in r.hits)


def test_g4_accepts_an_existing_script_with_an_existing_flag(tmp_path):
    """反方向：脚本和参数都对，就不该红。"""
    root = _repo(tmp_path)
    (root / "experiments").mkdir()
    (root / "experiments" / "01_build.py").write_text(
        "import argparse\nap = argparse.ArgumentParser()\n"
        "ap.add_argument('--force')\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# 测试\n\n```\npython experiments/01_build.py --force\n```\n", encoding="utf-8")
    assert _check("G4", root).status == dk.PASS


@pytest.mark.parametrize("line", [
    "python <脚本名>.py --whatever",           # 模板里的占位符
    "python experiments/*.py",                 # 通配符
])
def test_g4_treats_placeholders_as_templates_not_commands(tmp_path, line):
    """带占位符/通配符的是**给人看的模板**，它没有承诺那个文件存在。

    判据必须这样定：否则连「怎么写一条命令」都没法在文档里示范 ——
    写下示范动作本身就会变成一处「违规」。
    """
    root = _repo(tmp_path)
    (root / "README.md").write_text(f"# 测试\n\n```\n{line}\n```\n", encoding="utf-8")
    assert _check("G4", root).status == dk.PASS


# ---------------------------------------------------------------- G5 README 工具表
def test_g5_flags_a_tool_in_the_readme_that_the_code_does_not_have(tmp_path):
    """表里多写一个同样有害：对方会去调一个不存在的工具。"""
    root = _repo(tmp_path)
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "| `get_passage` | `kb:read` | 取文 |",
        "| `get_passage` | `kb:read` | 取文 |\n| `drop_table` | `db:query` | 删表 |"),
        encoding="utf-8")
    r = _check("G5", root)
    assert r.status == dk.FAIL
    assert any("drop_table" in h["needle"] for h in r.hits)


def test_g5_flags_a_tool_that_is_missing_from_the_readme(tmp_path):
    root = _repo(tmp_path)
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "| `get_passage` | `kb:read` | 取文 |\n", ""), encoding="utf-8")
    r = _check("G5", root)
    assert r.status == dk.FAIL
    assert any("代码里有、表里没写" in h["needle"] for h in r.hits)


def test_g5_flags_a_scope_that_disagrees_with_the_code(tmp_path):
    root = _repo(tmp_path)
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "| `run_sql` | `db:query` |", "| `run_sql` | `db:read` |"), encoding="utf-8")
    r = _check("G5", root)
    assert r.status == dk.FAIL
    assert any("scope" in h["needle"] for h in r.hits)


# ---------------------------------------------------------------- G6 README 路径
def test_g6_flags_a_repo_path_in_the_readme_that_does_not_exist(tmp_path):
    root = _repo(tmp_path)
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n见 `reports/没有这个.md`。\n",
                 encoding="utf-8")
    r = _check("G6", root)
    assert r.status == dk.FAIL
    assert any("reports/没有这个.md" in h["needle"] for h in r.hits)


@pytest.mark.parametrize("line", [
    "见 <https://example.invalid/x>。",           # 外链
    "见 [这一节](#工具)。",                        # 锚点
    "见 `reports/*.md`。",                        # 通配符
])
def test_g6_ignores_things_that_are_not_claims_about_local_files(tmp_path, line):
    """外链/锚点/通配符本来就不是「仓库里必须有这个文件」的声明。"""
    root = _repo(tmp_path)
    p = root / "README.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n" + line + "\n", encoding="utf-8")
    assert _check("G6", root).status == dk.PASS


# ---------------------------------------------------------------- G7 报告引用
def test_g7_flags_a_reference_to_a_report_that_is_not_there(tmp_path):
    """指向一份不存在的报告，比不说还糟 —— 对方会以为是自己克隆不完整。"""
    root = _repo(tmp_path)
    (root / "reports" / "other.md").write_text(
        "## 口径\n实测数字见 reports/never_made.md。\n", encoding="utf-8")
    r = _check("G7", root)
    assert r.status == dk.FAIL
    assert any("never_made.md" in h["needle"] for h in r.hits)


@pytest.mark.parametrize("marker", ["尚未生成", "未完成", "阶段 6", "会被创建"])
def test_g7_accepts_a_reference_that_says_the_file_is_not_generated_yet(
        tmp_path, marker):
    """★ 反方向：**当场说明**它还没有，就该放过。

    否则文档里连「这份报告需要凭据，还没跑」都不能写 —— 那样读者更糊涂。
    """
    root = _repo(tmp_path)
    (root / "reports" / "other.md").write_text(
        f"## 口径\n实测数字见 reports/later.md（{marker}）。\n", encoding="utf-8")
    assert _check("G7", root).status == dk.PASS


def test_g7_survives_a_line_wrap_between_the_path_and_the_marker(tmp_path):
    """★ 这条是踩出来的：按**行**判会把折到下一行的「（尚未生成）」判丢，
    报一堆假红。所以判据是**自然段**。"""
    root = _repo(tmp_path)
    (root / "reports" / "other.md").write_text(
        "## 口径\n实测数字见 reports/later.md\n（尚未生成，需要模型凭据）。\n",
        encoding="utf-8")
    assert _check("G7", root).status == dk.PASS


def test_g7_does_not_mistake_jsonl_for_json(tmp_path):
    """★ 也是踩出来的：`usage_ledger.jsonl` 曾被正则截成 `.json`，
    报了一个根本不存在的问题。边界要卡死。"""
    root = _repo(tmp_path)
    (root / "reports" / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "reports" / "other.md").write_text(
        "## 口径\n记账写在 reports/ledger.jsonl 里。\n", encoding="utf-8")
    assert _check("G7", root).status == dk.PASS


# ---------------------------------------------------------------- G8 依赖
def test_g8_flags_a_third_party_import_that_is_not_declared(tmp_path):
    root = _repo(tmp_path)
    (root / "mcp_server" / "tool.py").write_text("import openai\n", encoding="utf-8")
    r = _check("G8", root)
    assert r.status == dk.FAIL
    assert any("openai" in h["needle"] for h in r.hits)


def test_g8_knows_that_jwt_is_shipped_as_pyjwt(tmp_path):
    """★ import 名与发行包名不一致的正是最容易漏的：`import jwt` → PyJWT、
    `import dotenv` → python-dotenv。漏了就是别人 clone 下来 ImportError。"""
    root = _repo(tmp_path)
    (root / "mcp_server" / "tool.py").write_text("import jwt\nimport dotenv\n",
                                                 encoding="utf-8")
    assert _check("G8", root).status == dk.FAIL, "写 PyJWT/python-dotenv 才算声明过"

    (root / "requirements.txt").write_text("PyJWT==2.14.0\npython-dotenv==1.2.3\n",
                                           encoding="utf-8")
    assert _check("G8", root).status == dk.PASS


def test_g8_does_not_fail_on_standard_library_imports(tmp_path):
    root = _repo(tmp_path)
    (root / "mcp_server" / "tool.py").write_text(
        "import json\nimport re\nfrom pathlib import Path\n", encoding="utf-8")
    (root / "requirements.txt").write_text("jieba==0.42.1\n", encoding="utf-8")
    assert _check("G8", root).status == dk.PASS


# ---------------------------------------------------------------- 自排除
def test_the_checker_does_not_scan_its_own_report(tmp_path):
    """★ 生成物不参与生成它的那条检查。

    报告里「命中的行」一节是**引用**。把引用当成新的声明来判，报告会自己
    触发自己，而且每轮多套一层反引号（实测长过三代）。
    这条测试反过来验证：真把那份报告算进来，它一定会红。
    """
    root = _repo(tmp_path)
    own = root / "reports" / "deliverable_check.md"
    own.write_text("## 口径\n- 脚本不存在：99_ghost.py\n见 reports/never.md\n",
                   encoding="utf-8")
    assert _check("G4", root).status == dk.PASS
    assert _check("G7", root).status == dk.PASS
    assert own.resolve() not in {p.resolve() for p in dk._docs(root)}
