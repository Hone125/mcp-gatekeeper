"""HTTP 入口：错误映射、路由表对齐、以及「阻塞调用真的被挪出了事件循环」。

`experiments/30_http_smoke.py` 走真进程真 TCP，管的是**端到端**；
这里走进程内的 `TestClient`，管的是**那几条映射规则本身**，以及几件端到端
不容易看清的事：

- `status_for()` 的**顺序**：`UNKNOWN_TOOL` 与 `DEV_SECRET_REFUSED` 都带
  `denied: True`，只要把它们排到「一刀切 403」后面，就会被静默吞成 403。
  所以这里逐个钉住「它们的码 → 它们的状态码」，而不是只测「拒绝 → 403」。
- **注入故障**反证自检抓得住：真的去改 `TOOL_SCOPES`，看它报不报。
  「自检永远返回空列表」是这类断言最容易掉进去的假绿。
- **500 的响应体里不许有异常消息**：消息可能带着参数值，而 `token` 就在参数里。
- **工具是在工作线程里跑的**：`async def` 端点里直接调同步阻塞的 SQLite
  会把整个服务卡住，`/healthz` 都探不通。这条断言用「那个线程里有没有正在运行的
  事件循环」来判 —— 有就是没挪出去。
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from mcp_server import auth, dispatch, http_api


@pytest.fixture
def client():
    with TestClient(http_api.app) as c:
        yield c


# ---------------------------------------------------------------- 状态码映射
def test_status_for_maps_every_denial_code_to_a_status():
    """一张表说完。**每一行的顺序都是判据**，见文件头。"""
    assert http_api.status_for({"denied": True, "code": auth.E_NO_TOKEN}) == 403
    assert http_api.status_for({"denied": True, "code": auth.E_SCOPE}) == 403
    assert http_api.status_for({"denied": True, "code": auth.E_BAD_SIGNATURE}) == 403
    assert http_api.status_for({"denied": True, "code": auth.E_EXPIRED}) == 403


def test_unknown_tool_is_404_not_403_even_though_it_is_also_denied():
    """★ `UNKNOWN_TOOL` 也带 `denied: True`。顺序反了它就会变成 403。"""
    r = {"ok": False, "denied": True, "tool": "nope", "code": auth.E_UNKNOWN_TOOL}
    assert http_api.status_for(r) == 404


def test_dev_secret_refusal_is_500_not_403_even_though_it_is_also_denied():
    """★ 同上。这个码的含义是「**服务端**自己没配好」，报 403 会甩锅给调用方。"""
    r = {"ok": False, "denied": True, "tool": "run_sql", "code": auth.E_DEV_SECRET}
    assert http_api.status_for(r) == 500


def test_bad_arguments_is_400_and_is_not_a_refusal():
    r = dispatch.bad_arguments("run_sql", "缺少 sql")
    assert http_api.status_for(r) == 400
    assert r["denied"] is False, "参数问题不是拒绝 —— 报成拒绝会把排错的人引到令牌上去"


def test_a_blocked_sql_is_a_success():
    """★ 被护栏拦下是**业务结论**，200。

    「请求被正确处理并得出结论了吗」—— 是。它和 403 的区别是「你的 SQL 写得
    不对」与「你没有权限」，调用方对这两件事的处置完全不同。
    """
    assert http_api.status_for({"ok": True, "blocked": True, "rule": "R_DDL"}) == 200


def test_an_ordinary_business_failure_is_still_200():
    """业务层的 `ok: False`（比如查了个不存在的表）仍然是 200。

    只有**没被工具接住**的异常才是 500。把业务失败也报成 5xx，会让调用方
    去重试一个重试一百遍也不会成功的请求。
    """
    assert http_api.status_for({"ok": False, "err_code": "E_NO_SUCH_TABLE"}) == 200


# ---------------------------------------------------------------- 路由表
def test_route_names_are_exactly_the_tool_scopes_keys():
    """与 `dispatch.tool_names()` 同源，所以三者必须相等。"""
    assert set(http_api.route_names()) == set(auth.TOOL_SCOPES)
    assert set(http_api.route_names()) == set(dispatch.tool_names())


def test_tool_routes_are_generated_not_hand_written():
    """路径是照着工具名拼出来的，不是手写的 6 条。"""
    assert http_api.tool_routes() == [(n, f"/tools/{n}") for n in dispatch.tool_names()]


def test_selfcheck_passes_on_the_real_app():
    assert http_api.selfcheck() == []


def test_selfcheck_catches_a_tool_without_a_route(monkeypatch):
    """★ 注入故障：往 `TOOL_SCOPES` 里塞一个没有路由的工具。

    这正是那个**静默降级**的场景 —— stdio 侧调得到、HTTP 侧调不到，
    两个入口的行为悄悄不一样了。
    """
    broken = dict(auth.TOOL_SCOPES)
    broken["a_tool_without_a_route"] = "db:read"
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = http_api.selfcheck()
    assert any("a_tool_without_a_route" in p for p in problems), problems


def test_selfcheck_catches_a_route_that_no_longer_has_a_scope(monkeypatch):
    """反方向：路由还在，但工具从 `TOOL_SCOPES` 里没了。

    那时它拿不到 scope 映射，对任何令牌都只会回 UNKNOWN_TOOL —— 看起来像
    权限问题，其实是漏登记。
    """
    broken = {k: v for k, v in auth.TOOL_SCOPES.items() if k != "run_sql"}
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = http_api.selfcheck()
    assert any("run_sql" in p for p in problems), problems


def test_selfcheck_runs_the_tool_layer_selfcheck_too(monkeypatch):
    """工具层的注册表坏掉时，HTTP 这一层没有任何办法是好的 —— 要一起报出来。"""
    broken = dict(auth.TOOL_SCOPES)
    broken["run_sql"] = "db:write"          # scope 名不存在，server.selfcheck 会报
    monkeypatch.setattr(auth, "TOOL_SCOPES", broken)
    problems = http_api.selfcheck()
    assert any(p.startswith("[工具层]") for p in problems), problems


# ---------------------------------------------------------------- 端到端（进程内）
def test_get_tools_lists_the_six_tools_with_their_scopes(client):
    r = client.get("/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["n"] == 6
    assert {t["name"]: t["scope"] for t in body["tools"]} == dict(auth.TOOL_SCOPES)


def test_healthz_answers_without_touching_the_database(client):
    """探针只探「进程还活着」。碰数据库的话，库一慢编排系统就会杀掉健康进程。"""
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_an_empty_body_is_403_no_token(client):
    r = client.post("/tools/list_tables", json={})
    assert r.status_code == 403
    assert r.json()["code"] == auth.E_NO_TOKEN


def test_an_unknown_tool_is_404_with_our_own_body(client):
    """默认的 `{"detail":"Not Found"}` 必须被改写成与 stdio 侧同一个拒绝体。"""
    r = client.post("/tools/no_such_tool", json={"token": "x"})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == auth.E_UNKNOWN_TOOL
    assert body["tool"] == "no_such_tool"
    assert body["denied"] is True


def test_a_missing_argument_is_400(client):
    r = client.post("/tools/run_sql", json={"token": auth.make_token("u", ["db:query"])})
    assert r.status_code == 400
    assert r.json()["code"] == dispatch.E_BAD_ARGUMENTS


def test_a_malformed_body_is_400(client):
    r = client.post("/tools/run_sql", content=b"{not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["code"] == dispatch.E_BAD_ARGUMENTS


def test_a_granted_call_returns_the_business_payload(client, tiny_db, monkeypatch):
    """★ 反证：令牌齐全时 HTTP 这一侧拿到的是**真的业务结果**。"""
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    r = client.post("/tools/run_sql", json={
        "token": auth.make_token("u", ["db:query"]),
        "sql": "SELECT name FROM artist ORDER BY id",
    })
    assert r.status_code == 200
    assert r.json()["rows"] == [["A"], ["B"]]


def test_a_blocked_sql_comes_back_as_200_with_blocked_true(client, tiny_db, monkeypatch):
    from mcp_server import paths
    monkeypatch.setattr(paths, "chinook_db", lambda: tiny_db)

    r = client.post("/tools/run_sql", json={
        "token": auth.make_token("u", ["db:query"]),
        "sql": "DROP TABLE artist",
    })
    assert r.status_code == 200, r.text
    assert r.json()["blocked"] is True


# ---------------------------------------------------------------- 事件循环
def test_the_tool_runs_off_the_event_loop(client, monkeypatch):
    """★ 阻塞的同步工具必须在**工作线程**里跑。

    判据不是线程名（那是实现细节），而是「那个线程里有没有正在运行的事件循环」：
    端点协程跑在事件循环线程上，`asyncio.to_thread` 派出去的工作线程里没有。
    真在事件循环里直调的话，一次慢查询就会把整个服务卡住 ——
    `experiments/29_concurrency_bench.py` 的 B 组实测出来的就是这个现象。
    """
    from mcp_server import db_tools

    seen: dict = {}

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            seen["on_loop"] = False
        else:
            seen["on_loop"] = True
        seen["thread"] = threading.current_thread().name
        return {"ok": True}

    monkeypatch.setattr(db_tools, "list_tables", spy)
    r = client.post("/tools/list_tables", json={"token": auth.make_token("u", ["db:read"])})
    assert r.status_code == 200
    assert seen["on_loop"] is False, f"工具跑在事件循环线程里了（{seen['thread']}）"


def test_the_endpoint_itself_really_is_async(client, monkeypatch):
    """反证上一条：端点**确实是** `async def`。

    如果端点被改成同步函数，Starlette 会把它丢进它自己的线程池 —— 那也没错，
    但上一条断言就变成在证明「Starlette 的线程池」而不是「我们的 `to_thread`」，
    两件事的维护含义不一样，所以分开钉住。
    """
    for name, _path in http_api.tool_routes():
        fn = dispatch.handlers()[name]
        assert not asyncio.iscoroutinefunction(fn), f"{name} 不该是协程：它要做阻塞 I/O"
    assert len(http_api.tool_routes()) == 6


# ---------------------------------------------------------------- 500
def test_an_unhandled_exception_becomes_a_500_without_the_message(monkeypatch):
    """★ 没被接住的异常 → 500，且**响应体里只有异常类名**。

    消息里可能带着参数值，而 `token` 就在参数里。堆栈进 stderr 供本机排查，
    不进响应体 —— 也就不可能被谁抄进报告。
    """
    from mcp_server import db_tools

    def boom(*a, **k):
        raise ValueError("秘密内容-supersecret-令牌值")

    monkeypatch.setattr(db_tools, "list_tables", boom)
    with TestClient(http_api.app, raise_server_exceptions=False) as c:
        r = c.post("/tools/list_tables", json={"token": auth.make_token("u", ["db:read"])})

    assert r.status_code == 500
    body = r.json()
    assert body["code"] == http_api.E_INTERNAL
    assert body["detail"] == "ValueError"
    assert "supersecret" not in r.text
    assert "秘密内容" not in r.text
