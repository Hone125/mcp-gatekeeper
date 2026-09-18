"""控制台编码：让脚本自己负责，不指望调用方记得加 `-X utf8`。

## 为什么需要这个文件

在 Windows 上，`python experiments/11_guardrail_test.py` 直接跑（不带 `-X utf8`）时，
stdout 会按系统 ANSI 代码页（简体中文机器上是 GBK）编码，**而且错误处理是 strict**。
于是这一行会炸：

    print("  Genre 写入之前 25、之后 25 ✅")
    UnicodeEncodeError: 'gbk' codec can't encode character '✅'

**炸点在最后一行，所有用例都已经跑完了。** 现象是脚本打印出「41 / 41」之后
以退出码 1 结束 —— 调用方（或 CI）看到的是「护栏实验失败」，而实际上它全过了。

这类「工作做对了、退出码是错的」比崩溃更糟：它逼着人学会「这个脚本的退出码不用看」，
而退出码正是本项目里最硬的一类证据（`11_guardrail_test.py` 里每条负例的「实测」栏
依据的就是子进程退出码）。

## 一条容易搞反的细节：崩的是 stdout，不是 stderr

Python 里 **stderr 的默认错误处理是 `backslashreplace`**，stdout 是 `strict`。
所以同样是打一个 ⚠，走 stderr 只会打印成字面的 `\\u26a0`（难看，但不崩），
走 stdout 直接抛 `UnicodeEncodeError`。

这一点是**实测出来的**，不是推理出来的：我最初以为 `guardrails.py` 的
`--raw` 警告（带 ⚠、走 stderr）会让 CLI 崩掉，还照这个想法写了一条回归测试；
那条测试的负控当场把它证伪了 —— 打桩去掉修复之后，CLI 的退出码仍然是文档里那个码，
stderr 里也没有 `UnicodeEncodeError`。**错的解释被删掉，换成了这一节。**

所以这里对两个流给不同的 `errors`：stdout 用 `replace`（那几行是给人看的进度），
stderr 保持 `backslashreplace`（排查用的信息里保留转义序列更有用）。

## 还有一个更实际的理由：子进程的 stdout 是要被机器解析的

`python -m mcp_server.guardrails` 的输出由调用方按 UTF-8 解码（`11_guardrail_test.py`、
`conftest.py`）。如果编码跟着终端代码页走，在 GBK 机器上解出来就是乱码 ——
报告里会出现「报错原因是乱码」这种看着吓人、其实无害的现象。
把编码钉死成 UTF-8，两边才对得上。

## 为什么不在别处兜底

- **不放到 `mcp_server/__init__.py`**：那是库的入口，import 就改全局 stdio 是越权行为；
  而且 `server.py` 的 stdout 是 JSON-RPC 通道，**绝不能**在这里被动过。
- **不指望调用方加 `-X utf8`**：本仓已经踩过一次 —— 加参数的地方（子进程调用）
  是对的，但父进程自己没人管。约定要写在能被强制执行的地方。

所以是显式调用：每个 `experiments/*.py` 和 `tools/check.py` 在 `main()` 开头调一次。

## `mcp_server/server.py` 不要调它

服务端的 stdout 是协议通道。它的中文一律走 stderr，编码由 MCP SDK 的
stdio 传输层自己处理 —— 见 `server.py` 顶部那段说明。
"""
from __future__ import annotations

import sys


def setup_stdio() -> None:
    """把本进程的 stdout / stderr 重配成 UTF-8。**幂等，且永不抛异常。**

    两个流的 `errors` **故意不同**：

    - stdout → `replace`：给人看的进度行，打不出来显示成 `?` 可以接受；
    - stderr → `backslashreplace`：这是 Python 对 stderr 的默认值，保留它 ——
      排查用的信息里看到 `\\u26a0` 比看到一个 `?` 有用得多，至少知道原字符是什么。

    两者共同点是**都不抛异常**：终端上显示成什么是小事，退出码被带歪是大事。

    **注意**：真正要看的产物一律用显式 `encoding="utf-8"` 写文件，
    不经过 stdout。这个函数只管给人看的那部分输出。
    """
    for stream, errors in ((sys.stdout, "replace"),
                           (sys.stderr, "backslashreplace")):
        try:
            stream.reconfigure(encoding="utf-8", errors=errors)
        except (AttributeError, ValueError, OSError):
            # 流被换成了非 TextIOWrapper（某些测试夹具、被重定向到自定义对象），
            # 或者已经关掉了。都不是致命情况 —— 保持原样即可。
            pass
