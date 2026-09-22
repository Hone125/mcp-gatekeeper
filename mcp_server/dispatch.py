"""按名字派发：把一次「工具名 + 参数」的调用落到 `server.py` 里那 6 个函数上。

## 为什么要有这一层，而不是让 HTTP 直接调 `server`

`server.py` 里那 6 个函数是**被 `@mcp.tool()` 装饰过的模块级函数**，直接
`import` 进来就能调。HTTP 层当然可以挨个写 6 个分支，但那样会多出**第二处
工具名清单** —— 而这份仓库已经吃过一次「两处独立维护的字符串，漏一处是静默
降级」（见 `server.py` 文件头「为什么要有启动自检」）。

所以这里的规矩是：

1. **工具名只有一个真值源**：`auth.TOOL_SCOPES` 的键。`tool_names()` 从它生成，
   不抄第二遍。往 HTTP 上挂几条路由也是照着它循环挂的。
2. **不复制业务逻辑**。`handlers()` 用 `getattr(server, 名字)` 拿到那个函数本身，
   调用就是普通函数调用。任何在 `server.py` 里改的东西（改 SQL、改返回体）
   自动在这里生效 —— 没有一条「HTTP 版实现」需要跟着改。
3. **不绕过鉴权**。派发器**不做鉴权**，也**不许做**：6 个工具的第一行是
   `_deny(...)`，鉴权在那一行。HTTP 层要是自己判一次，就变成了「两处判鉴权」，
   而漏判一处的后果比两处都判更糟 —— 见 `mcp_server/auth.py` 里 `guard()` 的
   契约（永远返回 `(bool, dict)`，永不抛异常）。

## 拒绝与失败长什么样

派发器的返回值**就是工具的返回值**，所以拒绝体沿用 `server._deny` 的形状：

    {"ok": False, "denied": True, "tool": 名字, "code": ..., "detail": ...}

`code` 是 `auth.ALL_CODES` 里那些字面量（`NO_TOKEN` / `INSUFFICIENT_SCOPE` /
`TOKEN_EXPIRED` / …），未知工具借 `auth.guard(名字, None)` 拿 `UNKNOWN_TOOL` ——
**顺序与工具入口完全一致**：先查「这个工具登不登记过」，再查「有没有令牌」。

参数绑不上（缺必填的、多给了不认识的）返回 `code = "BAD_ARGUMENTS"`。这个码
**不在 `auth.ALL_CODES` 里**，因为它是传输层的事，不是鉴权的事：`auth` 管
「你能不能调」，`dispatch` 管「你这条请求是不是这个工具的合法调用」。
**`detail` 只回显参数名与 Python 的绑定错误，不回显任何参数值** ——
`token` 就在参数里，回显它会把它抄进日志与报告。

## 真异常让它冒泡

工具的**业务失败**早就被降级成返回体字段了（`blocked` / `ok` / `error`），
所以从这里冒出去的异常是**没被接住的那一类**（比如参数类型不对、
数据库文件缺失时某个函数没兜住）。那类问题不该被这里悄悄改写成一个
看起来正常的返回体：HTTP 层会把它映射成 500，`concurrency.run_many` 会把它
收成一个异常计数 —— 两种处置都需要它是异常。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

from mcp_server import auth, server

# 参数绑不上时用的码。**刻意不放进 `auth.ALL_CODES`**：见文件头最后两节。
E_BAD_ARGUMENTS = "BAD_ARGUMENTS"


def tool_names() -> list[str]:
    """工具名清单。**唯一真值源是 `auth.TOOL_SCOPES` 的键**，保持登记顺序。

    不排序：登记顺序就是 `server.py` 里的注册顺序，报告与 `GET /tools` 按它
    输出，读的人能一眼对上。要排序的调用方自己 `sorted()`。
    """
    return list(auth.TOOL_SCOPES)


def handlers() -> dict[str, Callable[..., dict]]:
    """名字 → `server.py` 里那个函数。只收 `TOOL_SCOPES` 登记过的名字。

    每次都现算（6 次 `getattr` 而已）：**缓存住它就会在测试里打桩失效** ——
    而「无令牌时业务函数根本没被调用」这条断言恰恰要靠打桩来证。
    """
    out: dict[str, Callable[..., dict]] = {}
    for name in tool_names():
        fn = getattr(server, name, None)
        if callable(fn):
            out[name] = fn
    return out


def missing_handlers() -> list[str]:
    """登记了但 `server.py` 里拿不到对应函数的工具名。启动自检用，正常应为空。"""
    have = handlers()
    return [name for name in tool_names() if name not in have]


def catalog() -> list[dict]:
    """工具名 + 所需 scope。`GET /tools` 的载荷，从 `TOOL_SCOPES` 现算。"""
    return [{"name": name, "scope": auth.TOOL_SCOPES[name]} for name in tool_names()]


def unknown_tool(name: str) -> dict:
    """未知工具的拒绝体。借 `auth.guard` 取码，**不自己拼字面量**。

    `guard(名字, None)` 对未登记的名字返回 `UNKNOWN_TOOL` —— 与工具入口
    同一条路径，所以 HTTP 与 stdio 两侧对「不存在的工具」答复一致。
    """
    _ok, info = auth.guard(name, None)
    return {"ok": False, "denied": True, "tool": name, **info}


def bad_arguments(name: str, detail: str) -> dict:
    """参数不合法的返回体。**公开的**：HTTP 层解析请求体失败时要用同一个形状。"""
    return {"ok": False, "denied": False, "tool": name,
            "code": E_BAD_ARGUMENTS, "detail": detail}


def arguments_of(name: str) -> inspect.Signature:
    """那个工具的签名。找不到就抛 —— 这是构建错了，不是调用错了。"""
    fn = handlers().get(name)
    if fn is None:
        raise RuntimeError(
            f"{name!r} 在 auth.TOOL_SCOPES 里登记了，但 server.py 里拿不到这个函数。"
            "这是构建错误，不是调用错误：先跑 `python -m mcp_server.server --check`"
            " 把注册表对齐，别在这里兜底。")
    return inspect.signature(fn)


def dispatch(name: str, arguments: dict[str, Any] | None = None) -> dict:
    """按名字调一个工具。**未知名字与坏参数都降级成字典，不抛异常。**

    真正的业务异常会冒泡出去，理由见文件头「真异常让它冒泡」。
    """
    if name not in auth.TOOL_SCOPES:
        return unknown_tool(name)

    fn = handlers().get(name)
    if fn is None:
        # 到了这里说明 TOOL_SCOPES 与 server.py 对不上 —— 启动自检会先拦下它。
        arguments_of(name)          # 复用同一条报错，别写第二份文案
        raise AssertionError("unreachable")  # pragma: no cover

    args: dict[str, Any] = {} if arguments is None else arguments
    if not isinstance(args, dict):
        return bad_arguments(name, "参数必须是一个对象（这个工具的 kwargs）")

    sig = inspect.signature(fn)

    # `token` 缺席 = 「没带令牌」，不是「请求畸形」。
    #
    # 不补这一下的话，一个空请求体 `{}` 会被 `bind` 判成 BAD_ARGUMENTS(400)，
    # 而所有人心里的预期都是 403 NO_TOKEN —— 于是复核的人得先读一遍源码才
    # 明白 400 是什么意思。补成 `None` 之后它走的是 `guard()` 里
    # `if not token: return E_NO_TOKEN` 那条**本来就存在**的分支，拒绝码由
    # 鉴权层统一给出，派发层不自己造码。
    #
    # 只有 `token` 享受这个待遇：它是每个工具都有的「身份」参数，而
    # `sql` / `question` 这些缺席就是真的少给了参数。
    if "token" in sig.parameters and "token" not in args:
        args = {**args, "token": None}

    try:
        # `bind` 是**严格**的：多给一个键、少给一个必填键都会抛 TypeError。
        # 用 `bind` 而不是 `bind_partial` 是刻意的 —— 少传 token 必须当场判错，
        # 而不是让工具拿到一个默认的 None 再走一遍鉴权（那样错误码会变成
        # NO_TOKEN，把「请求少写了一个字段」误报成「鉴权失败」）。
        bound = sig.bind(**args)
    except TypeError as e:
        return bad_arguments(name, str(e))

    return fn(*bound.args, **bound.kwargs)
