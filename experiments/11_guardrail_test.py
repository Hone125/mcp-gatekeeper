"""SQL 护栏负例矩阵：每条 SQL 走一次**真实子进程**，记录「期望 / 实测 / 退出码」。

## 为什么要走子进程

进程内调 `guard_sql()` 然后打印一句「它被拒了」，那个「实测」列是我自己写的字符串 ——
我说什么就是什么。走 `python -m mcp_server.guardrails "<SQL>"` 之后，
**退出码由操作系统给出**，拦下/放行/执行失败/数据缺失四种结局各占一个不同的码。
证据的强度不一样，代价只是每条多 0.15 秒。

## 三层各自都要有独立的证据

只报「这些 SQL 都被拒了」是不够的：三层里只要有一层在干活，负例就全是绿的，
另外两层是不是死的完全看不出来。所以这里对每一层都单独构造了「只有这一层能拦住」的用例：

| 层 | 只有它能拦住的用例 | 怎么证明另外两层确实放行了 |
|---|---|---|
| L1 | `DROP TABLE Genre` | 它在连库之前就返回了（返回体里没有 `layer: L2+L3`） |
| L2 | `--raw` 直接执行 `INSERT`（绕过 L1 与 L3） | 返回体写明 `layer`，报的是 readonly database |
| L3 | `SELECT * FROM pragma_table_info('Track')` | ★ **同一条 SQL 加 `--no-authorizer` 就成功**，证明 L1 真的放它过去了 |

第三行是这个实验里最关键的一条。没有它，「pragma 那句被拒了」可以解释成
「L1 拦的」，那 L3 是不是在干活就无从判断。

## 退出码

由 `mcp_server/guardrails.py` 的 `main()` 定义：
`0` 放行且执行成功 / `1` 放行但执行失败 / `2` 被护栏拦下 / `3` 数据未就绪 / `4` 用法错误。

## 本实验的退出码

`0` 全部符合期望；`1` 有不符合期望的用例；`3` 示例库未构建（先跑 `01_build_db.py`）。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import console  # noqa: E402
from mcp_server import guardrails as g  # noqa: E402
from mcp_server import paths  # noqa: E402

REPORTS = paths.reports_dir()

# CLI 退出码（与 guardrails.main 的常量一一对应，这里故意用模块常量而不是字面量，
# 免得将来改了 CLI 的码而报告没跟着改）
EX_OK = g.E_ALLOWED_OK
EX_EXEC_FAIL = g.E_ALLOWED_FAILED
EX_BLOCKED = g.E_BLOCKED
EX_NO_DB = g.E_NO_DB
EX_USAGE = g.E_USAGE


class Case(dict):
    """一条用例。字段：label / sql / expect / extra / why。"""


def C(label: str, sql: str, expect: str, *, rule: str = "", why: str = "",
      extra: tuple[str, ...] = (), layer: str = "") -> Case:
    """`expect` 取值：
    `blocked` 被拦下 / `ok` 放行 / `rewritten` 放行且补了 LIMIT /
    `execfail` 放行但执行失败 / `usage` 用法错误
    """
    return Case(label=label, sql=sql, expect=expect, rule=rule, why=why,
                extra=list(extra), layer=layer)


# ================================================================ L1 静态校验
L1_CASES: list[Case] = [
    # --- R1 空 ---
    C("只有注释，没有语句", "-- 只留注释", "blocked", rule=g.R_EMPTY,
      why="注释剥掉之后什么都不剩。空语句交给 SQLite 会得到一句含糊的错误，提前报更清楚。"),
    C("只有两个连字符", "--", "blocked", rule=g.R_EMPTY),

    # --- R2 多语句 ---
    C("两条 SELECT 用分号连起来", "SELECT 1; SELECT 2", "blocked", rule=g.R_MULTI_STATEMENT,
      why="多语句是注入的经典载体：第一条合法、第二条干别的。"),
    C("结尾多写一个分号", "SELECT 1;;", "blocked", rule=g.R_MULTI_STATEMENT,
      why="刻意的：只去掉**一个**结尾分号，所以 `;;` 还剩一个，被拦下。"
          "用 rstrip(';') 会把 `;;;;` 全吃掉，这条就混过去了。"),

    # --- R4 写操作 / 管理关键字 ---
    C("DROP TABLE", "DROP TABLE Genre", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("INSERT", "INSERT INTO Genre (GenreId, Name) VALUES (99, 'x')", "blocked",
      rule=g.R_FORBIDDEN_KEYWORD),
    C("UPDATE", "UPDATE Album SET Title = 'x'", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("DELETE", "DELETE FROM Album", "blocked", rule=g.R_FORBIDDEN_KEYWORD,
      why="它同时满足 R3（不以 SELECT 开头）与 R4。报 R4 信息量更大，所以 R4 排在前面。"),
    C("PRAGMA 语句", "PRAGMA table_info(Track)", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("ATTACH 挂载别的库", "ATTACH DATABASE 'evil.db' AS e", "blocked",
      rule=g.R_FORBIDDEN_KEYWORD,
      why="挂上另一个库是「把数据搬出去」的常见第一步，必须拦。"),
    C("CREATE TABLE", "CREATE TABLE t (a)", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("ALTER TABLE", "ALTER TABLE Album ADD COLUMN z", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("VACUUM", "VACUUM", "blocked", rule=g.R_FORBIDDEN_KEYWORD),
    C("REINDEX", "REINDEX", "blocked", rule=g.R_FORBIDDEN_KEYWORD),

    # --- R5 内部表 ---
    C("读 sqlite_master", "SELECT name FROM sqlite_master", "blocked", rule=g.R_INTERNAL_TABLE),
    C("读 sqlite_schema", "SELECT * FROM sqlite_schema", "blocked", rule=g.R_INTERNAL_TABLE),
    C("内部表名写成大写", "SELECT name FROM SQLITE_MASTER", "blocked", rule=g.R_INTERNAL_TABLE,
      why="大小写不敏感，`SQLITE_MASTER` 一样拦。"),

    # --- R3 非查询语句 ---
    C("以 VALUES 开头", "VALUES (1)", "blocked", rule=g.R_NOT_SELECT),
    C("以 EXPLAIN 开头", "EXPLAIN QUERY PLAN SELECT 1 FROM Album", "blocked",
      rule=g.R_NOT_SELECT,
      why="⚠️ 这是**已知的误杀**：EXPLAIN 本身是只读的，被 R3 拦了。"
          "取舍是「宁可误杀不可放过」——放行 EXPLAIN 需要为它单开一条规则，"
          "而它在正常使用中不出现。照实记录，不假装它不存在。"),

    # --- R6 LIMIT 过大 ---
    C("LIMIT 超过上限", "SELECT * FROM Album LIMIT 201", "blocked", rule=g.R_LIMIT_TOO_LARGE),
    C("LIMIT 大得离谱", "SELECT * FROM Album LIMIT 5000", "blocked", rule=g.R_LIMIT_TOO_LARGE),
    C("LIMIT a,b 形式，第二个数超限", "SELECT * FROM Album LIMIT 10, 5000", "blocked",
      rule=g.R_LIMIT_TOO_LARGE,
      why="SQLite 里 `LIMIT a, b` 是「跳过 a 行、取 b 行」，有效上限是**第二个**数。"
          "只看第一个数会漏掉这条。"),

    # --- R7 LIMIT 不是字面量 ---
    C("LIMIT 后面是子查询", "SELECT * FROM Album LIMIT (SELECT 1)", "blocked",
      rule=g.R_LIMIT_NOT_LITERAL,
      why="⚠️ 这是**已知的误杀**：`LIMIT (SELECT 1)` 其实是安全的，但它是动态上限，"
          "静态校验判不了。同上，保守优先。"),
    C("LIMIT 后面是占位符", "SELECT * FROM Album LIMIT ?", "blocked",
      rule=g.R_LIMIT_NOT_LITERAL),
]

# ================================================================ 应放行（L1）
ALLOW_CASES: list[Case] = [
    C("最普通的查询，缺 LIMIT", "SELECT Title FROM Album", "rewritten",
      why="缺 LIMIT 是**改写**（补 `LIMIT 200` 并在返回体标 `limit_injected`），"
          "不是拒绝。这条规则防的是内存被打满，不是安全；拒绝会让上游为一句话反复重写、白烧调用次数。"),
    C("常量查询", "SELECT 1", "rewritten"),
    C("WITH 开头", "WITH x AS (SELECT 1 AS n) SELECT n FROM x", "rewritten",
      why="WITH 也放行，否则所有 CTE 写法都用不了。"),
    C("LIMIT 在上限之内", "SELECT * FROM Album LIMIT 50", "ok"),
    C("LIMIT a,b 两个数都在上限内", "SELECT * FROM Album LIMIT 10, 50", "ok"),
    C("分行写、带块注释", "SELECT\n  /* 只取标题 */\n  Title\nFROM Album\nLIMIT 5", "ok"),

    # ★ 下面四条是「不能误杀」的证据。少了它们，把关键字表调得再严也全绿。
    C("字符串字面量里有分号", "SELECT 'a;b'", "rewritten",
      why="★ 分号在**字面量**里，不是语句分隔符。扫描阶段会给字面量内容打码，所以不触发 R2。"
          "用正则 `;` 直接查会把这条合法查询拦掉。"),
    C("字符串字面量里有 --", "SELECT * FROM Album WHERE Title = 'a--b'", "rewritten",
      why="★ 引号里的 `--` 不是注释。用 `re.sub(r'--[^\\n]*')` 清洗会把这句话从 `'a` 处截断，"
          "变成一个语法错误的查询 —— 净化反而弄坏合法输入。"),
    C("字符串字面量里有 DROP", "SELECT 'DROP TABLE x'", "rewritten",
      why="★ 字面量被打码，所以关键字扫描看不到它。"),
    C("调用名为 replace 的标量函数", "SELECT replace(Title, 'a', 'b') FROM Album", "rewritten",
      why="★ `REPLACE` **刻意不在**关键字表里：它同时是标量函数，加进去会让这条合法查询被误杀。"
          "而 `REPLACE INTO ...` 不以 SELECT 开头，已由 R3 拦下，所以去掉它不降低防护强度。"),
]

# ================================================================ L3 与「三态」
DB_CASES: list[Case] = [
    # ★ 本实验的核心一条
    C("用表值函数读 pragma（L1 放行、L3 拦下）",
      "SELECT * FROM pragma_table_info('Track')", "blocked", rule=g.R_ENGINE_DENY,
      layer="L3",
      why="★ 这句话不含 `PRAGMA` 关键字（`pragma_table_info` 里后面跟的是下划线，"
          "`\\bPRAGMA\\b` 匹配不上），不以写关键字开头，也不含 `sqlite_` —— **L1 完全放行**。"
          "它被执行时才以 SQLITE_PRAGMA 动作回调 authorizer，被 L3 拒掉。"
          "这是「L3 不是装饰」的实证。下面的对照用例给出另一半证据。"),
    C("把 pragma 表值函数包进 CTE", "WITH x AS (SELECT * FROM pragma_table_info('Track')) "
      "SELECT * FROM x", "blocked", rule=g.R_ENGINE_DENY, layer="L3",
      why="换个写法一样拦 —— 拦的是动作，不是文本。"),
    C("正常查询不受 L3 影响", "SELECT Name FROM Track LIMIT 5", "ok", layer="L3",
      why="★ 反向证据：L3 挂了回调之后普通查询照样跑。"
          "authorizer 最容易写错的地方就是顺手把 SQLITE_SELECT 也拒了，那样护栏会变成一堵不分敌我的墙。"),
    C("查询一个不存在的表", "SELECT * FROM NoSuchTable", "execfail",
      why="★ 三态里的第三态：护栏**放行**了（`blocked=False`），是 SQL 本身执行失败。"
          "它必须和「被护栏拦下」分开报 —— 状态机对两者的处理不同："
          "被拦下要改**写法**，执行失败要改**语义**（列名/表名写错了）。"),
]

# ================================================================ 逐层隔离
# 这三条不进「期望/实测」主表，单列一节，因为它们要证明的是「某一层单独有效」。
LAYER_CASES: list[Case] = [
    Case(label="L1 确实放行了 pragma 那句（对照组）",
         sql="SELECT * FROM pragma_table_info('Track')", expect="ok",
         extra=["--no-authorizer"], layer="L1→L2",
         why="★ **上一条的对照组**。同一条 SQL，只把 L3 关掉，它就成功了 —— "
             "这证明拦下它的确实是 L3，而不是「L1 其实也拦了、只是错误码写成了 R8」。"
             "没有这一条，L3 是不是在干活就无法判断。"),
    Case(label="L2 单独挡住写（绕过 L1 与 L3）",
         sql="INSERT INTO Genre (GenreId, Name) VALUES (99, 'zzz')", expect="execfail",
         extra=["--raw"], layer="L2",
         why="★ 用 `--raw` 同时跳过 L1 与 L3，只剩 `mode=ro` 只读连接。"
             "写操作被文件模式挡下（readonly database），证明 L2 自己就是一道独立防线，"
             "而不是「反正 L1 已经拦了」的摆设。"),
    Case(label="L2 仍允许读（--raw 不是一刀切全拒）",
         sql="SELECT COUNT(*) FROM Genre", expect="ok", extra=["--raw"], layer="L2",
         why="反向证据：`--raw` 下读操作正常。否则上一条的「被拒」可以解释成"
             "「--raw 这个开关本身把什么都拒了」。"),
]


# ================================================================ 跑
def run_cli(sql: str, extra: list[str], timeout: int = 60) -> dict:
    """跑一次真实的 CLI，返回 `{exit, data, stderr}`。**不抛异常。**"""
    try:
        p = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-m", "mcp_server.guardrails",
             *extra, "--", sql],
            cwd=str(ROOT), capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"exit": -1, "data": {}, "stderr": f"超时（{timeout}s）未返回"}
    err = p.stderr.decode("utf-8", errors="replace")
    out = p.stdout.decode("utf-8", errors="replace").strip()
    data: dict = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"exit": p.returncode, "data": data, "stderr": err}


def measure(case: Case) -> dict:
    """把一次运行压成报告里的一行：期望 / 实测 / 退出码 / 结果。"""
    r = run_cli(case.get("sql", ""), case.get("extra", []))
    d = r["data"]
    exp = case["expect"]
    got = ""
    ok = False

    if exp == "blocked":
        code = d.get("rule", "")
        got = f"拦下（{code or '未报规则'}）"
        ok = r["exit"] == EX_BLOCKED and code == case.get("rule", "")
    elif exp == "ok":
        got = f"放行，{d.get('n_rows', '?')} 行"
        ok = r["exit"] == EX_OK and d.get("blocked") is False
    elif exp == "rewritten":
        got = (f"放行，补 LIMIT={d.get('limit_injected')}"
               if d.get("limit_injected") else "放行，但**没有**补 LIMIT")
        ok = r["exit"] == EX_OK and d.get("limit_injected") is True
    elif exp == "execfail":
        got = f"放行后执行失败（{str(d.get('error', ''))[:60]}）"
        ok = r["exit"] == EX_EXEC_FAIL and d.get("blocked") is False
    elif exp == "usage":
        got = "用法错误"
        ok = r["exit"] == EX_USAGE
    else:
        got = f"内部错误：未知的期望类型 {exp!r}"
        ok = False

    return {
        "label": case["label"], "sql": case.get("sql", ""), "expect": exp,
        "expect_text": {
            "blocked": f"拦下（{case.get('rule', '')}）", "ok": "放行",
            "rewritten": "改写（补 LIMIT，非拒绝）", "execfail": "放行但执行失败",
            "usage": "用法错误",
        }.get(exp, exp),
        "got": got, "exit": r["exit"], "layer": case.get("layer", ""),
        "why": case.get("why", ""), "extra": case.get("extra", []),
        "detail": d, "pass": ok,
    }


def genre_rows(db: Path) -> int | None:
    """数一下 Genre 有多少行，用来证明 `--raw` 那次写**真的没写进去**。"""
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM Genre").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return None


# ================================================================ 报告
def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ")
                                     for c in r) + " |")
    return "\n".join(out)


def _short(sql: str, n: int = 52) -> str:
    s = " ".join(sql.split())
    return f"`{s if len(s) <= n else s[:n - 1] + '…'}`"


def render_md(d: dict) -> str:
    L: list[str] = []
    L.append("# SQL 护栏负例矩阵（实测结果）\n")
    L.append("本文件由 `experiments/11_guardrail_test.py` 生成，**不要手改**；重跑该脚本即可复现。\n")

    L.append("## 口径\n")
    L.append("- **一条用例** = 一次真实子进程调用 "
             "`python -m mcp_server.guardrails \"<SQL>\"`。")
    L.append("- **退出码由操作系统给出**，不是我在进程内打印的一句话。取值："
             "`0` 放行且执行成功 / `1` 放行但执行失败 / `2` 被护栏拦下 / "
             "`3` 数据未就绪 / `4` 用法错误。")
    L.append("- **通过** = 退出码与错误码**两个都对**。不接受「反正拒了」："
             "拦下必须报出**期望的那条规则号**，否则「L1 拦的」和「L3 拦的」就分不开了。")
    L.append("- 证据强度：子进程退出码 > 进程内返回值。后者是我自己写的字符串。")
    L.append(f"- 运行方式：`python experiments/11_guardrail_test.py`（退出码 0 = 全过）\n")

    L.append("## 总览\n")
    L.append(_table([
        ["L1 静态校验（含应拒与应放行）",
         f"{d['l1_pass']} / {d['l1_cases']}"],
        ["L3 引擎级 + 执行三态", f"{d['db_pass']} / {d['db_cases']}"],
        ["逐层隔离（每层单独有效的证据）", f"{d['layer_pass']} / {d['layer_cases']}"],
        ["**合计**", f"**{d['n_pass']} / {d['n_cases']}**"],
    ], ["项", "通过 / 总数"]))
    L.append("")

    L.append("## 一、L1 静态校验：应拒用例\n")
    L.append(_table(
        [[i + 1, r["label"], _short(r["sql"]), r["expect_text"], r["got"],
          f"`{r['exit']}`", "✅" if r["pass"] else "❌"]
         for i, r in enumerate(d["l1_deny"])],
        ["#", "用例", "SQL", "期望", "实测", "退出码", "结果"]))
    L.append("")

    L.append("## 二、L1 静态校验：应放行用例（不能误杀）\n")
    L.append("只测「该拒的都拒了」是一种假安全 —— 把关键字表调到拒掉一切，那组用例照样全绿。"
             "下面这些**必须放行**。\n")
    L.append(_table(
        [[i + 1, r["label"], _short(r["sql"]), r["expect_text"], r["got"],
          f"`{r['exit']}`", "✅" if r["pass"] else "❌"]
         for i, r in enumerate(d["l1_allow"])],
        ["#", "用例", "SQL", "期望", "实测", "退出码", "结果"]))
    L.append("")

    L.append("## 三、L3 引擎级，以及「三态」\n")
    L.append("`blocked` 与 `error` 是两回事：前者是护栏拦下的，后者是护栏放行、SQL 自己执行失败。"
             "状态机对两者的处理不同 —— 被拦下要改**写法**，执行失败要改**语义**。"
             "混成一个「失败」会让上游只能瞎猜。\n")
    L.append(_table(
        [[i + 1, r["label"], _short(r["sql"]), r["expect_text"], r["got"],
          f"`{r['exit']}`", "✅" if r["pass"] else "❌"]
         for i, r in enumerate(d["db_rows"])],
        ["#", "用例", "SQL", "期望", "实测", "退出码", "结果"]))
    L.append("")

    L.append("## 四、逐层隔离：每一层单独有效的证据\n")
    L.append("只报「这些 SQL 都被拒了」是不够的：三层里只要有一层在干活，负例就全是绿的，"
             "另外两层是不是死的完全看不出来。下面三条为每一层单独构造了"
             "「只有这一层能拦住 / 只有这一层会放行」的场景。\n")
    L.append(_table(
        [[i + 1, r["label"], _short(r["sql"]), r["expect_text"], r["got"],
          f"`{r['exit']}`", "✅" if r["pass"] else "❌"]
         for i, r in enumerate(d["layer_rows"])],
        ["#", "用例", "SQL", "期望", "实测", "退出码", "结果"]))
    L.append("")
    L.append(f"> **`--raw` 那次写有没有真的写进去**：全程开始前 `Genre` 有 "
             f"{d['genre_before']} 行，跑完之后 {d['genre_after']} 行。"
             "`mode=ro` 连接下 SQLite 在文件层面就拒绝写，所以那条 INSERT 报的是 "
             "`attempt to write a readonly database`，磁盘上一个字节都没变。")
    L.append("> 这里**不拿「报错了」当证据**，拿的是前后行数对比 —— 报错信息也可能是别的原因。\n")

    L.append("## 五、每一条的取舍说明\n")
    L.append("下表是上面各用例的设计理由，主要是**已知的误杀**和**刻意不拦的东西**。"
             "两种情况都照实写：一个 SQL 护栏如果声称自己零误杀，那它多半是没测过。\n")
    L.append(_table(
        [[r["label"], r["why"]] for r in d["all_rows"] if r["why"]],
        ["用例", "为什么这么设计"]))
    L.append("")

    fails = [r for r in d["all_rows"] if not r["pass"]]
    if fails:
        L.append("## ⚠️ 不符合期望的用例\n")
        for r in fails:
            L.append(f"- **{r['label']}**（{_short(r['sql'])}）："
                     f"期望 {r['expect_text']}，实测 {r['got']}，退出码 `{r['exit']}`。"
                     f"原始返回：`{json.dumps(r['detail'], ensure_ascii=False)}`")
        L.append("")

    L.append("## 六、局限\n")
    L.append("- 用例是**枚举**出来的。枚举得再全也不能证明「不存在能绕过的写法」，"
             "本表证明的是「列出的这些都被挡住了」。")
    L.append("- L1 是**保守**的文本规则，不是语义分析：上面第五节列出了两条已知误杀。"
             "设计取向是「宁可误杀，不可放过」—— 语义正确性交给 L3（引擎自己）兜底。")
    L.append("- L3 挡的是**动作**。如果将来 SQLite 新增一种越权动作而不在 `_DENY_ACTIONS` 里，"
             "它不会被拦。缓解手段是 L2 的只读连接不依赖这张动作表。")
    L.append("- 本表不涉及**跨库**场景（`ATTACH` 被 L1 拒，所以没有可测的跨库读）。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    db = paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        print("         先跑：python experiments/01_build_db.py", file=sys.stderr)
        return EX_NO_DB

    REPORTS.mkdir(parents=True, exist_ok=True)
    before = genre_rows(db)

    l1_deny = [measure(c) for c in L1_CASES]
    l1_allow = [measure(c) for c in ALLOW_CASES]
    l1_rows = l1_deny + l1_allow
    db_rows = [measure(c) for c in DB_CASES]
    layer_rows = [measure(c) for c in LAYER_CASES]

    # 空的表格是最难发现的一类报告缺陷：它能正常渲染、看上去是个表头加零行，
    # 而「应放行」那张表一旦为空，整个实验就退化成了「一刀切全拒也满分」。
    for name, rows in (("L1 应拒", l1_deny), ("L1 应放行", l1_allow),
                       ("L3/三态", db_rows), ("逐层隔离", layer_rows)):
        if not rows:
            print(f"[FAIL] {name} 一节是用例空的，报告会缺一整块", file=sys.stderr)
            return 1

    after = genre_rows(db)

    all_rows = l1_rows + db_rows + layer_rows
    data = {
        "l1_deny": l1_deny, "l1_allow": l1_allow, "db_rows": db_rows,
        "layer_rows": layer_rows, "all_rows": all_rows,
        "l1_cases": len(l1_rows), "l1_pass": sum(r["pass"] for r in l1_rows),
        "db_cases": len(db_rows), "db_pass": sum(r["pass"] for r in db_rows),
        "layer_cases": len(layer_rows), "layer_pass": sum(r["pass"] for r in layer_rows),
        "genre_before": before, "genre_after": after,
    }
    data["n_cases"] = len(all_rows)
    data["n_pass"] = sum(r["pass"] for r in all_rows)

    (REPORTS / "guardrail_results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / "guardrail_results.md").write_text(render_md(data),
                                                  encoding="utf-8", newline="\n")

    print("=" * 72)
    print("SQL 护栏负例矩阵（每条走一次真实子进程）")
    print("=" * 72)
    print(f"  L1 静态校验    {data['l1_pass']} / {data['l1_cases']}")
    print(f"  L3 + 执行三态  {data['db_pass']} / {data['db_cases']}")
    print(f"  逐层隔离       {data['layer_pass']} / {data['layer_cases']}")
    print(f"  合计           {data['n_pass']} / {data['n_cases']}")
    print("-" * 72)
    print(f"  Genre 行数：跑之前 {before}，跑之后 {after}"
          f"{'  ✅ 未被改动' if before == after else '  ❌ 被改动了！'}")

    ok = data["n_pass"] == data["n_cases"]
    for r in all_rows:
        if not r["pass"]:
            ok = False
            print(f"  [FAIL] {r['label']}")
            print(f"         期望 {r['expect_text']}，实测 {r['got']}，"
                  f"退出码 {r['exit']}（期望 {r['expect']}）")
    if ok:
        print("  [OK] 全部符合期望：该拒的拒、该放行的放行、每层都单独有效")
    print("\n已写入 reports/guardrail_results.md 与 reports/guardrail_results.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
