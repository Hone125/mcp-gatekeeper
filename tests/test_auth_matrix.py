"""鉴权契约测试。

分两层：

- **单元层**：直接调 `verify()`，断言**错误码足够具体**（签名错不能报成泛泛的「无效令牌」）。
- **产物层**：跑 `experiments/10_auth_test.py`，断言它退出码 0，并读它落盘的
  `reports/auth_results.json` 断言**「应放行」那一半也是全过的**。

第二层是重点。只统计「拒绝成功」的鉴权测试是一种假安全：把所有请求一刀切拒掉，
拒绝率就是 100%，测试全绿，而系统根本没法用。所以这里必须同时断言放行组。
"""
from __future__ import annotations

import json

import pytest

from mcp_server import auth, paths


# ---------------------------------------------------------------- 单元层
def test_valid_token_has_expected_claims():
    tok = auth.make_token("u1", ["db:read", "db:query"])
    p = auth.verify(tok, "db:read")
    assert p["sub"] == "u1"
    assert p["_granted"] == ["db:query", "db:read"]


def test_expired_reports_specific_code():
    with pytest.raises(auth.AuthError) as e:
        auth.verify(auth.make_token("u", ["db:read"], ttl=-10), "db:read")
    assert e.value.code == auth.E_EXPIRED


def test_bad_signature_reports_specific_code():
    forged = auth.make_token("u", ["db:read"],
                             secret_="a-different-secret-of-sufficient-length-0000")
    with pytest.raises(auth.AuthError) as e:
        auth.verify(forged, "db:read")
    assert e.value.code == auth.E_BAD_SIGNATURE


def test_bad_audience_and_issuer_are_distinguished():
    with pytest.raises(auth.AuthError) as e1:
        auth.verify(auth.make_token("u", ["db:read"], audience_="someone-else"), "db:read")
    assert e1.value.code == auth.E_BAD_AUDIENCE

    with pytest.raises(auth.AuthError) as e2:
        auth.verify(auth.make_token("u", ["db:read"], issuer_="evil"), "db:read")
    assert e2.value.code == auth.E_BAD_ISSUER


def test_scope_matching_is_exact_no_wildcards():
    """scope 没有通配符、没有层级继承 —— 这是刻意的，必须被测试固定住。"""
    tok = auth.make_token("u", ["db:read"])
    with pytest.raises(auth.AuthError) as e:
        auth.verify(tok, "db:query")
    assert e.value.code == auth.E_SCOPE
    # 大小写敏感
    with pytest.raises(auth.AuthError):
        auth.verify(tok, "DB:READ")
    # db:* 不是通配
    with pytest.raises(auth.AuthError):
        auth.verify(auth.make_token("u", ["db:*"]), "db:read")


def test_alg_none_attack_is_rejected():
    """去掉签名的 `alg: none` 令牌必须被拒。`algorithms=[...]` 白名单就是为它准备的。"""
    import jwt
    header = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    body = jwt.utils.base64url_encode(
        json.dumps({"sub": "u", "iss": auth.issuer(), "aud": auth.audience(),
                    "exp": 9999999999, "scope": "db:read"}).encode()).decode()
    with pytest.raises(auth.AuthError) as e:
        auth.verify(f"{header}.{body}.", "db:read")
    assert e.value.code in (auth.E_INVALID, auth.E_BAD_SIGNATURE)


@pytest.mark.parametrize("payload_patch", [
    {"exp": None}, {"iss": None}, {"aud": None}, {"sub": None},
])
def test_missing_required_claim_is_rejected(payload_patch):
    import jwt
    payload = {"sub": "u", "iss": auth.issuer(), "aud": auth.audience(),
               "exp": 9999999999, "scope": "db:read"}
    payload.update({k: v for k, v in payload_patch.items()})
    payload = {k: v for k, v in payload.items() if v is not None}
    tok = jwt.encode(payload, auth.secret(), algorithm=auth.ALGO)
    with pytest.raises(auth.AuthError) as e:
        auth.verify(tok, "db:read")
    assert e.value.code == auth.E_INVALID


def test_every_tool_has_a_scope_and_every_scope_is_used():
    """映射表不能有孤儿：既不能有工具没登记，也不能有登记了却没人用的 scope。"""
    used = set(auth.TOOL_SCOPES.values())
    assert used <= set(auth.SCOPES), f"用到了未声明的 scope：{used - set(auth.SCOPES)}"
    assert set(auth.SCOPES) == used, f"声明了但没人用的 scope：{set(auth.SCOPES) - used}"


def test_strict_mode_refuses_dev_secret(monkeypatch):
    monkeypatch.delenv("MCP_JWT_SECRET", raising=False)
    monkeypatch.setenv("MCP_JWT_STRICT", "1")
    tok = auth.make_token("u", ["db:read"])
    with pytest.raises(auth.AuthError) as e:
        auth.verify(tok, "db:read")
    assert e.value.code == auth.E_DEV_SECRET


# ---------------------------------------------------------------- 产物层
def test_auth_experiment_passes_and_covers_both_sides(run_py):
    p = run_py("experiments/10_auth_test.py")
    assert p.returncode == 0, p.out

    data = json.loads((paths.reports_dir() / "auth_results.json").read_text(encoding="utf-8"))
    # 「应拒」全过
    assert data["deny_pass"] == data["deny_cases"], data
    # ★「应放行」也必须全过 —— 否则就是一刀切全拒的假安全
    assert data["allow_cases"] > 0, "一条放行用例都没有，这个测试没有意义"
    assert data["allow_pass"] == data["allow_cases"], data
    assert data["n_pass"] == data["n_cases"]
