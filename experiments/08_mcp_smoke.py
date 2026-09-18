"""MCP 冒烟测试：走一次**真实的 stdio 握手**，像一个真客户端那样把服务端跑起来。

## 为什么不直接调函数

前面的实验都是 `import` 之后直接调函数，那只证明了「函数是对的」。
真正会出问题的地方在**进程边界**上：

- 服务端能不能被 `python -m mcp_server.server` 拉起来？
- 工具的 `input_schema` 里到底有没有 `token`？（客户端据此决定要不要传令牌）
- 令牌不对时，服务端是回一个**正常的工具结果**，还是把整个会话搞崩？
- 服务端卡住不响应时，客户端会不会**永远挂在那里**？

最后一条是这里最要紧的：一个没有超时的握手，在服务端启动失败时不会报错，
只会**静静地等到天荒地老**。所以下面同时挂了两个超时，并且专门造了一个
「服务端起得来但不说话」的场景去**证明这两个超时真的会触发** ——
只写「我们设了超时」而没有跑过一次超时，等于没设。

## 两种「失败」必须分开

| 情形 | 协议层 | 返回体 |
|---|---|---|
| 令牌缺失/不对/scope 不够 | **正常结果**（`is_error=False`） | `denied: True` + 错误码 |
| 参数没传够（协议层就缺字段） | **协议错误**（`is_error=True`） | 框架生成的错误文本 |

把鉴权失败做成协议错误是常见的错法：客户端会把它当成「工具调用失败」而不是
「我没有权限」，于是重试、报错、把整轮对话打断。这里是刻意的设计，也有测试固定。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from mcp_server import auth, console, paths, server  # noqa: E402

REPORTS = paths.reports_dir()

SERVER_CMD = [sys.executable, "-B", "-X", "utf8", "-m", "mcp_server.server"]


def child_env() -> dict:
    """拉起服务端时用的环境：**显式钉死 stdio 编码**。

    ## 为什么光有 `-X utf8` 不够

    `-X utf8` 打开 UTF-8 模式，但 **`PYTHONIOENCODING` 的优先级比它高**。
    如果这个环境变量恰好被设成了 gbk，子进程的 stderr 就按 GBK 编码，
    而父进程这边是按 UTF-8 解码的 —— 结果是启动横幅解出来一片乱码，
    「stderr 里有没有启动信息」这条检查误报成失败。

    这个坑是**实测撞出来的**：用 `PYTHONIOENCODING=gbk` 跑这个脚本，
    12/12 的会话检查全过，唯独 stdout 纯净性那条报「stderr 里没有启动信息」。

    **服务端本身没做错。** 错在父进程：它假定了一个自己没去设定的编码。
    要么自己设，要么别假定 —— 这里选前者。
    """
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env

# 会话级读超时。它管的是「发出请求后等回复」这一段。
READ_TIMEOUT = 20.0
# 每个动作外面再套一层硬超时。它管的是 `read_timeout_seconds` **管不到**的部分：
# 进程启动、传输层握手、以及框架内部在收到回复之后的处理。
# 两个都挂上是因为它们的保护范围不重合 —— 只挂一个的话，另一个区间里卡住就没人管。
HARD_TIMEOUT = 30.0

# 「服务端不响应」那个负例的等待上限。设得比 HARD_TIMEOUT 小，这样它必须靠
# 超时机制返回，而不是靠运气。
HANG_TIMEOUT = 5.0


def root_cause(exc: BaseException) -> BaseException:
    """把嵌套的 `ExceptionGroup` 剥到最里面那个真实异常。

    这不是洁癖。`stdio_client` 把一切都包在 anyio 的任务组里，任何失败都会变成
    十几层 `+-+ ExceptionGroup`，真正的原因（比如「模块名拼错了」）埋在最后一行。
    直接打印 `repr(exc)` 的话，报告里出现的是一屏缩进，读者抓不到重点。
    """
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
            continue
        cause = exc.__cause__ or exc.__context__
        if cause is not None and cause is not exc:
            exc = cause
            continue
        break
    return exc


def describe_exc(exc: BaseException) -> str:
    e = root_cause(exc)
    return f"{type(e).__name__}: {str(e)[:160]}"


class Rows:
    """收集检查行的同时把结果也记下来，避免「检查了但没记录」。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        # 线上真实收到的 input_schema，原样存下来供报告引用。
        # **不手写**：报告里凡是「这个工具要求哪些参数」的说法，
        # 都必须来自这里，而不是我照着函数签名再抄一遍 —— 抄写就是编数字。
        self.schemas: list[dict] = []

    def add(self, label: str, expect: str, got: str, ok: bool, note: str = "") -> None:
        self.rows.append({"label": label, "expect": expect, "got": got,
                          "ok": bool(ok), "note": note})

    def fail_rest(self, labels: list[tuple[str, str]], reason: str) -> None:
        for label, expect in labels:
            self.add(label, expect, f"未跑到（{reason}）", False,
                     "会话在这一步之前就断了")

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.rows)


