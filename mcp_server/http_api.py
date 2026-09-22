"""HTTP 入口：把同样这 6 个工具挂到 `POST /tools/<工具名>` 上。

## 这是「换个传输层」，不是「再实现一遍」

每个路由调的是 `dispatch.dispatch()`，而它调的是 `server.py` 里那个被
`@mcp.tool()` 装饰过的**同一个函数**。所以：

- 鉴权还是工具入口那一行 `_deny(...)`，**HTTP 层一次都不判**（判两次就多一处
  会漏的地方，见 `tests/test_dispatch.py` 里那条「两侧返回体逐字节相同」）。
- 业务逻辑没有第二份。改 `run_sql` 的护栏，stdio 与 HTTP 同时生效。
- 顺带白拿的：FastAPI 会按路由生成 OpenAPI，`/docs` 上能直接点着调 —— 6 个
  工具的入参 schema 是从真实函数签名里取的，不是抄的文档。

## 路由表从哪来：仍由 `auth.TOOL_SCOPES` 现生成

下面不是手写 6 条 `@app.post(...)`，而是 `for name in dispatch.tool_names()`
循环挂的，并且把**路由的 `name` 设成工具名本身**。这样启动自检能直接比对
「路由名字集合 == `TOOL_SCOPES` 键集合」，与 `server.py` 那条自检同构 ——
两处独立维护的字符串，漏一处是静默降级，这条规矩在这个仓库里已经写过三遍，
不能到 HTTP 这一层就松掉。

## 错误映射：不搞一刀切

`status_for()` 一张表说完。要点有三个：

- **被 SQL 护栏拦下（`blocked: True`）是 200**，不是 4xx/5xx。它是**业务结论**：
  请求被正确地接收、正确地执行、并得出了「这条 SQL 不许跑」的结论。把它报成
  传输层失败，调用方就会去重试或查网络，而正确处置是去看 SQL 写错了什么。
- **未知工具是 404**（默认 404 的 `{"detail":"Not Found"}` 会被改写成与 stdio
  侧同一个拒绝体，`code: UNKNOWN_TOOL`）。
- **`DEV_SECRET_REFUSED` 是 500**，虽然它也带 `denied: True`。这个码的含义是
  「**服务端自己**在严格模式下还在用内置开发密钥」，是服务端的配置问题；
  报 403 等于把运维的锅甩给调用方。

## ⚠️ 阻塞调用必须挪出事件循环

工具函数是同步阻塞的（SQLite 读写）。`async def` 端点里直接调它会把**整个
服务**卡住 —— 一次 `POST /tools/run_sql` 期间，`/healthz` 都探不通。
所以下面一律 `await asyncio.to_thread(...)`。

这不是理论担心：`experiments/29_concurrency_bench.py` 的 B 组实测出来的就是这个
现象 —— 同步函数直接 `gather` 会退化成串行（实测最大并发 1）。服务端不能
自己再踩一遍同一个坑。

## stdout 与 stderr

与 `server.py` 同一条规矩：**只有 `--check` 这个给人看的模式才写 stdout**。
服务模式下所有人类可读输出（含端口那一行）走 stderr —— 冒烟脚本就是靠读
stderr 拿到真实端口的。

退出码：0 = 通过；1 = 自检不通过；2 = 参数不对或端口起不来。
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import traceback
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mcp_server import auth, dispatch, server

TOOLS_PREFIX = "/tools/"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# 没被工具接住的那类异常。**不在 auth.ALL_CODES 里**：它不是拒绝，是故障。
E_INTERNAL = "INTERNAL_ERROR"

app = FastAPI(
    title="mcp-guarded-toolkit · HTTP 入口",
    description=(
        "与 MCP stdio 入口同一批工具、同一套守卫，只是多了一个传输层。"
        "每个工具是 `POST /tools/<工具名>`，请求体是那个工具的关键字参数（含 `token`）。"
        "被 SQL 护栏拦下的请求返回 200，因为那是业务结论而不是传输失败。"
    ),
    version="1.0",
)


# ---------------------------------------------------------------- 路由表
def tool_routes() -> list[tuple[str, str]]:
    """(工具名, 路径)。**从 `dispatch.tool_names()` 现生成**，不手写。"""
    return [(name, f"{TOOLS_PREFIX}{name}") for name in dispatch.tool_names()]


def route_names() -> list[str]:
    """挂在 `/tools/` 下的路由名。自检拿它和 `TOOL_SCOPES` 的键比对。

    只收 `/tools/` 前缀：FastAPI 自己还挂着 `/openapi.json`、`/docs`、
    `/redoc` 这些，它们不是工具路由，混进来会让比对永远不等。
    """
    out = []
    for r in app.routes:
        path = str(getattr(r, "path", ""))
        if path.startswith(TOOLS_PREFIX):
            out.append(str(getattr(r, "name", "")))
    return out


def status_for(result: dict) -> int:
    """工具返回体 → HTTP 状态码。**顺序有意义**，别改成字典查表。

    `UNKNOWN_TOOL` 与 `DEV_SECRET_REFUSED` 都带 `denied: True`，所以它们必须
    排在「一刀切 403」那条前面；把顺序调换，这两种就会被吞成 403。
    """
    code = result.get("code")
    if code == auth.E_UNKNOWN_TOOL:
        return 404
    if code == auth.E_DEV_SECRET:
        # 服务端自己没配好（严格模式下还在用内置开发密钥）。报 403 会把
        # 运维的问题说成调用方的问题。
        return 500
    if result.get("denied") is True:
        return 403
    if code == dispatch.E_BAD_ARGUMENTS:
        return 400
    return 200


def _json(body: dict, status: int | None = None) -> JSONResponse:
    return JSONResponse(status_code=status if status is not None else status_for(body),
                        content=body)


# ---------------------------------------------------------------- 端点
def _make_endpoint(name: str) -> Callable[[Request], Any]:
    async def endpoint(request: Request) -> JSONResponse:
        raw = await request.body()

        if not raw.strip():
            # 空请求体 = 没给参数。**不是错误**：`token` 缺席由 dispatch 补 `None`
            # 交给 guard 判 NO_TOKEN，于是这里自然得到 403。
            args: Any = {}
        else:
            try:
                args = json.loads(raw)
            except Exception:  # noqa: BLE001 —— 解析失败是调用方的问题，不是故障
                return _json(dispatch.bad_arguments(name, "请求体不是合法 JSON"))

        # 见文件头「阻塞调用必须挪出事件循环」。
        result = await asyncio.to_thread(dispatch.dispatch, name, args)
        return _json(result)

    # 路由名 = 工具名。自检与 `/tools` 都读它，改这里等于改契约。
    endpoint.__name__ = f"tool_{name}"
    endpoint.__doc__ = f"{name}：与 MCP 工具同名同函数，鉴权仍在工具入口那一行。"
    return endpoint


def _tool_count() -> JSONResponse:
    """`GET /tools`：工具名 + 所需 scope。从 `TOOL_SCOPES` 现算。

    **不要求令牌**：这几行信息在 README 的工具表里本来就是公开的，
    调不动任何东西（真调还是要令牌）。挡它只会让 /docs 变得没法用。
    """
    cat = dispatch.catalog()
    return JSONResponse(content={"ok": True, "n": len(cat), "tools": cat})


def _healthz() -> JSONResponse:
    """存活探针。刻意不碰数据库 —— 探针探的是「进程还活着」。

    要探「依赖是否可用」应该是另一个端点，否则数据库一慢，编排系统就把
    一个健康的进程杀掉重启。
    """
    return JSONResponse(content={"ok": True, "tools": len(dispatch.tool_names())})


# 循环挂载。放在模块末段而不是顶部，是为了让上面的函数定义先就位。
for _name, _path in tool_routes():
    app.add_api_route(_path, _make_endpoint(_name), methods=["POST"], name=_name)
app.add_api_route("/tools", _tool_count, methods=["GET"], name="tools_index")
app.add_api_route("/healthz", _healthz, methods=["GET"], name="healthz")


# ---------------------------------------------------------------- 错误改写
@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """把默认的 404 改写成与 stdio 侧同一个拒绝体。

    `POST /tools/nope` 匹配不到任何路由，Starlette 会抛 404，默认响应体是
    `{"detail":"Not Found"}` —— 那既不是我们的拒绝形状，也没说清原因。
    这里只在 `/tools/` 前缀下改写，其他路径的默认行为原样保留。
    """
    path = request.url.path
    if exc.status_code == 404 and path.startswith(TOOLS_PREFIX):
        name = path[len(TOOLS_PREFIX):].split("/")[0]
        return JSONResponse(status_code=404, content=dispatch.unknown_tool(name))
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """没被工具接住的那类异常 → 500。**只回类名，不回消息。**

    消息里可能带着参数值，而 `token` 就在参数里。完整堆栈打到 stderr 供本机
    排查 —— 但不进响应体，也就不可能被谁抄进报告。
    """
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    path = request.url.path
    name = path[len(TOOLS_PREFIX):].split("/")[0] if path.startswith(TOOLS_PREFIX) else ""
    return JSONResponse(status_code=500, content={
        "ok": False, "denied": False, "tool": name,
        "code": E_INTERNAL, "detail": type(exc).__name__,
    })


# ---------------------------------------------------------------- 自检
def selfcheck() -> list[str]:
    """启动自检：返回问题清单，空 = 通过。装不起来就别装。

    与 `server.py` 的自检同构，并且**把它也跑一遍**：工具层注册表坏掉时，
    HTTP 这一层没有任何办法是好的，早一点说出来比晚一点好。
    """
    problems: list[str] = []

    for name in dispatch.missing_handlers():
        problems.append(f"{name!r} 在 TOOL_SCOPES 里登记了，但 server.py 里没有这个函数")
    for p in server.selfcheck():
        problems.append(f"[工具层] {p}")

    names = set(dispatch.tool_names())
    table = set(route_names())

    for name in dispatch.tool_names():
        if name not in table:
            problems.append(
                f"工具 {name!r} 没有对应的路由（应为 POST {TOOLS_PREFIX}{name}）—— "
                "HTTP 侧调不到它，而 stdio 侧调得到，两个入口的行为就不一样了")
    for extra in sorted(table - names):
        problems.append(
            f"路由 {extra!r} 不在 TOOL_SCOPES 里 —— 它拿不到 scope 映射，"
            "对任何令牌都只会返回 UNKNOWN_TOOL：要么是漏登记，要么是名字写错了")

    paths = {str(getattr(r, "path", "")) for r in app.routes}
    for need in ("/tools", "/healthz"):
        if need not in paths:
            problems.append(f"缺少路由 {need}")

    return problems


# ---------------------------------------------------------------- 命令行
def _flag(argv: list[str], flag: str, default: str) -> str:
    """取 `--flag 值` 或 `--flag=值` 里的值。"""
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return default


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv

    # 与 server.py 同一条规矩：stdout 只留给 `--check`。
    out = sys.stdout if check_only else sys.stderr

    def say(msg: str = "") -> None:
        print(msg, file=out)

    warn = auth.warn_if_dev_secret()
    if warn:
        print(f"[警告] {warn}", file=sys.stderr)

    names = dispatch.tool_names()
    say(f"HTTP 入口挂载 {len(names)} 个工具：{', '.join(names)}")
    for name in names:
        say(f"  POST {TOOLS_PREFIX}{name:<16} 需要 {auth.TOOL_SCOPES.get(name, '（未登记）')}")

    problems = selfcheck()
    if problems:
        print("\n[FAIL] 启动自检未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    say("[OK] 启动自检通过：路由名集合与 TOOL_SCOPES 键集合完全一致")

    if check_only:
        return 0

    try:
        port = int(_flag(argv, "--port", str(DEFAULT_PORT)))
    except ValueError:
        print(f"[FAIL] --port 需要一个整数，收到 {_flag(argv, '--port', '')!r}", file=sys.stderr)
        return 2
    host = _flag(argv, "--host", DEFAULT_HOST)

    # 自己 bind 再交给 uvicorn，而不是让它去 bind 一个端口号。
    #
    # `--port 0` 让内核分配一个空闲端口，`getsockname()` 立刻告诉我们**真实**
    # 端口是多少。先探测空闲端口再交给 uvicorn 去 bind 会有竞态：探测到 bind
    # 之间那个端口可能被别的进程抢走。冒烟脚本要的就是这条可靠的通路。
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
    except OSError as e:
        print(f"[FAIL] 起不来：{host}:{port} —— {e}", file=sys.stderr)
        sock.close()
        return 2

    real_host, real_port = sock.getsockname()[:2]
    # flush：冒烟脚本是**逐行读**这一行拿端口的，缓冲住它就会两边一起卡死。
    print(f"[http] 监听 {real_host}:{real_port}", file=sys.stderr, flush=True)

    import uvicorn   # 延迟导入：`--check` 与测试都不需要起 ASGI 服务器

    config = uvicorn.Config(app, log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
