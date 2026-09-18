"""工具级鉴权：HS256 令牌的签发、校验，以及按工具粒度下沉的 scope 检查。

## 设计要点

**1. scope 是「资源:动作」，且按工具粒度绑定。**

    db:read   → 看有哪些表、看表结构
    db:query  → 执行 SQL、用自然语言问数据库
    kb:read   → 检索文本、取原文片段

粒度绑在**工具**上而不是绑在「客户端角色」上，是因为角色会随着需求膨胀成一张
没人看得懂的大表，而工具名是有限的、可枚举的。`TOOL_SCOPES` 就是那张唯一的映射表。

**2. 失败一律降级成数据结构，不抛异常。**

`guard()` 的契约是**永远返回 `(bool, dict)`**，任何情况下都不往外抛。
原因是它跑在 MCP 工具函数的第一行：一旦抛出去，SDK 会把它包成一个通用错误，
调用方只能看到「工具执行失败」，**具体是签名错还是 scope 不够全部丢失**。
降级成 `{"code": ..., "detail": ...}` 之后，拒绝原因是可以被程序读的。
`tests/test_guard_contract.py` 用模糊输入强制这条契约。

**3. 校验顺序：签名 → issuer → audience → 过期 → scope。**

顺序不是随意的：签名必须最先验，否则后面读到的 claim 全都不可信。
PyJWT 的 `decode()` 一次性完成前四步（失败时抛不同的异常子类），scope 在最后单独查。

## ⚠️ 关于内置的开发用密钥

`MCP_JWT_SECRET` 没设置时会用一个**公开写在源码里的默认密钥**。这意味着任何人都能
用它伪造出一个合法令牌。这在本地演示和测试里是必要的（否则 clone 下来第一步就跑不动），
但在任何真实场景里都是致命的。

两道防线：
- 服务端启动时如果发现用的是默认密钥，会打印醒目告警（见 `warn_if_dev_secret()`）。
- 设置 `MCP_JWT_STRICT=1` 后，`verify()` 会**直接拒绝**使用默认密钥，强制显式配置。
"""
from __future__ import annotations

import os
import time
from functools import wraps

import jwt

ALGO = "HS256"

# 长度超过 32 字节：HS256 的密钥短于 32 字节时 PyJWT 会发 InsecureKeyLengthWarning。
# 这个默认值本身无所谓长短（它已经公开了），但让仓库跑起来零告警更省事。
DEV_SECRET = "dev-only-insecure-secret-change-me-0123456789abcdef"

# ---------------------------------------------------------------- 工具 → scope
# 这是唯一的映射表。新增工具时必须同时在这里登记，否则 `guard()` 会返回
# `UNKNOWN_TOOL`。`mcp_server/server.py` 启动时会断言「实际注册的工具集合」
# 与「这张表的键集合」完全一致，防止改一处漏一处导致的静默降级。
TOOL_SCOPES: dict[str, str] = {
    "list_tables": "db:read",
    "get_schema": "db:read",
    "run_sql": "db:query",
    "ask_database": "db:query",
    "search_passages": "kb:read",
    "get_passage": "kb:read",
}

# scope 词表。只有这三个，没有通配符、没有层级继承。
SCOPES: tuple[str, ...] = ("db:read", "db:query", "kb:read")


def issuer() -> str:
    return os.environ.get("MCP_JWT_ISSUER") or "mcp-guarded-toolkit"


def audience() -> str:
    return os.environ.get("MCP_JWT_AUDIENCE") or "mcp-client"


def secret() -> str:
    return os.environ.get("MCP_JWT_SECRET") or DEV_SECRET


def using_dev_secret() -> bool:
    return secret() == DEV_SECRET


def strict_mode() -> bool:
    return (os.environ.get("MCP_JWT_STRICT") or "").strip() in ("1", "true", "yes")


def warn_if_dev_secret() -> str | None:
    """用的是默认密钥就返回一句告警文案，否则返回 None。由服务端启动时调用。"""
    if not using_dev_secret():
        return None
    return (
        "⚠️  正在使用内置的开发用签名密钥（MCP_JWT_SECRET 未设置）。\n"
        "    任何人都能用这个公开的密钥伪造出合法令牌 —— 仅限本地演示与测试。\n"
        "    设置环境变量 MCP_JWT_SECRET 换成一个高熵随机值，或设 MCP_JWT_STRICT=1 强制拒绝。"
    )


# ---------------------------------------------------------------- 错误
class AuthError(Exception):
    """内部异常。**只在 `verify()` 层抛出**，`guard()` 会把它接住并降级成字典。"""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# 错误码全集。报告与测试都引用这些字符串。
E_EXPIRED = "TOKEN_EXPIRED"
E_BAD_AUDIENCE = "BAD_AUDIENCE"
E_BAD_ISSUER = "BAD_ISSUER"
E_BAD_SIGNATURE = "BAD_SIGNATURE"
E_INVALID = "INVALID_TOKEN"
E_SCOPE = "INSUFFICIENT_SCOPE"
E_NO_TOKEN = "NO_TOKEN"
E_UNKNOWN_TOOL = "UNKNOWN_TOOL"
E_DEV_SECRET = "DEV_SECRET_REFUSED"