async def _call(session: ClientSession, name: str, args: dict):
    return await asyncio.wait_for(session.call_tool(name, args), timeout=HARD_TIMEOUT)


async def run_checks() -> Rows:
    """一次完整的会话：握手、列工具、按令牌逐个试。"""
    r = Rows()
    params = StdioServerParameters(command=SERVER_CMD[0], args=SERVER_CMD[1:], cwd=str(ROOT),
                                   env=child_env())

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=READ_TIMEOUT) as s:
            # ---------------- 握手
            init = await asyncio.wait_for(s.initialize(), timeout=HARD_TIMEOUT)
            r.add("握手成功，拿到服务端标识",
                  f"server_info.name == {server.mcp.name!r}",
                  f"{init.server_info.name!r}，协议版本 {init.protocol_version!r}",
                  init.server_info.name == server.mcp.name,
                  "协议版本由 SDK 磋商，本仓库不锁定它")

            tools = await asyncio.wait_for(s.list_tools(), timeout=HARD_TIMEOUT)
            names = sorted(t.name for t in tools.tools)
            r.schemas = [{"name": t.name,
                          "required": sorted((t.input_schema or {}).get("required") or []),
                          "properties": sorted((t.input_schema or {}).get("properties") or {}),
                          "scope": auth.TOOL_SCOPES.get(t.name, "（未登记）")}
                         for t in sorted(tools.tools, key=lambda x: x.name)]

            # ---------------- 三处独立来源互相印证
            # 工具名在三处各自维护：@mcp.tool() 装饰的注册表、TOOL_SCOPES 映射表、
            # 以及客户端**从线上看到的**这一份。三处对齐才说明没有漏登记。
            declared = sorted(auth.TOOL_SCOPES)
            local = await server.registered_tool_names_async()
            r.add("线上工具数与 TOOL_SCOPES 一致", f"{len(declared)} 个",
                  f"{len(names)} 个：{', '.join(names)}", names == declared,
                  "客户端看到的是框架真实导出的清单")
            r.add("线上工具数与本地注册表一致", f"{len(local)} 个",
                  f"{len(names)} 个", names == local)

            # ---------------- 每个工具都要 token
            no_token = [t.name for t in tools.tools
                        if "token" not in (t.input_schema.get("required") or [])]
            r.add("**每个**工具的入参 schema 都把 token 列为必填",
                  "6 / 6 个工具要求 token",
                  "全部符合" if not no_token else f"这些没要求：{no_token}",
                  not no_token,
                  "客户端是照着 schema 决定传不传令牌的；schema 里没有 token，"
                  "就等于在接口契约里没提鉴权这回事")

            with_desc = [t.name for t in tools.tools if (t.description or "").strip()]
            r.add("**每个**工具都有描述", "6 / 6",
                  f"{len(with_desc)} / {len(tools.tools)}", len(with_desc) == len(tools.tools),
                  "描述是模型选择工具的唯一依据")

            # ---------------- 无令牌
            res = await _call(s, "list_tables", {"token": ""})
            d = json.loads(res.content[0].text)
            r.add("无令牌调用：协议层不算错误，返回体里标记拒绝",
                  "is_error=False 且 denied=True 且 code=NO_TOKEN",
                  f"is_error={res.is_error}，denied={d.get('denied')}，code={d.get('code')}",
                  res.is_error is False and d.get("denied") is True
                  and d.get("code") == auth.E_NO_TOKEN,
                  "做成协议错误的话，客户端会当成「调用失败」去重试，"
                  "而不是「我没有权限」")

            # ---------------- 伪造令牌
            forged = auth.make_token("attacker", list(auth.SCOPES),
                                     secret_="a-different-secret-of-sufficient-length-0000")
            res = await _call(s, "list_tables", {"token": forged})
            d = json.loads(res.content[0].text)
            r.add("伪造签名的令牌被拒", f"code={auth.E_BAD_SIGNATURE}",
                  f"code={d.get('code')}",
                  d.get("code") == auth.E_BAD_SIGNATURE and d.get("denied") is True)

            # ---------------- scope 不足
            res = await _call(s, "run_sql", {"token": auth.make_token("u", ["db:read"]),
                                             "sql": "SELECT 1"})
            d = json.loads(res.content[0].text)
            r.add("令牌有效但 scope 不足，被工具级 scope 拒掉",
                  f"code={auth.E_SCOPE}", f"code={d.get('code')}",
                  d.get("code") == auth.E_SCOPE and d.get("denied") is True,
                  "同一把钥匙开不了所有的门")
            r.add("被拒时返回体里没有查询结果",
                  "不含 rows / sql 字段",
                  "不含" if ("rows" not in d and not d.get("sql")) else f"含：{sorted(d)}",
                  "rows" not in d and not d.get("sql"),
                  "鉴权挡在业务代码之前，SQL 根本没被执行")

            # ---------------- 令牌齐全
            tok = auth.make_token("smoke-client", ["db:read"])
            res = await _call(s, "list_tables", {"token": tok})
            d = json.loads(res.content[0].text)
            r.add("令牌齐全时放行（★ 反证：不是一刀切全拒）",
                  "denied 不为 True",
                  f"denied={d.get('denied')}，业务层 code={d.get('code') or 'ok'}",
                  d.get("denied") is not True,
                  "断言的是「没被拒」，不是「业务成功」——本实验不要求数据已构建")

            # ---------------- 协议层错误
            res = await _call(s, "run_sql", {})          # 两个必填参数都没给
            r.add("参数没传够：这次是**协议错误**",
                  "is_error=True",
                  f"is_error={res.is_error}",
                  res.is_error is True,
                  "和上面的鉴权拒绝形成对照：缺参数是「调用方式不对」，"
                  "令牌不对是「调用方式对但你没权限」。两者的处理方式完全不同")

            # 注意这里**接受两种结局**：SDK 可能把「未知工具」回成一个 is_error=True
            # 的正常结果，也可能在客户端侧直接抛错。两者都算「失败了」。
            # 只 catch 异常的话，前一种会被误判成「居然没报错」——第一版就是这么写错的。
            try:
                res = await _call(s, "no_such_tool", {"token": tok})
                r.add("调用不存在的工具", "is_error=True，或客户端直接抛错",
                      f"is_error={res.is_error}，文本 {res.content[0].text[:60]!r}",
                      res.is_error is True,
                      "调用一个没注册的工具必须失败，否则说明服务端在乱猜工具名")
            except Exception as e:  # noqa: BLE001
                r.add("调用不存在的工具", "is_error=True，或客户端直接抛错",
                      f"客户端抛出 {describe_exc(e)}", True)

    return r


