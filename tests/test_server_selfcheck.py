"""服务端：启动自检的有效性，以及每个工具的鉴权入口。

## 两类断言，缺一不可

**「拒绝」的断言**容易写，也容易骗人：如果一个 bug 让所有工具对任何令牌都拒绝，
那么所有「拒绝成功」的测试全绿，而服务其实完全不能用。
所以下面**同时**断言「应该放行的那一半也能通过」——
`test_allowed_token_actually_reaches_the_business_code` 就是干这个的。

**「自检通过」的断言**同样容易骗人：如果 `selfcheck()` 因为写错而永远返回空列表，
`assert selfcheck() == []` 会一直绿，而它本该发现的问题一个也发现不了。
所以下面用**注入故障**的方式反证它抓得住：真的去改 `TOOL_SCOPES`，
看它是不是真的报出来。这些都是常规测试，不是「临时改一下再改回来」。
"""
from __future__ import annotations

import pytest

from mcp_server import auth, server


def _call(tool: str, token: str, **kw):
    """按工具名调用真实注册的那个函数对象。"""
    fn = getattr(server, tool)
    return fn(token=token, **kw)


# ---------------------------------------------------------------- 自检本身
def test_selfcheck_passes_on_the_real_server():
    assert server.selfcheck() == []
    assert len(server.registered_tool_names()) == 6


def test_selfcheck_catches_a_registered_tool_missing_from_tool_scopes(monkeypatch):
    """注入故障：把一个工具从 TOOL_SCOPES 里抹掉。

    这正是那个**静默降级**的场景 —— 工具实际能调，但没有 scope 映射，
    于是对任何令牌都返回 UNKNOWN_TOOL，看起来像权限问题其实是漏登记。
    """
    broken = {k: v for k, v in auth.TOOL_SCOPES.items() if k != "run_sql"}
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = server.selfcheck()
    assert any("run_sql" in p and "UNKNOWN_TOOL" in p for p in problems), problems


def test_selfcheck_catches_a_declared_tool_that_is_not_registered(monkeypatch):
    """反方向：TOOL_SCOPES 里登记了一个根本不存在的工具。"""
    broken = dict(auth.TOOL_SCOPES)
    broken["a_tool_that_does_not_exist"] = "db:read"
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = server.selfcheck()
    assert any("a_tool_that_does_not_exist" in p for p in problems), problems


def test_selfcheck_catches_an_undeclared_scope(monkeypatch):
    """scope 名写错（比如 db:write）时必须报出来，而不是静默通过。"""
    broken = dict(auth.TOOL_SCOPES)
    broken["run_sql"] = "db:write"
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = server.selfcheck()
    assert any("db:write" in p for p in problems), problems


# ---------------------------------------------------------------- 鉴权入口
TOOLS_AND_ARGS = [
    ("list_tables", {}),
    ("get_schema", {}),
    ("run_sql", {"sql": "SELECT 1"}),
    ("ask_database", {"question": "一共有多少张专辑"}),
    ("search_passages", {"query": "茶"}),
    ("get_passage", {"doc_id": "1"}),
]


@pytest.mark.parametrize("tool,kwargs", TOOLS_AND_ARGS)
def test_every_tool_denies_without_a_token(tool, kwargs):
    """无令牌时**每一个**工具都必须拒绝，而且拒绝要带 denied 标记。

    带标记的意义：调用方能一眼区分「被拒绝」和「查了但没结果」。
    少了它，一个空的返回体和一次拒绝长得一模一样。
    """
    r = _call(tool, "", **kwargs)
    assert r["ok"] is False, r
    assert r["denied"] is True, r
    assert r["code"] == auth.E_NO_TOKEN, r
    assert r["tool"] == tool


@pytest.mark.parametrize("tool,kwargs", TOOLS_AND_ARGS)
def test_every_tool_denies_on_a_forged_token(tool, kwargs):
    """签名不对的令牌同样是拒绝，而且错误码要具体到 BAD_SIGNATURE。"""
    forged = auth.make_token("attacker", list(auth.SCOPES),
                             secret_="a-different-secret-of-sufficient-length-0000")
    r = _call(tool, forged, **kwargs)
    assert r["denied"] is True and r["code"] == auth.E_BAD_SIGNATURE, r


@pytest.mark.parametrize("tool,kwargs", TOOLS_AND_ARGS)
def test_every_tool_refuses_a_token_with_the_wrong_scope(tool, kwargs):
    """★ 工具级 scope 下放：拿着一把钥匙不能开所有的门。

    给每个工具发一个**只差它自己那个 scope** 的令牌，必须被拒。
    这条是「工具级 scope」这个设计有没有真的生效的唯一证据。
    """
    need = auth.TOOL_SCOPES[tool]
    others = [s for s in auth.SCOPES if s != need]
    r = _call(tool, auth.make_token("u", others), **kwargs)
    assert r["denied"] is True, r
    assert r["code"] == auth.E_SCOPE, r


def test_allowed_token_actually_reaches_the_business_code(tiny_db, monkeypatch):
    """★ 反证：令牌齐全时**真的走到了业务代码**，不是被一刀切拒掉。

    没有这一条，上面所有「拒绝」的断言都可以被一个「永远拒绝」的 bug 满足。
    """
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    tok = auth.make_token("u", ["db:read"])
    r = _call("list_tables", tok)
    assert r.get("denied") is not True, r
    assert r["ok"] is True, r
    assert {t["name"] for t in r["tables"]} == {"artist", "album"}, r


def test_allowed_token_reaches_run_sql_and_gets_real_rows(tiny_db, monkeypatch):
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    tok = auth.make_token("u", ["db:query"])
    r = _call("run_sql", tok, sql="SELECT name FROM artist ORDER BY id")
    assert r["ok"] is True, r
    assert r["rows"] == [["A"], ["B"]], r


def test_denied_tools_do_not_execute_the_sql_at_all(tiny_db, monkeypatch):
    """★ 鉴权必须挡在业务代码**之前**。

    做法：把一个只读库的路径指给 run_sql，同时给一个没有 db:query 的令牌。
    如果鉴权在业务代码之后，这里会看到「SQL 跑了但结果被扣下」的痕迹；
    挡在前面的话，返回体里连 `sql` 字段都不会有。
    """
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    tok = auth.make_token("u", ["kb:read"])
    r = _call("run_sql", tok, sql="SELECT name FROM artist")
    assert r["denied"] is True, r
    assert "rows" not in r, f"拒绝的返回体里不该有查询结果：{r}"
    assert r.get("sql", "") == "", f"拒绝的返回体里不该有执行过的 SQL：{r}"


def test_a_broken_token_never_crashes_the_tool():
    """`guard()` 的契约是永不抛异常。工具入口靠它兜底，所以这里连奇怪输入一起试。"""
    for bad in [None, 0, 1, [], {}, "a.b.c", "x" * 5000, "eyJhbGciOiJub25lIn0.."]:
        r = _call("list_tables", bad)  # type: ignore[arg-type]
        assert isinstance(r, dict) and r["denied"] is True, (bad, r)
        assert r["code"] in auth.ALL_CODES, (bad, r)
