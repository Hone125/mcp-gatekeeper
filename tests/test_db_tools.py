"""看库结构这一层的测试。

## 为什么这个文件是补出来的

`schema_doc()` 的输出是**喂给模型的唯一结构来源** —— 提示词里的表名、列名、
外键全都从它来。它原本一条测试都没有，于是「渲染出来的表名全是 `?`」这个
bug 一直躺着：函数不报错、不抛异常、结构看起来也整整齐齐，只是表名那一格
是问号。真到了评测里，现象会是「模型写的 SQL 表名不对」，
排查方向会指向模型、指向提示词，而不会指向这里。

所以下面最要紧的一条是 `test_schema_doc_actually_contains_the_table_names`。
"""
from __future__ import annotations

from mcp_server import db_tools


def test_list_tables_gives_names_and_row_counts(tiny_db):
    r = db_tools.list_tables(tiny_db)
    assert r["ok"] is True
    assert {t["name"]: t["rows"] for t in r["tables"]} == {"artist": 2, "album": 3}


def test_table_schema_has_columns_pk_and_foreign_keys(tiny_db):
    r = db_tools.table_schema("album", tiny_db)
    assert r["ok"] is True
    assert r["name"] == "album"
    assert r["rows"] == 3
    assert [c["name"] for c in r["columns"]] == ["id", "artist_id", "title", "year"]
    assert [c["name"] for c in r["columns"] if c["pk"]] == ["id"]
    assert r["foreign_keys"] == [
        {"column": "artist_id", "ref_table": "artist", "ref_column": "id"}]


def test_unknown_table_lists_what_is_available(tiny_db):
    """报错要顺手告诉调用方有哪些表 —— 否则它只能靠猜。"""
    r = db_tools.table_schema("nope", tiny_db)
    assert r["ok"] is False
    assert r["err_code"] == db_tools.E_NO_SUCH_TABLE
    assert set(r["available"]) == {"artist", "album"}
    assert r["available"] == sorted(r["available"]), "表名清单应当是排好序的，方便人读"


def test_get_schema_without_a_table_returns_all(tiny_db):
    r = db_tools.get_schema(None, tiny_db)
    assert r["ok"] is True and r["n_tables"] == 2
    assert {t["name"] for t in r["tables"]} == {"artist", "album"}


def test_get_schema_with_a_table_returns_one(tiny_db):
    r = db_tools.get_schema("artist", tiny_db)
    assert r["ok"] is True and r["n_tables"] == 1
    assert r["tables"][0]["name"] == "artist"


def test_missing_db_is_a_clear_code_not_a_crash(tmp_path):
    missing = tmp_path / "nope.db"
    for label, r in (("list_tables", db_tools.list_tables(missing)),
                     ("get_schema", db_tools.get_schema(None, missing)),
                     ("table_schema", db_tools.table_schema("album", missing))):
        assert r["ok"] is False, label
        assert r["err_code"] == db_tools.E_NO_DB, (label, r)
        assert "01_build_db.py" in r["detail"], f"{label} 的错误信息要指向修复动作"


def test_get_schema_treats_its_first_argument_as_a_table_not_a_path(tiny_db):
    """签名是 `get_schema(table, db_path)`。

    把路径塞进第一个位置**不会报错**，只会安静地报「没有这张表」——
    调用方看到的现象像是数据问题，其实是参数位置用错了。这条把参数的含义钉住。

    ★ 两个参数都显式传：这条测的是**参数位置的含义**，与「默认那个库建没建」
    无关。原来的写法只传第一个参数、靠默认库兜底，于是 clone 下来（示例库
    要跑 `experiments/01_build_db.py` 才有）会先撞上 `DB_NO_DB`，
    红得跟这条要钉的东西没有关系。判据一个字没改，去掉了那层隐式依赖。
    """
    r = db_tools.get_schema(str(tiny_db), tiny_db)
    assert r["err_code"] == db_tools.E_NO_SUCH_TABLE, r
    assert db_tools.get_schema(None, tiny_db)["ok"] is True


# ---------------------------------------------------------------- ★ 关键回归
def test_schema_doc_actually_contains_the_table_names(tiny_db):
    """★ 回归测试：结构文本里必须出现**真实表名**，不能是 `?`。

    这条是因为一个真实 bug 补的：`table_schema()` 返回的键叫 `table`，
    而 `schema_doc()` 读的是 `name`，两边对不上，于是渲染出来是
    `表 ?（3 行）`。模型拿不到表名，自然写不对 SQL，而现象看起来像模型不行。

    断言写成「文中出现 artist 和 album」而不是「不出现 `?`」：
    前者直接绑定在真实需求上，后者只能证明某个具体的坏值不在。
    """
    doc = db_tools.schema_doc(tiny_db)
    assert "artist" in doc and "album" in doc, doc
    assert "表 ?" not in doc, f"表名没渲染出来：\n{doc}"


def test_schema_doc_carries_columns_types_keys_and_foreign_keys(tiny_db):
    """外键尤其重要 —— 模型最容易写错的就是 join 条件。"""
    doc = db_tools.schema_doc(tiny_db)
    for col in ("id", "artist_id", "title", "year", "name"):
        assert col in doc, f"缺列 {col}：\n{doc}"
    assert "INTEGER" in doc and "TEXT" in doc
    assert "PK" in doc, "主键标记丢了"
    assert "-> artist.id" in doc, f"外键标记丢了：\n{doc}"


def test_schema_doc_says_so_when_there_is_no_database(tmp_path):
    doc = db_tools.schema_doc(tmp_path / "nope.db")
    assert "读不到库结构" in doc
    assert "01_build_db.py" in doc


def test_schema_doc_respects_the_table_cap(tiny_db):
    """截断只该截「表」，不该按字符数截。

    断言数的是**表头行**（`表 xxx（n 行）`），不是「文中出现没出现某个词」——
    后者会误判：`max_tables=1` 时留下的那张表，它的外键注解里就写着另一张表的名字。
    第一版断言就是那么写的，于是测试红了一次。
    """
    def headers(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if ln.startswith("表 ")]

    assert len(headers(db_tools.schema_doc(tiny_db))) == 2
    assert len(headers(db_tools.schema_doc(tiny_db, max_tables=1))) == 1


def test_run_sql_goes_through_the_guardrail(tiny_db):
    """`run_sql` 不是 `execute` 的别名 —— 它必须过三层护栏。"""
    r = db_tools.run_sql("DROP TABLE artist", tiny_db)
    assert r["ok"] is False and r["blocked"] is True

    r = db_tools.run_sql("SELECT name FROM artist", tiny_db)
    assert r["ok"] is True and r["rows"] == [["A"], ["B"]]
    assert r["limit_injected"] is True