async def hang_check() -> dict:
    """★ 证明超时**真的会触发**：起一个能连上但永远不回话的「服务端」。

    只写「我们设了超时」而从不跑一次超时，那句话是没有证据的。这里用
    `python -c "time.sleep(3600)"` 冒充一个启动成功但永不回应的服务端，
    断言 `initialize()` 在 `HANG_TIMEOUT` 秒量级内失败，而不是挂到天荒地老。
    """
    params = StdioServerParameters(
        command=sys.executable, args=["-c", "import time; time.sleep(3600)"], cwd=str(ROOT),
        env=child_env())
    t0 = time.monotonic()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=READ_TIMEOUT) as s:
                await asyncio.wait_for(s.initialize(), timeout=HANG_TIMEOUT)
        return {"label": "服务端启动成功但永不回应时，客户端不会挂死",
                "expect": (f"等到声明的硬超时（{HANG_TIMEOUT:.0f}s）之后、"
                           f"{HANG_TIMEOUT * 3:.0f}s 之内失败并给出原因"),
                "got": "居然握手成功了 —— 超时机制没生效", "ok": False}
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        # ★ 下界和上界都要有，而且**下界才是这条用例的重点**。
        #
        # 只断言 `dt < 上界` 的话，一个根本没等待、立刻抛错的实现同样能通过 ——
        # 那就等于没测到超时机制。下界取声明过的硬超时 `HANG_TIMEOUT`（5s）：
        # 必须**真的等到那一层超时才失败**。
        #
        # 注意 `dt` 从「起子进程」之前开始计时，所以它天然比 5s 大一截
        # （实测约 7s：起进程 + 等待）。原来的期望栏写的是「5 秒内失败」，
        # 而实测一直是 7.0s —— **期望栏本身写着一条从来没被满足过的判据**，
        # 旁边却是一个 ✅。判据是 `dt < 3×`，所以它一直没被发现。
        # 现在期望栏改成真实契约：**大于下界、小于上界**。
        #
        # ★ 耗时**不写进结果里**：报告要能逐字节复现（同一份代码跑两遍，
        # `git status` 应当干净）。时钟读数会变 —— 原来这里存的是 `dt`，
        # 于是每跑一次闸门 `reports/mcp_smoke.{json,md}` 就显示「已修改」。
        # 「等了多久」这件事由下面这句判据（下界）承担，不需要把秒数抄进报告。
        waited = dt >= HANG_TIMEOUT
        return {"label": "服务端启动成功但永不回应时，客户端不会挂死",
                "expect": (f"等到声明的硬超时（{HANG_TIMEOUT:.0f}s）之后、"
                           f"{HANG_TIMEOUT * 3:.0f}s 之内失败并给出原因"),
                "got": (f"等到声明的硬超时之后才失败：{describe_exc(e)}"
                        if waited else
                        f"**没有等到硬超时就失败了**：{describe_exc(e)} —— 超时机制可能没生效"),
                "ok": waited and dt < HANG_TIMEOUT * 3}


