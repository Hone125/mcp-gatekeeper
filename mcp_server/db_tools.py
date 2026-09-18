"""数据库工具：列结构、看表结构、执行查询。

## 两类连接，权限不同 —— 这个区分是刻意的

| 用途 | 走哪条路 | 能不能读内部表 |
|---|---|---|
| **用户提供的 SQL** | `guardrails.run_query()` —— 三层护栏全开 | 不能 |
| **本模块自己拼的 SQL**（列结构、看表结构） | `_introspect_connect()` —— 只读但**不挂 authorizer** | 能 |

为什么列结构要看 `sqlite_master`？因为那是 SQLite 唯一权威的表结构来源。
而三层护栏里的 L1/L3 恰好禁掉了内部表 —— 这是**对的**，用户不该拿 `run_sql` 去翻
`sqlite_master`。但工具自己也跟着被挡住就没法干活了。

这里的关键区别是：**这两条路上的 SQL 由谁写**。用户写的那条必须过三层；
工具自己写的那条是固定的模板字符串，参数全部走占位符，用户只能决定「查哪张表」，
决定不了「查什么语句」。所以它不是绕过护栏，是走另一条本来就不需要护栏的路。

**只读属性两条路都有**（`mode=ro`），所以即便判断失误，也写不进去任何东西。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp_server import guardrails, paths

E_NO_DB = "DB_NO_DB"
E_NO_SUCH_TABLE = "DB_NO_SUCH_TABLE"
E_INTROSPECT = "DB_INTROSPECT_FAILED"


def _introspect_connect(db_path: Path | None = None) -> sqlite3.Connection:
    """只读、无 authorizer 的内部连接。**只用于本模块自己拼的固定语句。**"""
    p = Path(db_path) if db_path is not None else paths.chinook_db()
    if not p.is_file():
        raise FileNotFoundError(
            f"数据库文件不存在：{p}\n先跑一次构建脚本：python experiments/01_build_db.py")
    return sqlite3.connect(guardrails._readonly_uri(p), uri=True, timeout=5.0)


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def list_tables(db_path: Path | None = None) -> dict:
    """列出所有表及各自行数。**不抛异常。**"""
    try:
        conn = _introspect_connect(db_path)
    except FileNotFoundError as e:
        return {"ok": False, "err_code": E_NO_DB, "detail": f"{e}", "tables": []}
    try:
        tables = []
        for name in _table_names(conn):
            n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            tables.append({"name": name, "rows": n})
    except sqlite3.Error as e:
        return {"ok": False, "err_code": E_INTROSPECT, "detail": f"{type(e).__name__}: {e}",
                "tables": []}
    finally:
        conn.close()
    return {"ok": True, "n_tables": len(tables), "tables": tables}


def table_schema(table: str, db_path: Path | None = None) -> dict:
    """单张表的列定义与外键。**不抛异常。**"""
    try:
        conn = _introspect_connect(db_path)
    except FileNotFoundError as e:
        return {"ok": False, "err_code": E_NO_DB, "detail": f"{e}"}
    try:
        names = _table_names(conn)
        if table not in names:
            return {"ok": False, "err_code": E_NO_SUCH_TABLE,
                    "detail": f"没有名为 {table!r} 的表",
                    "available": names}
        cols = [{"name": r[1], "type": (r[2] or "").upper(), "notnull": bool(r[3]),
                 "pk": bool(r[5])}
                for r in conn.execute(f'PRAGMA table_info("{table}")')]
        # 外键信息对写 SQL 帮助很大（模型最容易错的就是 join 条件），所以带上
        fks = [{"column": r[3], "ref_table": r[2], "ref_column": r[4]}
               for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')]
        n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error as e:
        return {"ok": False, "err_code": E_INTROSPECT, "detail": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()
    # 键名统一用 `name`，和 `list_tables` 保持一致。
    # 这里原本叫 `table`，而 `schema_doc` 读的是 `name` —— 一个键名不一致，
    # 结果渲染出来的结构里表名全是 `?`，模型拿不到表名自然写不对 SQL，
    # 而失败现象看起来像「模型不行」。统一的键名 + 下面 test_db_tools.py 里
    # 那条断言，是为了让这类错误下次在测试里就撞墙，而不是等到评测报告上。
    return {"ok": True, "name": table, "rows": n, "columns": cols, "foreign_keys": fks}


def get_schema(table: str | None = None, db_path: Path | None = None) -> dict:
    """不带 `table` 就返回全部表的结构。带 `table` 就只返回那一张。**不抛异常。**"""
    if table:
        r = table_schema(table, db_path)
        if not r["ok"]:
            return {**r, "tables": []}
        return {"ok": True, "n_tables": 1, "tables": [r]}

    lt = list_tables(db_path)
    if not lt["ok"]:
        return {**lt, "tables": []}
    tables = []
    for t in lt["tables"]:
        r = table_schema(t["name"], db_path)
        tables.append(r if r["ok"] else t)
    return {"ok": True, "n_tables": len(tables), "tables": tables}


def schema_doc(db_path: Path | None = None, max_tables: int = 40) -> str:
    """把库结构渲染成一段紧凑文本，用于拼进给模型的提示词。

    格式是刻意选的 —— 每张表先给名字和行数，再逐列给「列名 类型」，
    主键标 `PK`、外键标 `-> 表.列`。实测这种铺法比 JSON 省一半 token，
    而且模型对「表名：列名 列名」这种行文格式更不容易看串行。
    """
    doc = get_schema(None, db_path)
    if not doc.get("ok"):
        return f"（读不到库结构：{doc.get('detail', doc.get('err_code'))}）"
    lines = []
    for t in doc["tables"][:max_tables]:
        lines.append(f"表 {t.get('name', '?')}（{t.get('rows', '?')} 行）")
        fk_map = {f["column"]: f"{f['ref_table']}.{f['ref_column']}"
                  for f in t.get("foreign_keys", [])}
        for c in t.get("columns", []):
            tags = []
            if c["pk"]:
                tags.append("PK")
            if c["notnull"] and not c["pk"]:
                tags.append("NOT NULL")
            if c["name"] in fk_map:
                tags.append(f"-> {fk_map[c['name']]}")
            suffix = ("  " + " ".join(tags)) if tags else ""
            lines.append(f"  {c['name']} {c['type'] or '?'}{suffix}")
        lines.append("")
    return "\n".join(lines).strip()


def run_sql(sql: str, db_path: Path | None = None) -> dict:
    """执行一条查询，**三层护栏全开**。返回 `guardrails.QueryResult` 的字典形式。

    这是唯一接受用户 SQL 的入口。返回值里 `blocked` / `ok` 两个字段的组合含义见
    `guardrails.QueryResult` 的文档 —— 「被拦下」和「SQL 写错了」是两回事，不要合并。
    """
    return guardrails.run_query(sql, db_path).as_dict()
