"""名字派发器：名字表对齐、拒绝体与工具入口一致、鉴权挡在业务代码之前。

## 这一层最容易出的三种错，各配一条反证

**1. 名字表分叉。** 派发器自己维护一份工具名清单，和 `auth.TOOL_SCOPES` 慢慢
对不上 —— 表现是「某个工具在 HTTP 上神秘地返回 UNKNOWN_TOOL」。所以这里
既断言两者相等（`test_tool_names_is_exactly_the_tool_scopes_keys`），又**注入
故障**反证它会跟着 `TOOL_SCOPES` 走（把 `run_sql` 抹掉，它必须当场变成未知工具）。

**2. 鉴权被绕过。** 派发器如果先绑参数、先调业务函数，再让工具内部那一行
`_deny` 去拒 —— 看起来也对，但业务函数**已经跑过一遍了**。所以这里把
`db_tools.list_tables` 打桩成「一被调用就炸」的炸弹：无令牌、错 scope 两种
情况下它都必须**一次都没被调用**。

**3. 一刀切。** 如果所有请求都被拒，上面那些「拒绝」的断言全绿而系统根本不能用。
所以每个拒绝断言都配一条放行断言：令牌齐全时**真的走到业务代码**，而且
工具的返回值**原样透传**（用 `is` 比对同一个对象，不只是 `==`）。

参数绑定那几条同理：**「少给了一个字段」不许被报成「鉴权失败」**，
否则排错的人会跑去查令牌，而问题在请求体里。
"""
from __future__ import annotations

import inspect

import pytest

from mcp_server import auth, dispatch, server

# 与 tests/test_server_selfcheck.py 里那张表同一个思路，但**不抄那张表**：
# 参数直接从真实签名里生成。多一个工具时这里不用改，而抄一份表就会漏。
# 占位值 `""` 永远不会被业务代码看到 —— 拒绝发生在参数被使用之前，
# 这正是下面几条断言要证明的事。


def _required_args(name: str) -> dict:
    """按签名给每个必填参数塞一个占位值。"""
    out = {}
    for pname, p in dispatch.arguments_of(name).parameters.items():
        if p.default is inspect.Parameter.empty:
            out[pname] = ""
    return out


class _Bomb:
    """被调用就炸，并记下次数。用来证明「业务函数一次都没跑」。"""

    def __init__(self, what: str) -> None:
        self.what = what
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"鉴权没挡住：{self.what} 被调用了")


@pytest.fixture
def bomb_list_tables(monkeypatch):
    from mcp_server import db_tools
    bomb = _Bomb("db_tools.list_tables")
    monkeypatch.setattr(db_tools, "list_tables", bomb)
    return bomb


# ---------------------------------------------------------------- 名字表
def test_tool_names_is_exactly_the_tool_scopes_keys():
    """★ 判据：派发器认识的名字集合 == `TOOL_SCOPES` 的键集合。"""
    assert set(dispatch.tool_names()) == set(auth.TOOL_SCOPES)
    assert dispatch.tool_names() == list(auth.TOOL_SCOPES)   # 顺序也一致


def test_every_registered_name_has_a_handler_in_server():
    assert dispatch.missing_handlers() == []
    assert set(dispatch.handlers()) == set(auth.TOOL_SCOPES)


def test_catalog_pairs_every_name_with_its_declared_scope():
    cat = dispatch.catalog()
    assert [row["name"] for row in cat] == dispatch.tool_names()
    assert {row["name"]: row["scope"] for row in cat} == dict(auth.TOOL_SCOPES)
    assert all(row["scope"] in auth.SCOPES for row in cat), cat


def test_the_name_table_follows_tool_scopes_instead_of_a_second_copy(monkeypatch):
    """★ 注入故障：把 `run_sql` 从 `TOOL_SCOPES` 里抹掉。

    派发器必须**当场**不认这个名字。如果它偷偷维护了第二份清单，这里就会
    仍然认得 `run_sql` —— 那正是「两处独立维护的字符串」的静默降级。
    """
    broken = {k: v for k, v in auth.TOOL_SCOPES.items() if k != "run_sql"}
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    assert "run_sql" not in dispatch.tool_names()
    assert dispatch.missing_handlers() == []
    r = dispatch.dispatch("run_sql", {"sql": "SELECT 1", "token": "x"})
    assert r["code"] == auth.E_UNKNOWN_TOOL, r