def stdout_purity_check() -> dict:
    """★ 把服务端的 stdout 整条抓下来，断言**里面没有一行不是 JSON**。

    这是那个 bug 的直接守卫。启动时打印的「已注册 N 个工具」原本走 `print()`
    （stdout），而 stdio 传输下 stdout 就是 JSON-RPC 通道 —— 客户端收到这行
    人话，解析器抛 `Invalid JSON`，整个会话崩掉。手动跑服务端时一切正常，
    因为人是看着终端的，看不出「stdout 和 stderr 混在一起了」。

    同时断言 stderr 里**确实有**那几行：否则「stdout 是空的」也可能只是因为
    进程根本没起来，那样的通过毫无意义。
    """
    try:
        p = subprocess.run(SERVER_CMD, cwd=str(ROOT), input=b"", env=child_env(),
                           capture_output=True, timeout=HARD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"label": "服务端 stdout 里一行人话都没有（stdin 关闭后应当自行退出）",
                "expect": "stdout 全是 JSON-RPC，且 stderr 里能看到启动信息",
                "got": f"stdin 关闭后 {HARD_TIMEOUT:.0f} 秒仍未退出", "ok": False}

    out = p.stdout.decode("utf-8", errors="replace")
    err = p.stderr.decode("utf-8", errors="replace")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    bad = [ln for ln in lines if not ln.strip().startswith("{")]
    started = "已注册" in err and "TOOL_SCOPES" in err

    return {
        "label": "服务端 stdout 里一行人话都没有",
        "expect": "stdout 全是 JSON-RPC；启动信息出现在 stderr",
        "got": (f"stdout {len(lines)} 行，其中 {len(bad)} 行不是 JSON"
                + (f"（例如 {bad[0][:60]!r}）" if bad else "")
                + f"；stderr 里{'有' if started else '**没有**'}启动信息"),
        "ok": not bad and started,
        "stdout_lines": len(lines),
        "stderr_has_startup": started,
    }