ALL_CODES = (E_EXPIRED, E_BAD_AUDIENCE, E_BAD_ISSUER, E_BAD_SIGNATURE, E_INVALID,
             E_SCOPE, E_NO_TOKEN, E_UNKNOWN_TOOL, E_DEV_SECRET)


# ---------------------------------------------------------------- 签发
def make_token(sub: str = "demo-user", scopes: list[str] | None = None, ttl: int = 3600,
               issuer_: str | None = None, audience_: str | None = None,
               secret_: str | None = None, algo: str = ALGO, iat_offset: int = 0) -> str:
    """签一个令牌。

    `ttl` 为负数即得到「已过期」的令牌；`iat_offset` 用来整体平移签发时间。
    这两个参数是给测试造负例用的，生产调用不该传。
    """
    now = int(time.time()) + iat_offset
    payload = {
        "sub": sub,
        "iss": issuer_ if issuer_ is not None else issuer(),
        "aud": audience_ if audience_ is not None else audience(),
        "iat": now,
        "exp": now + ttl,
        "scope": " ".join(scopes or []),
    }
    return jwt.encode(payload, secret_ if secret_ is not None else secret(), algorithm=algo)


# ---------------------------------------------------------------- 校验
def verify(token: str, required_scope: str) -> dict:
    """校验令牌并检查 scope。通过返回 payload，失败抛 `AuthError`。

    `algorithms=[ALGO]` 是白名单，不是「建议」—— 它挡掉 `alg: none` 和算法混淆
    这类攻击。少了这个参数，攻击者可以把头部改成 `{"alg":"none"}` 并去掉签名，
    很多库会照单全收。
    """
    if strict_mode() and using_dev_secret():
        raise AuthError(E_DEV_SECRET,
                        "MCP_JWT_STRICT=1 时不允许使用内置的开发用密钥，请显式设置 MCP_JWT_SECRET")

    try:
        payload = jwt.decode(
            token, secret(), algorithms=[ALGO],
            audience=audience(), issuer=issuer(),
            # sub/exp/iss/aud 必须存在。scope 不在必填列表里：一个只做健康检查的
            # 客户端不该被迫带 scope，缺省按空集处理。
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError(E_EXPIRED, str(e)) from e
    except jwt.InvalidAudienceError as e:
        raise AuthError(E_BAD_AUDIENCE, str(e)) from e
    except jwt.InvalidIssuerError as e:
        raise AuthError(E_BAD_ISSUER, str(e)) from e
    except jwt.InvalidSignatureError as e:
        raise AuthError(E_BAD_SIGNATURE, str(e)) from e
    except Exception as e:  # noqa: BLE001 —— 兜底，保证不会有异常逃出 verify()
        raise AuthError(E_INVALID, f"{type(e).__name__}: {e}") from e

    granted = set((payload.get("scope") or "").split())
    if required_scope not in granted:
        raise AuthError(E_SCOPE, f"需要 {required_scope}，实际持有 {sorted(granted)}")

    # 注入解析结果供审计。以 _ 开头，不会和标准 claim 撞名。
    payload["_granted"] = sorted(granted)
    return payload


def guard(tool_name: str, token: str | None) -> tuple[bool, dict]:
    """工具入口的强制检查。**永远返回 `(bool, dict)`，永不抛异常。**

    返回 `(True, payload)` 或 `(False, {"code":..., "detail":...})`。
    """
    try:
        need = TOOL_SCOPES.get(tool_name)
        if need is None:
            return False, {"code": E_UNKNOWN_TOOL, "detail": f"未登记的工具 {tool_name!r}"}
        if not token:
            return False, {"code": E_NO_TOKEN, "detail": "缺少 token"}
        return True, verify(token, need)
    except AuthError as e:
        return False, {"code": e.code, "detail": e.detail}
    except Exception as e:  # noqa: BLE001
        # 兜底。契约是「绝不抛异常」，所以这里必须有个什么都接的 except ——
        # 万一 verify 以后被改出别的异常类型，也只会变成一个拒绝，而不是
        # 让整个工具调用崩成 SDK 的通用错误。
        return False, {"code": E_INVALID, "detail": f"{type(e).__name__}: {e}"}


def guarded(tool_name: str):
    """装饰器形态的 `guard`：从关键字参数里取 `token` 并强制校验。

    注意它会把 `token` **从参数里消费掉**，被包装的函数看不到也拿不到 token。
    需要把令牌透传给下游时用不了这个装饰器，得在函数体里显式调 `guard()`。
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, token: str | None = None, **kwargs):
            ok, info = guard(tool_name, token)
            if not ok:
                return {"ok": False, "denied": True, **info}
            return fn(*args, **kwargs)

        return wrapper

    return deco
