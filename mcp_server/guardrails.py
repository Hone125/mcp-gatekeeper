"""SQL 护栏：三层防御，逐层独立生效。

## 三层分别防什么

| 层 | 手段 | 防住什么 | 挡不住的 |
|---|---|---|---|
| L1 | 本模块 `guard_sql()` 的文本校验 | 明显的写操作、多语句、非查询语句、内部表 | 变形写法（表值 pragma 函数、将来新增的语法） |
| L2 | `mode=ro` 只读连接 | 任何落到磁盘的写 | 读侧的信息泄露 |
| L3 | `sqlite3.set_authorizer` 引擎级回调 | **语句真正执行时**才暴露的越权动作 | 不经过 SQLite 的动作（不存在） |

**L3 不是装饰。** 实测有一个真实案例能通过 L1、被 L3 拦下：

    SELECT * FROM pragma_table_info('Track')

这句话不含 `PRAGMA` 关键字（`pragma_table_info` 里 `PRAGMA` 后面跟的是下划线，
`\\bPRAGMA\\b` 匹配不上），不以写关键字开头，也不含 `sqlite_`，L1 完全放行；
但它执行时 SQLite 会以 `SQLITE_PRAGMA` 动作回调 authorizer，L3 拒绝。
`reports/guardrail_results.md` 里有这条的逐条留痕。

## 为什么不用 SQL 解析器

- 依赖 `sqlparse` 只能做「切词 + 粗略分类」，它不构建语义树，判断不了 `LIMIT` 到底属于哪层子查询，
  也判断不了某个标识符是表名还是列名 —— 用了它还是要自己写一堆正则，白多一个依赖。
- 真正的语义正确性由 **L3（引擎自己）** 提供：SQLite 在最权威的位置告诉我们「这句话想干什么」。
  解析器只能猜，引擎不用猜。

所以这里的取舍是：**L1 用保守的正则做粗筛（宁可误杀，不可放过），把语义判断交给引擎。**

## 已知的误杀（如实记录）

- `LIMIT` 只能写字面量整数。`LIMIT (SELECT ...)` 会被 R7 拒绝。
- 字符串字面量里出现 `DROP` 之类的词**不会**误杀 —— 扫描阶段会把字面量内容打码（见 `_scan`）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from mcp_server import console, paths

# 单次查询最多返回多少行。缺 LIMIT 时自动补这个值；显式写超过这个值的 LIMIT 会被拒绝。
MAX_ROWS = 200

# ---------------------------------------------------------------- 规则编号
# 编号是稳定契约：reports/guardrail_results.md 的每一行都引用它，测试也断言它。
R_EMPTY = "R1_EMPTY"
R_MULTI_STATEMENT = "R2_MULTI_STATEMENT"
R_NOT_SELECT = "R3_NOT_SELECT"
R_FORBIDDEN_KEYWORD = "R4_FORBIDDEN_KEYWORD"
R_INTERNAL_TABLE = "R5_INTERNAL_TABLE"
R_LIMIT_TOO_LARGE = "R6_LIMIT_TOO_LARGE"
R_LIMIT_NOT_LITERAL = "R7_LIMIT_NOT_LITERAL"
R_ENGINE_DENY = "R8_ENGINE_DENY"          # 由 L3 触发，不在 guard_sql 里

RULE_TEXT = {
    R_EMPTY: "空语句",
    R_MULTI_STATEMENT: "只允许单条语句（检测到语句内分号）",
    R_NOT_SELECT: "只允许 SELECT / WITH 查询",
    R_FORBIDDEN_KEYWORD: "命中写操作/管理关键字",
    R_INTERNAL_TABLE: "禁止访问 SQLite 内部表",
    R_LIMIT_TOO_LARGE: f"LIMIT 超过上限 {MAX_ROWS}",
    R_LIMIT_NOT_LITERAL: "LIMIT 必须是字面量整数",
    R_ENGINE_DENY: "被 SQLite 引擎级 authorizer 拒绝",
}

# ---------------------------------------------------------------- 关键字表
# 注意这里**刻意不含 REPLACE**：REPLACE 在 SQLite 里同时是标量函数
# `REPLACE(str, from, to)`，加进来会让 `SELECT REPLACE(Title,'a','b') FROM Album`
# 被误杀。而语句形态的 `REPLACE INTO ...` 不以 SELECT/WITH 开头，已由 R3 拦下，
# 所以去掉它不降低防护强度。这个取舍写在这里，免得以后有人「顺手补上」。
FORBIDDEN_KEYWORDS: tuple[str, ...] = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "TRIGGER",
    "GRANT", "REVOKE",
)

_RE_FORBIDDEN = re.compile(r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b", re.I)
_RE_INTERNAL = re.compile(r"\bsqlite_(master|schema|temp_master|temp_schema)\b", re.I)
_RE_LEADING_OK = re.compile(r"^\s*(SELECT|WITH)\b", re.I)
_RE_LIMIT_ANY = re.compile(r"\bLIMIT\b", re.I)
_RE_LIMIT_NUM = re.compile(r"\bLIMIT\s+(\d+)\s*(?:,\s*(\d+))?", re.I)
_RE_NOT_AUTHORIZED = re.compile(r"not authorized|prohibited", re.I)


# ---------------------------------------------------------------- 文本扫描
def _scan(sql: str) -> tuple[str, str, int]:
    """扫描一遍 SQL，返回 `(clean, masked, n_comments)`。

    - `clean`：注释被删掉，字符串字面量**原样保留**。用于拼接 LIMIT 与最终执行。
    - `masked`：在 clean 的基础上，把字符串字面量**内容**逐字符换成 `x`
      （长度和位置都不变）。用于关键字/分号检查 —— 这样 `'DROP'` 或 `'a;b'`
      出现在字面量里就不会被误判成注入。
    - `n_comments`：删掉了几个注释，用于报告。

    手写扫描而不是用正则替换，是因为正则理解不了「这个 `--` 在引号里面」：

        SELECT * FROM t WHERE name = 'a--b'      ← 引号里的 -- 不是注释

    用 `re.sub(r"--[^\\n]*", ...)` 处理会把这句话截断成 `SELECT * FROM t WHERE name = 'a`，
    变成一个语法错误的查询。这种「净化反而弄坏合法输入」的行为，比不净化更难排查。
    """
    clean: list[str] = []
    masked: list[str] = []
    n = len(sql)
    i = 0
    n_comments = 0
    while i < n:
        ch = sql[i]

        # 行注释：跳到行尾，但保留换行符本身（否则相邻两行会粘成一个 token）
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            n_comments += 1
            continue

        # 块注释：跳到 */
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            n_comments += 1
            continue

        # 字符串字面量（'...'）与带引号标识符（"..."）：整体保留在 clean，
        # 内容在 masked 里打码。'' 与 "" 是转义写法。
        if ch in ("'", '"'):
            q = ch
            clean.append(ch)
            masked.append(ch)
            i += 1
            while i < n:
                if sql[i] == q:
                    if i + 1 < n and sql[i + 1] == q:
                        clean.append(q)
                        clean.append(q)
                        masked.append(q)
                        masked.append(q)
                        i += 2
                        continue
                    clean.append(q)
                    masked.append(q)
                    i += 1
                    break
                clean.append(sql[i])
                masked.append("x")
                i += 1
            continue

        clean.append(ch)
        masked.append(ch)
        i += 1

    return "".join(clean), "".join(masked), n_comments


@dataclass(frozen=True)
class Verdict:
    """L1 的判定结果。`ok=True` 时 `sql` 是可直接执行的语句。"""

    ok: bool
    sql: str = ""
    rule: str = ""
    reason: str = ""
    limit_injected: bool = False
    comments_removed: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "sql": self.sql,
            "rule": self.rule,
            "reason": self.reason,
            "limit_injected": self.limit_injected,
            "comments_removed": self.comments_removed,
        }


def guard_sql(sql: str) -> Verdict:
    """L1：静态文本校验。返回 `Verdict`。

    检查顺序是有讲究的：先报**最具体**的规则。例如 `DROP TABLE x` 同时满足
    「不以 SELECT 开头」和「命中写关键字」，报告 R4 比 R3 更有信息量，
    所以 R4 排在 R3 前面。
    """
    clean, masked, n_comments = _scan(sql or "")

    # 归一化：去掉首尾空白，再去掉**一个**结尾分号。
    # 只去一个是有意的 —— `SELECT 1;;` 去掉一个还剩一个，会被 R2 拦下；
    # 如果用 rstrip(";") 会把所有尾分号都吃掉，`SELECT 1;;;;` 就混过去了。
    clean = clean.strip()
    masked = masked.strip()
    if masked.endswith(";"):
        clean = clean[:-1].rstrip()
        masked = masked[:-1].rstrip()

    if not masked:
        return Verdict(False, rule=R_EMPTY, reason=RULE_TEXT[R_EMPTY])

    if ";" in masked:
        return Verdict(False, rule=R_MULTI_STATEMENT, reason=RULE_TEXT[R_MULTI_STATEMENT],
                       comments_removed=n_comments)

    m = _RE_FORBIDDEN.search(masked)
    if m:
        return Verdict(False, rule=R_FORBIDDEN_KEYWORD,
                       reason=f"{RULE_TEXT[R_FORBIDDEN_KEYWORD]}：{m.group(0).upper()}",
                       comments_removed=n_comments)

    if _RE_INTERNAL.search(masked):
        return Verdict(False, rule=R_INTERNAL_TABLE, reason=RULE_TEXT[R_INTERNAL_TABLE],
                       comments_removed=n_comments)

    if not _RE_LEADING_OK.match(masked):
        return Verdict(False, rule=R_NOT_SELECT, reason=RULE_TEXT[R_NOT_SELECT],
                       comments_removed=n_comments)

    # LIMIT 检查。取**最后一个** LIMIT 子句 —— 对顶层查询来说那就是外层的那个。
    if _RE_LIMIT_ANY.search(masked):
        nums = _RE_LIMIT_NUM.findall(masked)
        if not nums:
            return Verdict(False, rule=R_LIMIT_NOT_LITERAL, reason=RULE_TEXT[R_LIMIT_NOT_LITERAL],
                           comments_removed=n_comments)
        first, second = nums[-1]
        # `LIMIT a, b` 在 SQLite 里是「跳过 a 行、取 b 行」，所以有效上限是第二个数
        cap = int(second) if second else int(first)
        if cap > MAX_ROWS:
            return Verdict(False, rule=R_LIMIT_TOO_LARGE,
                           reason=f"{RULE_TEXT[R_LIMIT_TOO_LARGE]}（实际 {cap}）",
                           comments_removed=n_comments)
        return Verdict(True, sql=clean, comments_removed=n_comments)

    # 没有 LIMIT：自动补一个，并**在返回体里标记**，让调用方知道 SQL 被改写过了。
    # 这里选择「改写」而不是「拒绝」：这条规则防的是内存被打满，不是安全。
    # 拒绝会让状态机为一句话反复重写、白烧 token，而改写达到了同样的目的。
    return Verdict(True, sql=clean + f" LIMIT {MAX_ROWS}", limit_injected=True,
                   comments_removed=n_comments)


# ---------------------------------------------------------------- L3：引擎级
# 引擎在准备语句时会带着「这句话想干什么」回调这个函数，我们按动作类型拒绝。
#
# 两个必须注意的点：
#   1. **绝不能拒绝 SQLITE_SELECT**（动作 21）。那会把所有查询一起拒掉，
#      包括我们自己的合法查询 —— 护栏变成一堵不分敌我的墙。
#   2. 设置时机必须在 `connect()` **之后**。连接建立过程中 SQLite 自己会做内部操作，
#      太早挂上回调会拦到自己的初始化。
_DENY_ACTIONS = frozenset({
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_PRAGMA,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_ANALYZE,
})

# 记录被 L3 拒掉的调用，供报告引用。只在进程内累积，不落盘、不参与业务逻辑。
engine_denials: list[dict] = []


def _authorizer(action: int, arg1, arg2, db_name, trigger) -> int:
    """L3 回调。返回 SQLITE_OK 放行、SQLITE_DENY 拒绝。"""
    if action == sqlite3.SQLITE_READ:
        # 读内部表也拒。实测这**不会**误伤正常查询：SQLite 只在语句真正读某张表时
        # 才以 SQLITE_READ 回调，做名字解析时并不回调，所以 `SELECT ... FROM Track` 不受影响。
        if arg1 and str(arg1).lower().startswith("sqlite_"):
            engine_denials.append({"action": "SQLITE_READ", "target": str(arg1)})
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action in _DENY_ACTIONS:
        engine_denials.append({"action": f"SQLITE_ACTION_{action}", "target": str(arg1)})
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _readonly_uri(db_path: Path) -> str:
    """构造 `file:...?mode=ro` 形式的只读 URI。

    用 `quote` 转义：路径里可能有空格、`#`、`?` 这些在 URI 里有特殊含义的字符，
    不转义的话 SQLite 会把它们当成 URI 语法，最后报一个和真实原因毫无关系的
    「unable to open database file」。
    """
    return "file:" + quote(db_path.resolve().as_posix(), safe="/:") + "?mode=ro"


def _connect(db_path: Path | None, with_authorizer: bool) -> sqlite3.Connection:
    """打开一个 `mode=ro` 连接，可选是否挂 L3 回调。

    `with_authorizer=False` **只给排查问题用**：要证明「L2 自己也能挡住写」，就必须
    造出一个只经过 L2 的路径。生产路径上永远是 `True`。
    """
    p = Path(db_path) if db_path is not None else paths.chinook_db()
    if not p.is_file():
        # 提前给出清晰错误。否则 SQLite 只会说 "unable to open database file"，
        # 让人误以为是权限或 URI 格式的问题，而真实原因是文件根本没构建。
        raise FileNotFoundError(
            f"数据库文件不存在：{p}\n"
            f"先跑一次构建脚本：python experiments/01_build_db.py"
        )
    conn = sqlite3.connect(_readonly_uri(p), uri=True, timeout=5.0)
    if with_authorizer:
        conn.set_authorizer(_authorizer)
    return conn


def connect_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """打开一个**只读 + 挂了 authorizer** 的连接。L2 与 L3 都在这里生效。"""
    return _connect(db_path, with_authorizer=True)


# ---------------------------------------------------------------- 执行
@dataclass
class QueryResult:
    """`run_query` 的返回体。字段刻意区分三种「没成功」：

    - `blocked=True`：被护栏拦下（L1 或 L3），`rule`/`reason` 有值
    - `blocked=False, ok=False`：护栏放行了，但 SQL 本身执行失败（语法错、列不存在），`error` 有值
    - `ok=True`：正常返回

    两种失败必须分开：状态机对它们的处理不同 —— 被拦下要改写法，执行失败要改语义。
    """

    ok: bool
    blocked: bool
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    n_rows: int = 0
    truncated: bool = False
    rule: str = ""
    reason: str = ""
    error: str = ""
    limit_injected: bool = False
    comments_removed: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "blocked": self.blocked, "sql": self.sql,
            "columns": self.columns, "rows": self.rows, "n_rows": self.n_rows,
            "truncated": self.truncated, "rule": self.rule, "reason": self.reason,
            "error": self.error, "limit_injected": self.limit_injected,
            "comments_removed": self.comments_removed,
        }


def run_query(sql: str, db_path: Path | None = None) -> QueryResult:
    """走完三层，执行一条查询。**本函数不抛异常**，所有失败都降级成返回体字段。"""
    v = guard_sql(sql)
    if not v.ok:
        return QueryResult(ok=False, blocked=True, sql="", rule=v.rule, reason=v.reason,
                           comments_removed=v.comments_removed)

    try:
        conn = connect_readonly(db_path)
    except FileNotFoundError as e:
        return QueryResult(ok=False, blocked=False, sql=v.sql, error=f"FileNotFoundError: {e}")

    try:
        cur = conn.execute(v.sql)
        columns = [d[0] for d in (cur.description or [])]
        # 多取一行用来判断「是否被截断」，比另外发一条 COUNT(*) 便宜得多。
        fetched = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(fetched) > MAX_ROWS
        rows = [list(r) for r in fetched[:MAX_ROWS]]
        return QueryResult(ok=True, blocked=False, sql=v.sql, columns=columns, rows=rows,
                           n_rows=len(rows), truncated=truncated,
                           limit_injected=v.limit_injected, comments_removed=v.comments_removed)
    except sqlite3.Error as e:
        msg = str(e)
        if _RE_NOT_AUTHORIZED.search(msg):
            # L3 拦下的。这是**安全拦截**，不是 SQL 写错了，必须和语法错分开报。
            return QueryResult(ok=False, blocked=True, sql=v.sql, rule=R_ENGINE_DENY,
                               reason=f"{RULE_TEXT[R_ENGINE_DENY]}：{msg}",
                               limit_injected=v.limit_injected, comments_removed=v.comments_removed)
        return QueryResult(ok=False, blocked=False, sql=v.sql, error=f"{type(e).__name__}: {msg}",
                           limit_injected=v.limit_injected, comments_removed=v.comments_removed)
    finally:
        conn.close()


# ---------------------------------------------------------------- 命令行
# 为什么护栏要有一个 CLI：
#
# 报告里每一条负例都要写「期望 / 实测 / **退出码**」。如果只看进程内的返回值，
# 「实测」就是我自己打印的一句话 —— 我说它被拒了，它就被拒了。走一次真实的
# 子进程，退出码由操作系统给出，这个证据的强度不一样。
#
# 退出码：
#   0 = 放行且执行成功
#   1 = 放行但执行失败（护栏没拦，是 SQL 本身写错了）
#   2 = 被护栏拦下（L1 静态校验或 L3 引擎回调）
#   3 = 数据库文件不存在（环境未就绪，不是安全问题）
#   4 = 用法错误
E_USAGE = 4
E_ALLOWED_OK = 0
E_ALLOWED_FAILED = 1
E_BLOCKED = 2
E_NO_DB = 3

_RAW_WARNING = (
    "⚠️  --raw 会**同时跳过 L1 与 L3**，只保留 L2（mode=ro）只读连接。\n"
    "    它存在的唯一目的是演示「L2 单独也能挡住写」。\n"
    "    服务端永远不会走这条路径。\n"
)


def main(argv: list[str] | None = None) -> int:
    """把一条 SQL 丢给护栏，用退出码汇报结果。见上面的退出码表。"""
    # 这个 CLI 的 stdout 是给**机器解析**的：调用方（`11_guardrail_test.py`、
    # `tests/conftest.py`、`tests/test_sql_guardrail.py`）一律按 UTF-8 解码它。
    # 不钉死编码的话，在 GBK 机器上解出来就是乱码 —— 报告里会出现
    # 「报错原因是乱码」，看着吓人，其实无害。
    console.setup_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    raw = False
    no_authorizer = False

    db: Path | None = None
    words: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        # 只把**已知的**参数名当参数。写成「-- 开头就算参数」会把
        #     guardrails "-- 只留注释"
        # 这种合法输入当参数吞掉，SQL 变成空串，然后报一个「用法错误」——
        # 调用方完全看不出是自己那句话被吃了。这和 _scan 里「引号里的 -- 不是注释」
        # 是同一类问题：光看前缀判断不了，得知道真正的语法。
        if a == "--raw":
            raw = True
        elif a == "--no-authorizer":
            no_authorizer = True
        elif a == "--db":
            if i + 1 >= len(argv):
                print("--db 后面要跟一个路径", file=sys.stderr)
                return E_USAGE
            db = Path(argv[i + 1])
            i += 2
            continue
        elif a == "--":
            # 传统的「参数到此为止」分隔符：后面全是 SQL，哪怕看起来像参数
            words.extend(argv[i + 1:])
            break
        else:
            words.append(a)
        i += 1

    sql = " ".join(words)
    if not sql.strip():
        print("用法：python -m mcp_server.guardrails [--raw] [--no-authorizer] "
              "[--db 路径] \"<SQL>\"", file=sys.stderr)
        return E_USAGE

    if raw:
        print(_RAW_WARNING, file=sys.stderr)
        verdict_sql = sql
        limit_injected = False
        comments_removed = 0
    else:
        v = guard_sql(sql)
        if not v.ok:
            print(json.dumps({"layer": "L1", "blocked": True, "rule": v.rule,
                              "reason": v.reason, "sql": ""},
                             ensure_ascii=False))
            return E_BLOCKED
        verdict_sql = v.sql
        limit_injected = v.limit_injected
        comments_removed = v.comments_removed

    try:
        conn = _connect(db, with_authorizer=not (raw or no_authorizer))
    except FileNotFoundError as e:
        print(json.dumps({"layer": "L2", "blocked": False, "error": str(e)},
                         ensure_ascii=False))
        return E_NO_DB

    try:
        cur = conn.execute(verdict_sql)
        columns = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(MAX_ROWS + 1)
        rows = [list(r) for r in fetched[:MAX_ROWS]]
        print(json.dumps({"layer": "L2+L3", "ok": True, "blocked": False,
                          "sql": verdict_sql, "columns": columns,
                          "n_rows": len(rows), "truncated": len(fetched) > MAX_ROWS,
                          "limit_injected": limit_injected,
                          "comments_removed": comments_removed}, ensure_ascii=False))
        return E_ALLOWED_OK
    except sqlite3.Error as e:
        msg = str(e)
        if _RE_NOT_AUTHORIZED.search(msg):
            print(json.dumps({"layer": "L3", "blocked": True, "rule": R_ENGINE_DENY,
                              "reason": msg, "sql": verdict_sql}, ensure_ascii=False))
            return E_BLOCKED
        print(json.dumps({"layer": "L2+L3", "ok": False, "blocked": False,
                          "sql": verdict_sql, "error": f"{type(e).__name__}: {msg}"},
                         ensure_ascii=False))
        return E_ALLOWED_FAILED
    finally:
        conn.close()


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