async def dead_server_check() -> dict:
    """服务端**起不来**（模块名拼错）时，客户端要给出可读的原因而不是静默卡住。"""
    params = StdioServerParameters(
        command=sys.executable, args=["-B", "-X", "utf8", "-m", "no.such.module"], cwd=str(ROOT),
        env=child_env())
    t0 = time.monotonic()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=READ_TIMEOUT) as s:
                await asyncio.wait_for(s.initialize(), timeout=HARD_TIMEOUT)
        return {"label": "服务端根本起不来时，客户端给出可读原因",
                "expect": "立刻报错，且原因指向子进程退出",
                "got": "居然握手成功了", "ok": False}
    except Exception as e:  # noqa: BLE001
        dt = time.monotonic() - t0
        # 同 `hang_check`：耗时只用于判据，不抄进报告（报告要能逐字节复现）。
        # 这一条的判据是「**没等到硬超时**就失败了」，也就是「立刻」的机械含义。
        return {"label": "服务端根本起不来时，客户端给出可读原因",
                "expect": "立刻报错，且原因指向子进程退出",
                "got": (f"没等到硬超时（{HARD_TIMEOUT:.0f}s）就失败了：{describe_exc(e)}"
                        if dt < HARD_TIMEOUT else
                        f"一直等到硬超时才失败：{describe_exc(e)}"),
                "ok": dt < HARD_TIMEOUT}


# ---------------------------------------------------------------- 报告
def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ")
                                     for c in r) + " |")
    return "\n".join(out)


def render_md(d: dict) -> str:
    L: list[str] = []
    L.append("# MCP 协议层冒烟测试（实测结果）\n")
    L.append("本文件由 `experiments/08_mcp_smoke.py` 生成，**不要手改**；重跑该脚本即可复现。\n")

    L.append("## 口径\n")
    L.append("- 本实验**真的把服务端当子进程拉起来**，通过 stdio 用 MCP 协议通信 ——"
             "不是 `import` 之后直接调函数。前面几节的实验证明了「函数是对的」，"
             "这一节证明「跨进程还能用」。")
    L.append("- 命令：`python -B -X utf8 -m mcp_server.server`，工作目录为仓库根。")
    L.append(f"- 超时：会话级读超时 `read_timeout_seconds={READ_TIMEOUT:.0f}s`，"
             f"每个动作外面再套 `asyncio.wait_for({HARD_TIMEOUT:.0f}s)`。"
             "两层保护范围不重合，所以两层都挂。")
    L.append("- 不联网、不调用模型。是否已构建 `data/` 下的产物不影响本实验的通过与否。")
    L.append(f"- 运行方式：`python experiments/08_mcp_smoke.py`（退出码 0 = 全过）\n")

    L.append("## 总览\n")
    L.append(_table([["会话内检查", f"{d['pass']} / {d['cases']}"],
                     ["超时与失败路径", f"{d['timeout_pass']} / {d['timeout_cases']}"],
                     ["**合计**", f"**{d['n_pass']} / {d['n_cases']}**"]],
                    ["项", "通过 / 总数"]))
    L.append("")

    L.append("## 一、会话内检查\n")
    L.append(_table([[i + 1, r["label"], r["expect"], r["got"], "✅" if r["ok"] else "❌"]
                     for i, r in enumerate(d["rows"])],
                    ["#", "检查项", "期望", "实测", "结果"]))
    L.append("")

    L.append("## 二、超时与失败路径\n")
    L.append("这一节的用意是：**只声明「我们设了超时」而不跑一次超时，等于没设。** "
             "下面用两个假服务端把两条失败路径真的走一遍 —— "
             "一个「连得上但永不回话」，一个「根本起不来」。\n")
    L.append(_table([[r["label"], r["expect"], r["got"], "✅" if r["ok"] else "❌"]
                     for r in d["timeout_rows"]],
                    ["检查项", "期望", "实测", "结果"]))
    L.append("")

    L.append("## 三、每个工具在线上暴露的入参\n")
    L.append("下面这张表是**客户端从协议里真实收到的 `input_schema`**，不是照着函数签名抄的。\n")
    L.append("客户端是照着 schema 决定传不传令牌的。schema 里不把 `token` 列为必填，"
             "就等于在**接口契约**里没提鉴权这回事 —— 函数体里检查得再严，"
             "从契约角度看也是「这个工具不需要身份」。\n")
    L.append(_table(
        [[f"`{r['name']}`",
          "、".join(f"`{x}`" for x in r["required"]) or "（无）",
          "、".join(f"`{x}`" for x in r["properties"]) or "（无）",
          f"`{r['scope']}`"]
         for r in d["schemas"]],
        ["工具", "required（必填）", "properties（全部入参）", "需要的 scope"]))
    L.append("")

    if d["notes"]:
        L.append("## 四、值得记下来的实测行为\n")
        for n in d["notes"]:
            L.append(f"- {n}")
        L.append("")
    return "\n".join(L)


