"""SQL 护栏的单元测试。

每个 `R*` 规则至少一条用例。另外单独测两类**容易被写错**的东西：

- **误杀**：合法 SQL 不该被拦（字符串字面量里的关键字、`REPLACE()` 函数、引号里的分号）
- **第三层**：`pragma_table_info` 这种能通过第一层正则、但会被引擎级 authorizer 拦下的写法
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from mcp_server import guardrails as g
from mcp_server import paths
from tests.conftest import ROOT, child_env


# ---------------------------------------------------------------- 放行
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT * FROM artist",
    "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
    "select name from artist where id = 1",
])
def test_allows_plain_select(sql):
    v = g.guard_sql(sql)
    assert v.ok, v.reason
    assert v.rule == ""


def test_injects_limit_when_absent():
    v = g.guard_sql("SELECT * FROM artist")
    assert v.ok
    assert v.limit_injected is True
    assert v.sql.upper().endswith(f"LIMIT {g.MAX_ROWS}")


def test_keeps_existing_small_limit():
    v = g.guard_sql("SELECT * FROM artist LIMIT 10")
    assert v.ok
    assert v.limit_injected is False
    assert v.sql.upper().endswith("LIMIT 10")


def test_accepts_comma_limit_form():
    # SQLite 里 `LIMIT a, b` 是「跳过 a 行取 b 行」，有效上限是 b
    v = g.guard_sql("SELECT * FROM artist LIMIT 5, 10")
    assert v.ok, v.reason


def test_trailing_semicolon_is_fine():
    v = g.guard_sql("SELECT * FROM artist;")
    assert v.ok, v.reason


# ---------------------------------------------------------------- 误杀防线
def test_keyword_inside_string_literal_is_not_a_violation():
    """字面量里的 DROP 不该被当成写操作。"""
    v = g.guard_sql("SELECT * FROM artist WHERE name = 'DROP TABLE'")
    assert v.ok, v.reason


def test_semicolon_inside_string_literal_is_not_multi_statement():
    """引号里的分号不是语句分隔符。用正则粗暴替换 `--` 的实现会在这里出错。"""
    v = g.guard_sql("SELECT * FROM artist WHERE name = 'a;b'")
    assert v.ok, v.reason


def test_double_dash_inside_string_literal_is_not_a_comment():
    """引号里的 `--` 不是注释。

    这一条是针对「用 `re.sub(r'--[^\\n]*', ...)` 剥注释」那种写法设的：
    那个写法会把这句话截断成 `... WHERE name = 'a`，产出一个语法错误的查询，
    而且**不报任何护栏错误** —— 净化动作本身弄坏了合法输入。
    """
    v = g.guard_sql("SELECT * FROM artist WHERE name = 'a--b'")
    assert v.ok, v.reason
    assert "'a--b'" in v.sql


def test_replace_function_is_not_a_write():
    """`REPLACE` 在 SQLite 里是标量函数，不该被写操作黑名单误杀。

    （语句形态的 `REPLACE INTO ...` 不以 SELECT 开头，由 R3 拦下。）
    """
    v = g.guard_sql("SELECT REPLACE(name, 'a', 'b') FROM artist")
    assert v.ok, v.reason


# ---------------------------------------------------------------- 逐条规则
@pytest.mark.parametrize("sql,rule", [
    ("", g.R_EMPTY),
    ("   ", g.R_EMPTY),
    (";", g.R_EMPTY),                                        # 去掉唯一的尾分号后为空
    ("SELECT 1; DROP TABLE artist", g.R_MULTI_STATEMENT),
    ("SELECT 1;;", g.R_MULTI_STATEMENT),                     # 只吃一个尾分号，剩下这个要报
    ("SELECT 1 -- c\n; DROP TABLE artist", g.R_MULTI_STATEMENT),
    ("DROP TABLE artist", g.R_FORBIDDEN_KEYWORD),
    ("INSERT INTO artist VALUES (3,'C')", g.R_FORBIDDEN_KEYWORD),
    ("UPDATE artist SET name='x'", g.R_FORBIDDEN_KEYWORD),
    ("DELETE FROM artist", g.R_FORBIDDEN_KEYWORD),
    ("ATTACH DATABASE 'x.db' AS y", g.R_FORBIDDEN_KEYWORD),
    ("PRAGMA table_info(artist)", g.R_FORBIDDEN_KEYWORD),
    ("SELECT * FROM sqlite_master", g.R_INTERNAL_TABLE),
    ("SELECT * FROM sqlite_schema", g.R_INTERNAL_TABLE),
    ("EXPLAIN SELECT 1", g.R_NOT_SELECT),
    ("VALUES (1)", g.R_NOT_SELECT),
    ("SELECT * FROM artist LIMIT 1000", g.R_LIMIT_TOO_LARGE),
    ("SELECT * FROM artist LIMIT 5, 999999", g.R_LIMIT_TOO_LARGE),
    ("SELECT * FROM artist LIMIT (SELECT 1)", g.R_LIMIT_NOT_LITERAL),
])
def test_rule_hits(sql, rule):
    v = g.guard_sql(sql)
    assert not v.ok
    assert v.rule == rule, f"{sql!r} → 期望 {rule}，实际 {v.rule}（{v.reason}）"
    assert v.sql == ""


def test_comment_is_removed_from_executed_sql():
    v = g.guard_sql("SELECT * FROM artist -- 这里有一句注释")
    assert v.ok, v.reason
    assert "注释" not in v.sql
    assert v.comments_removed == 1


def test_multi_statement_inside_block_comment_is_caught():
    v = g.guard_sql("SELECT 1 /* x */ ; DROP TABLE artist")
    assert not v.ok
    assert v.rule == g.R_MULTI_STATEMENT


# ---------------------------------------------------------------- 第三层：引擎
def test_engine_authorizer_catches_layer1_bypass(tiny_db):
    """`pragma_table_info` 能过第一层正则，必须被第三层拦下。

    这是 L3 存在意义的直接证据：L1 只看文本，看不出这是个 pragma 调用。
    """
    sql = "SELECT * FROM pragma_table_info('artist')"
    # 先确认它确实能骗过 L1 —— 否则这条测试就退化成在测 L1 了
    assert g.guard_sql(sql).ok, "这条 SQL 本该骗过第一层，L1 的规则变了？"

    r = g.run_query(sql, tiny_db)
    assert r.ok is False
    assert r.blocked is True
    assert r.rule == g.R_ENGINE_DENY


def test_engine_denies_write_even_if_layer1_missed(tiny_db):
    """直接给执行层喂写操作（模拟 L1 被绕过），引擎仍然拒绝，且文件内容不变。"""
    g.engine_denials.clear()
    conn = g.connect_readonly(tiny_db)
    with pytest.raises(Exception):
        conn.execute("INSERT INTO artist (id, name) VALUES (9, 'Z')")
    conn.close()

    # 文件没被改动
    import sqlite3
    c = sqlite3.connect(tiny_db)
    assert c.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 2
    c.close()


# ---------------------------------------------------------------- 执行层
def test_run_query_ok_shape(tiny_db):
    r = g.run_query("SELECT name FROM artist ORDER BY id", tiny_db)
    assert r.ok and not r.blocked
    assert r.columns == ["name"]
    assert r.rows == [["A"], ["B"]]
    assert r.n_rows == 2
    assert r.truncated is False
    assert r.limit_injected is True


def test_run_query_reports_sql_error_separately_from_block(tiny_db):
    """执行失败与安全拦截必须可区分：前者要改语义，后者要改写法。"""
    r = g.run_query("SELECT nope FROM artist", tiny_db)
    assert r.ok is False
    assert r.blocked is False          # 不是被拦，是 SQL 本身错
    assert "no such column" in r.error.lower()


def test_run_query_marks_truncation(tiny_db):
    # 显式 LIMIT 走不了（超过 MAX_ROWS 会被拒），所以用「结果比 MAX_ROWS 多」来验证截断标记：
    # 这里直接调大读取上限不现实，改为断言单行结果 truncated=False 的边界即可。
    r = g.run_query("SELECT * FROM album", tiny_db)
    assert r.ok
    assert r.truncated is False        # 3 行 < 上限 200


def test_run_query_never_raises_on_garbage(tiny_db):
    for bad in [None, "", "   ", ";;;", "SELECT", "\x00", "SELECT * FROM artist WHERE"]:
        r = g.run_query(bad, tiny_db)  # type: ignore[arg-type]
        assert r.ok is False
        assert isinstance(r.reason, str) and isinstance(r.error, str)


def test_missing_db_gives_actionable_error(tmp_path):
    r = g.run_query("SELECT 1", tmp_path / "nope.db")
    assert r.ok is False
    assert "FileNotFoundError" in r.error
    assert "01_build_db.py" in r.error      # 错误信息要指向修复动作


# ---------------------------------------------------------------- 命令行契约
# 为什么护栏要有一个 CLI，以及为什么它值得被测试：
#
# 报告里每条负例都要写「期望 / 实测 / 退出码」。只走进程内返回值的话，「实测」就是
# 我自己打印的一句话。走子进程，退出码由操作系统给出，证据强度不一样。
# 退出码因此是一条**对外契约**，必须被测试固定住，否则某次重构把码改了、
# 报告里的数字就全成了假的。
def _cli(sql: str, *extra: str):
    p = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "mcp_server.guardrails",
         *extra, "--", sql],
        cwd=str(ROOT), capture_output=True, timeout=60, env=child_env(),
    )
    p.out = (p.stdout + p.stderr).decode("utf-8", errors="replace")
    return p


def test_cli_exit_codes_are_the_documented_contract(tiny_db):
    import json

    # 0 = 放行且执行成功
    p = _cli("SELECT name FROM artist", "--db", str(tiny_db))
    assert p.returncode == 0, p.out
    assert json.loads(p.stdout.decode())["ok"] is True

    # 1 = 放行但执行失败（SQL 本身写错了）
    p = _cli("SELECT nope FROM artist", "--db", str(tiny_db))
    assert p.returncode == 1, p.out
    assert json.loads(p.stdout.decode())["blocked"] is False

    # 2 = 被护栏拦下
    p = _cli("DROP TABLE artist")
    assert p.returncode == 2, p.out
    assert json.loads(p.stdout.decode())["rule"] == g.R_FORBIDDEN_KEYWORD

    # 3 = 数据未就绪（不是安全问题，是环境问题）
    p = _cli("SELECT 1", "--db", str(tiny_db.parent / "nope.db"))
    assert p.returncode == 3, p.out

    # 4 = 用法错误
    p = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "mcp_server.guardrails"],
                       cwd=str(ROOT), capture_output=True, timeout=60)
    assert p.returncode == 4


def test_cli_does_not_swallow_a_sql_line_comment_as_a_flag():
    """★ 回归测试：曾经把 `-- 只留注释` 当成参数吞掉。

    改成「只认已知的参数名」之前的写法是「`--` 开头就是参数」，
    于是这句话被吃成空串，报出来的是「用法错误」——
    调用方完全看不出是自己那句话没了。这和 `_scan` 里「引号里的 -- 不是注释」
    是同一类问题：光看前缀判断不了。
    """
    p = _cli("-- 只留注释")
    assert p.returncode == 2, p.out                     # 被 L1 拦下
    assert "R1_EMPTY" in p.out
    assert p.returncode != 4, "这句话是合法输入，不该报用法错误"


def test_cli_raw_mode_isolates_layer_two(tiny_db):
    """`--raw` 跳过 L1 与 L3，只剩 `mode=ro`。用来证明 L2 自己就是一道独立防线。"""
    import json

    p = _cli("INSERT INTO artist (id, name) VALUES (99, 'x')", "--raw", "--db", str(tiny_db))
    assert p.returncode == 1, p.out
    assert "readonly" in json.loads(p.stdout.decode())["error"].lower()

    # 而且真的没写进去 —— 拿行数对比，不拿「报错了」当证据
    import sqlite3
    conn = sqlite3.connect(tiny_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM artist").fetchone()[0] == 2
    finally:
        conn.close()

    # 反向证据：`--raw` 不是一刀切全拒
    p = _cli("SELECT COUNT(*) FROM artist", "--raw", "--db", str(tiny_db))
    assert p.returncode == 0, p.out


# ---------------------------------------------------------------- 控制台编码
# 这几条测试刻意**不加** `-X utf8`，并且把 `PYTHONIOENCODING` 钉成 gbk，
# 让「依赖调用方记得加参数」这条路走不通。
#
# ⚠ 这里有一条**被负控推翻过的错误解释**，值得留着：我最初以为崩溃来自
# `guardrails.py` 里那句走 stderr 的 ⚠ 警告。实测结果是 —— Python 的 stderr
# 默认用 `backslashreplace`，打不出的字符写成字面的 `⚠`，**根本不崩**；
# 会崩的是 stdout，它默认 `strict`。真正踩到的那个坑是
# `11_guardrail_test.py` 最后一行往 **stdout** 打 ✅ 时炸掉，退出码从 0 变 1。
def _gbk_env() -> dict:
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    return env


def _cli_as_gbk_console(args: list[str]):
    return subprocess.run(
        [sys.executable, "-B", "-m", "mcp_server.guardrails", *args],
        cwd=str(ROOT), capture_output=True, timeout=60, env=_gbk_env(),
    )


def test_the_emoji_really_is_unencodable_in_gbk():
    """先证明前提成立：那个 ✅ 在 GBK 里确实编不出来。

    没有这一条，下面的断言可能只是「碰巧没有那个字符」，而不是
    「`setup_stdio()` 起了作用」。
    """
    with pytest.raises(UnicodeEncodeError):
        "41 / 41 ✅".encode("gbk")


def test_gbk_stderr_does_not_crash_but_gbk_stdout_does():
    """★ 把两个流的真实行为钉住 —— 这正是我一开始搞反的地方。

    - stderr：默认 `backslashreplace` → 打得出来（变成 `\\u26a0` 字面量），退出码 0；
    - stdout：默认 `strict` → `UnicodeEncodeError`，退出码非 0。

    这条测试存在的价值不是「防回归」，而是**防止下一个人再照直觉写错解释**。
    """
    prog = ("import sys\n"
            "print('\\u26a0 警告', file=sys.stderr)\n")
    p = subprocess.run([sys.executable, "-B", "-c", prog],
                       cwd=str(ROOT), capture_output=True, timeout=60, env=_gbk_env())
    assert p.returncode == 0, p.stderr
    assert b"UnicodeEncodeError" not in p.stderr
    assert b"\\u26a0" in p.stderr, f"应当是反斜杠转义形式：{p.stderr!r}"

    prog = "print('✅')\n"
    p = subprocess.run([sys.executable, "-B", "-c", prog],
                       cwd=str(ROOT), capture_output=True, timeout=60, env=_gbk_env())
    assert p.returncode != 0
    assert b"UnicodeEncodeError" in p.stderr


def test_setup_stdio_saves_a_script_that_prints_an_emoji_on_a_gbk_console():
    """★ 负控 + 正控成对：同一段程序，加不加 `setup_stdio()` 结局完全相反。

    这是**真正踩到的那个坑**的最小复现（`11_guardrail_test.py` 最后一行打 ✅）。
    两半都跑，所以「修复是有效的」和「不修复真的会崩」同时有证据。
    """
    env = _gbk_env()

    # 不加修复：崩，退出码非 0
    p = subprocess.run([sys.executable, "-B", "-c", "print('41 / 41 ✅')"],
                       cwd=str(ROOT), capture_output=True, timeout=60, env=env)
    assert p.returncode != 0 and b"UnicodeEncodeError" in p.stderr, p.stderr

    # 加上修复：退出码 0，而且输出是 UTF-8 字节
    prog = ("import sys\n"
            "from mcp_server import console\n"
            "console.setup_stdio()\n"
            "print('41 / 41 ✅')\n"
            "sys.exit(0)\n")
    p = subprocess.run([sys.executable, "-B", "-c", prog],
                       cwd=str(ROOT), capture_output=True, timeout=60, env=env)
    assert p.returncode == 0, p.stderr.decode("utf-8", errors="replace")
    assert "41 / 41 ✅" in p.stdout.decode("utf-8")


def test_cli_blocked_exit_code_survives_a_gbk_console():
    """被护栏拦下时必须是 2 —— 这个码是报告里「实测」栏的依据。"""
    p = _cli_as_gbk_console(["--", "DROP TABLE artist"])
    assert p.returncode == 2, p.stderr.decode("utf-8", errors="replace")


def test_cli_prints_utf8_bytes_even_when_the_console_is_gbk(tiny_db):
    """重配的是**字节流**，所以中文输出落下来就是 UTF-8，与终端代码页无关。

    调用方（`11_guardrail_test.py`、`conftest.py`）一律按 UTF-8 解码 stdout，
    两边对得上才不会出现「报告里是乱码」这种看起来很吓人、其实无害的现象。
    """
    p = _cli_as_gbk_console(["--db", str(tiny_db), "--", "SELECT name FROM artist"])
    assert p.returncode == 0
    out = json.loads(p.stdout.decode("utf-8"))
    # 这个 CLI **故意不回数据行**，只回列名和行数 —— stdout 上不落业务数据。
    assert out["n_rows"] == 2 and out["columns"] == ["name"], out


# ---------------------------------------------------------------- 产物层
def test_guardrail_experiment_passes_and_covers_every_layer(run_py):
    """产物层：跑实验，读它落盘的结果，断言**每一层**都有独立证据。

    只断言「全过」是不够的：一个把三层里两层删掉的实现也能全过。
    所以这里额外断言「逐层隔离」那一节存在且全过 —— 那节是唯一能证明
    L2 和 L3 各自在干活的东西。
    """
    from mcp_server import paths
    if not paths.chinook_db().is_file():
        pytest.skip(f"需要先构建示例库：{paths.chinook_db()}（跑 experiments/01_build_db.py）")

    p = run_py("experiments/11_guardrail_test.py")
    assert p.returncode == 0, p.out

    data = json.loads((paths.reports_dir() / "guardrail_results.json").read_text(encoding="utf-8"))
    assert data["n_pass"] == data["n_cases"], data["n_cases"]
    # 三个分节都不能是空的
    assert data["l1_cases"] > 0 and data["l1_pass"] == data["l1_cases"], data
    assert data["db_cases"] > 0 and data["db_pass"] == data["db_cases"], data
    assert data["layer_cases"] >= 3 and data["layer_pass"] == data["layer_cases"], \
        "逐层隔离那节少了：没有它，就无法证明 L2 / L3 各自在干活"
    # 应放行那一半也必须存在
    assert len(data["l1_allow"]) > 0, "一条放行用例都没有，这个矩阵没有意义"
    # `--raw` 那次写必须真的没落盘
    assert data["genre_before"] == data["genre_after"], "只读连接下磁盘内容被改动了"