def test_the_reverse_direction_is_a_build_error_not_a_refusal(monkeypatch):
    """反方向：登记了一个 `server.py` 里根本没有的工具。

    这不是「调用错了」，是「构建错了」—— 派发器**不许**兜底成一个拒绝体，
    否则启动自检失去意义、而调用方会以为是自己权限不够。
    """
    broken = dict(auth.TOOL_SCOPES)
    broken["a_tool_that_does_not_exist"] = "db:read"
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)

    assert dispatch.missing_handlers() == ["a_tool_that_does_not_exist"]
    with pytest.raises(RuntimeError) as e:
        dispatch.arguments_of("a_tool_that_does_not_exist")
    assert "a_tool_that_does_not_exist" in str(e.value)
    with pytest.raises(RuntimeError):
        dispatch.dispatch("a_tool_that_does_not_exist", {})


# ---------------------------------------------------------------- 未知工具
def test_an_unknown_name_is_a_structured_refusal_not_an_exception():
    r = dispatch.dispatch("no_such_tool", {"token": "whatever"})
    assert r["ok"] is False
    assert r["denied"] is True
    assert r["code"] == auth.E_UNKNOWN_TOOL
    assert r["tool"] == "no_such_tool"
    assert "no_such_tool" in r["detail"]


def test_an_unknown_name_gets_the_same_code_as_the_stdio_side():
    """未知工具的码由 `auth.guard` 给，不是派发器自己拼的字面量。

    两侧同源，才不会出现「stdio 说 UNKNOWN_TOOL、HTTP 说 NOT_FOUND」这种
    同一件事两种说法。
    """
    _ok, info = auth.guard("no_such_tool", None)
    r = dispatch.dispatch("no_such_tool", {})
    assert r["code"] == info["code"] == auth.E_UNKNOWN_TOOL


@pytest.mark.parametrize("name", sorted(auth.TOOL_SCOPES))
def test_every_tool_refuses_without_a_token(name, bomb_list_tables):
    """无令牌时**每一个**工具都拒绝，且业务代码一次都没跑。"""
    r = dispatch.dispatch(name, _required_args(name))
    assert r["ok"] is False and r["denied"] is True, r
    assert r["code"] == auth.E_NO_TOKEN, r
    assert r["tool"] == name
    assert bomb_list_tables.calls == 0


@pytest.mark.parametrize("name", sorted(auth.TOOL_SCOPES))
def test_every_tool_refuses_a_token_missing_its_own_scope(name, bomb_list_tables):
    """★ 工具级 scope 必须原样生效：派发器不许把 scope 检查短路掉。"""
    need = auth.TOOL_SCOPES[name]
    others = [s for s in auth.SCOPES if s != need]
    args = {**_required_args(name), "token": auth.make_token("u", others)}
    r = dispatch.dispatch(name, args)
    assert r["denied"] is True, r
    assert r["code"] == auth.E_SCOPE, r
    assert bomb_list_tables.calls == 0


def test_the_refusal_body_is_byte_for_byte_what_the_tool_itself_returns():
    """★ HTTP 与 stdio 两侧对同一次拒绝必须给出**同一个**返回体。

    做法：拿一个错 scope 的令牌，分别走派发器和直接调 `server.py` 里那个
    函数，两个字典必须相等。这条挡住的是「HTTP 层顺手改写了一下拒绝体」——
    那样一来两侧的行为就悄悄地不一样了。
    """
    tok = auth.make_token("u", ["kb:read"])
    for name in dispatch.tool_names():
        need = auth.TOOL_SCOPES[name]
        if need == "kb:read":
            continue
        args = {**_required_args(name), "token": tok}
        direct = getattr(server, name)(**args)
        assert dispatch.dispatch(name, args) == direct, name


# ---------------------------------------------------------------- 参数绑定
def test_a_missing_token_is_no_token_not_bad_arguments():
    """空请求体 = 「没带令牌」，不是「请求畸形」。

    这是一个刻意的取舍：`token` 缺席时补 `None` 交给 `guard()` 判 NO_TOKEN，
    于是 `POST /tools/list_tables` 带一个 `{}` 会拿到 403 而不是 400 ——
    与所有人对「没带令牌」的预期一致，而且拒绝码仍由鉴权层统一给出。
    """
    r = dispatch.dispatch("list_tables", {})
    assert r["code"] == auth.E_NO_TOKEN, r
    assert r["denied"] is True