async def _gather():
    rows = await run_checks()
    t1 = stdout_purity_check()
    t2 = await hang_check()
    t3 = await dead_server_check()
    return rows, [t1, t2, t3]


def main() -> int:
    console.setup_stdio()
    REPORTS.mkdir(parents=True, exist_ok=True)

    try:
        rows, timeout_rows = asyncio.run(_gather())
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 冒烟测试整体失败：{describe_exc(e)}", file=sys.stderr)
        return 1

    schemas = rows.schemas

    notes = [
        "**鉴权失败不是协议错误**：令牌缺失/不对/scope 不足都返回 `is_error=False` "
        "加一个 `denied: True` 的正常结果。做成协议错误的话，客户端会把它读成"
        "「工具调用失败」而去重试，而不是「我没有权限」。缺必填参数才是 `is_error=True`。",
        "**`struct` 与文本都给**：工具返回的是结构化对象，SDK 同时把它序列化进 `content`。"
        "本实验读的是 `content[0].text` 并自己 `json.loads`，"
        "这样即使将来 SDK 改变结构化通道的形态，断言也不受影响。",
        "**任何一个失败都会被包成嵌套的 `ExceptionGroup`**：`stdio_client` 把所有异常"
        "收进 anyio 的任务组，`repr()` 出来是十几层 `+-+`。本脚本用 `root_cause()` "
        "剥到最里层再记进报告 —— 否则报告里出现的是一屏缩进，真正的原因（比如"
        "「模块名拼错了」）反而看不见。",
    ]

    all_rows = rows.rows + timeout_rows
    data = {
        "rows": rows.rows, "timeout_rows": timeout_rows, "schemas": schemas, "notes": notes,
        "cases": len(rows.rows), "pass": sum(r["ok"] for r in rows.rows),
        "timeout_cases": len(timeout_rows), "timeout_pass": sum(r["ok"] for r in timeout_rows),
    }
    data["n_cases"] = len(all_rows)
    data["n_pass"] = sum(r["ok"] for r in all_rows)

    (REPORTS / "mcp_smoke.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / "mcp_smoke.md").write_text(render_md(data), encoding="utf-8", newline="\n")

    print("=" * 72)
    print("MCP 协议层冒烟测试（真实 stdio 握手）")
    print("=" * 72)
    print(f"  会话内检查     {data['pass']} / {data['cases']}")
    print(f"  超时与失败路径 {data['timeout_pass']} / {data['timeout_cases']}")
    print(f"  合计           {data['n_pass']} / {data['n_cases']}")
    print("-" * 72)
    for r in all_rows:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['label']}")
        if not r["ok"]:
            print(f"      期望 {r['expect']}")
            print(f"      实测 {r['got']}")
    print("\n已写入 reports/mcp_smoke.md 与 reports/mcp_smoke.json")
    return 0 if data["n_pass"] == data["n_cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
