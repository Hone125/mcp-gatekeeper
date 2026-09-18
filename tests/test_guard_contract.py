"""`guard()` 的契约测试：**永远返回 `(bool, dict)`，永远不抛异常。**

## 为什么要专门测这个

这条契约不是风格偏好，而是有具体后果的。`guard()` 跑在每个 MCP 工具函数的第一行，
它一旦抛异常，MCP SDK 会把它包成一个通用错误，调用方只能看到
`Error executing tool <name>` —— **具体是签名过期还是 scope 不够，信息全部丢失**。
降级成 `{"code": ..., "detail": ...}` 之后，拒绝原因是可被程序读取的。

契约如果没有测试强制，某个新分支漏了一个 `try` 就会悄悄退化，而且退化之后
功能测试照样全绿（因为拒绝仍然发生了，只是变成了一句没有信息量的错误）。
"""
from __future__ import annotations

import pytest

from mcp_server import auth


WEIRD_TOKENS = [
    None, "", "   ", "a", "a.b", "a.b.c", "....", "not-a-jwt",
    "x" * 10_000, "\x00\x01\x02", "中文令牌", "{}", "[]",
    "eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9..", ".....",
    "Bearer abc", "null", "None", "0", "-1",
]


@pytest.mark.parametrize("tool", sorted(auth.TOOL_SCOPES) + ["nonexistent_tool", "", "  "])
@pytest.mark.parametrize("token", WEIRD_TOKENS)
def test_guard_never_raises_and_returns_pair(tool, token):
    try:
        ok, info = auth.guard(tool, token)
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"guard({tool!r}, {token!r}) 抛了异常：{type(e).__name__}: {e}")
    assert isinstance(ok, bool)
    assert isinstance(info, dict)


@pytest.mark.parametrize("token", [None, "", "garbage"])
def test_denial_always_carries_code_and_detail(token):
    ok, info = auth.guard("run_sql", token)
    assert ok is False
    assert info.get("code") in auth.ALL_CODES
    assert isinstance(info.get("detail"), str)


def test_unknown_tool_is_reported_as_such_not_as_a_token_problem():
    """工具名写错和令牌无效是两回事，不能都报成同一个码。

    这条防的是「改工具名漏改一处」导致静默降级：如果两者都报成 NO_TOKEN，
    排查的人会一直去查令牌，而真正的问题是名字对不上。
    """
    ok, info = auth.guard("no_such_tool", auth.make_token("u", ["db:read"]))
    assert ok is False
    assert info["code"] == auth.E_UNKNOWN_TOOL


def test_guarded_decorator_consumes_token_and_shapes_denial():
    @auth.guarded("run_sql")
    def f(x: int, token: str | None = None):
        return {"ok": True, "x": x, "saw_token": token}

    denied = f(1, token=None)
    assert denied["ok"] is False and denied["denied"] is True
    assert denied["code"] == auth.E_NO_TOKEN

    good = f(1, token=auth.make_token("u", ["db:query"]))
    assert good["ok"] is True
    # token 被装饰器消费掉，不会传进业务函数 —— 需要透传令牌时不能用这个装饰器
    assert good["saw_token"] is None


def test_verify_marks_granted_scopes():
    p = auth.verify(auth.make_token("u", ["kb:read", "db:read"]), "kb:read")
    assert p["_granted"] == ["db:read", "kb:read"]