def test_a_missing_required_argument_is_bad_arguments_not_a_refusal():
    """少给 `sql` 必须报成参数问题 —— 报成 NO_TOKEN 会把排错的人引到令牌上去。"""
    r = dispatch.dispatch("run_sql", {"token": auth.make_token("u", ["db:query"])})
    assert r["code"] == dispatch.E_BAD_ARGUMENTS, r
    assert r["denied"] is False, r
    assert "sql" in r["detail"], r


def test_an_unexpected_argument_is_bad_arguments():
    r = dispatch.dispatch("run_sql", {"sql": "SELECT 1", "token": "x", "limit": 3})
    assert r["code"] == dispatch.E_BAD_ARGUMENTS, r
    assert "limit" in r["detail"], r


def test_the_arguments_payload_must_be_an_object():
    r = dispatch.dispatch("list_tables", ["token", "x"])  # type: ignore[arg-type]
    assert r["code"] == dispatch.E_BAD_ARGUMENTS, r


def test_bad_arguments_never_echoes_an_argument_value():
    """★ 参数报错**只许回显参数名，不许回显参数值** —— `token` 就在参数里。

    两条路径都要试：多给一个键（值可能被回显）、少给一个键（`token` 已经
    在手上，更危险）。
    """
    tok = auth.make_token("u", ["db:query"])
    secret = "sk-this-is-not-a-real-key-but-looks-like-one"

    extra = dispatch.dispatch("run_sql", {"sql": "SELECT 1", "token": tok, "nope": secret})
    assert dispatch.E_BAD_ARGUMENTS == extra["code"], extra
    assert secret not in str(extra), extra
    assert tok not in str(extra), extra

    missing = dispatch.dispatch("run_sql", {"token": tok})
    assert missing["code"] == dispatch.E_BAD_ARGUMENTS, missing
    assert tok not in str(missing), missing


# ---------------------------------------------------------------- 放行那一半
def test_a_granted_call_reaches_the_business_code_and_its_value_is_passed_through(
        monkeypatch):
    """★ 反证：令牌齐全时真的走到了业务代码，且返回值**原样透传**。

    用 `is` 比对同一个对象：一旦派发器顺手把返回体包装或改写一层，
    这条立刻红 —— 而 `==` 是发现不了包装的。
    """
    from mcp_server import db_tools
    sentinel = {"ok": True, "tables": [{"name": "spy"}], "spy": True}
    calls = []

    def spy(*a, **k):
        calls.append((a, k))
        return sentinel

    monkeypatch.setattr(db_tools, "list_tables", spy)
    tok = auth.make_token("u", ["db:read"])

    r = dispatch.dispatch("list_tables", {"token": tok})
    assert r is sentinel
    assert len(calls) == 1
    assert calls[0] == ((), {})          # 参数按签名原样传下去，没多加没少加


def test_a_granted_run_sql_returns_real_rows(tiny_db, monkeypatch):
    """零打桩的放行断言：真库、真 SQL、真行。"""
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    r = dispatch.dispatch("run_sql", {
        "sql": "SELECT name FROM artist ORDER BY id",
        "token": auth.make_token("u", ["db:query"]),
    })
    assert r["ok"] is True, r
    assert r["rows"] == [["A"], ["B"]], r


def test_the_sql_guardrail_verdict_is_a_normal_result_not_a_refusal(tiny_db, monkeypatch):
    """被护栏拦下的 SQL 是**业务结论**，走的是正常返回体，不是拒绝。

    这条固定住 HTTP 层的映射口径：`blocked: True` 的响应仍然是 200 ——
    请求被正确地执行并得出了结论，传输层没有失败。
    """
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    r = dispatch.dispatch("run_sql", {
        "sql": "DROP TABLE artist",
        "token": auth.make_token("u", ["db:query"]),
    })
    assert r.get("blocked") is True, r
    assert r.get("denied") is not True, r


# ---------------------------------------------------------------- 真异常
def test_a_genuine_exception_is_not_swallowed(monkeypatch):
    """没被接住的那类异常必须冒泡 —— 派发器不许把它改写成看起来正常的返回体。

    上游两种处置都需要它是异常：HTTP 映射成 500、`run_many` 收成异常计数。
    """
    from mcp_server import db_tools

    def boom(*a, **k):
        raise ValueError("数据库文件不见了")

    monkeypatch.setattr(db_tools, "list_tables", boom)
    with pytest.raises(ValueError, match="数据库文件不见了"):
        dispatch.dispatch("list_tables", {"token": auth.make_token("u", ["db:read"])})
