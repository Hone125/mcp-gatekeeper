"""并发调用层：把一批「零参可调用」并发跑起来，限并发、限时、结果逐条成账。

## 为什么需要这一层

工具函数全是**同步阻塞**的（SQLite 读写）。要一次跑十几条互不相干的调用，
串行跑就是十几倍的时间；而 `asyncio.gather` 一批同步函数**不会更快** ——
它连让出点都没有，协程之间没有交错的机会。这两件事在
`experiments/29_concurrency_bench.py` 里都实测过（三种跑法的对照表在那里）。

这个模块存在的意义就是把「正确的那种并发」封装成一次调用，同时把
**失败也变成数据**：超时一条不影响其余，异常一条也不影响其余。

## 四条规矩

**1. 同步函数一律 `asyncio.to_thread`。** 这是本模块唯一真正重要的一行。
同步阻塞函数直接在事件循环里调，等于把整个循环按住 —— 并发度实测退化成 1
（见 29 的 B 组）。`offload=False` 这个开关**保留下来是为了复现那个负结果**，
不是给人用的：默认值和文档都指向 `True`。

**2. 结果顺序与入参顺序一一对应。** `gather` 保序，所以 `results[i]` 一定对应
`calls[i]`，即使第 3 条先跑完。调用方不用自己配对。

**3. `len(results) == len(calls)` 恒成立。** 哪怕 `gather` 里出了意外，
`return_exceptions=True` 之后再逐条检查一遍 `isinstance(r, BaseException)`，
把它也降级成一条错误记录。少一条结果比多一条错误难查得多。

**4. 失败降级成字段，不抛。** 超时 → `timeout: True`；异常 → `error` 是异常
**类名**，`detail` 最多 200 字。

## `detail` 里为什么不写秒数

`str(e)[:200]` 会把异常消息原样带出来。超时那条的 `timeout_seconds` 记的是
**设定值**（比如 5.0，是我们自己传进去的常量），不是实测耗时 —— 实测耗时进产物
会让报告没法逐字节复现（同一份代码跑两遍，`git status` 应当是干净的）。
`experiments/29` 因此把耗时只打终端、另存 `cache/`（那个目录不入库）。

## 关于 `wait_for` 超时的一个诚实说明

`wait_for` 超时只能**放弃 `await`**。如果被超时的是一个跑在 `to_thread` 里的
阻塞函数，那个线程会继续跑到底 —— Python 没有办法强杀一个线程。所以：

- 超时保护的是**调用方**（它不会无限等下去），不是被调用的那段代码；
- `offload=False` 时连这个保护都失效：事件循环被阻塞函数按住，`wait_for` 的
  计时器根本没机会触发。这不是缺陷，是「同步阻塞调用不能被超时中断」的直接后果。
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Iterable, Sequence

# 默认并发度。6 与 `auth.TOOL_SCOPES` 的工具数一致 —— 一个恰好能让「所有工具
# 各跑一次」铺满的值。它是个上限而不是目标：传进来的调用少于它时就跑不了那么满。
DEFAULT_LIMIT = 6

# 单条错误的 `detail` 截断长度。够看清是什么错，又不至于把一整篇 traceback 抄进报告。
DETAIL_LIMIT = 200


async def _invoke(fn: Callable[[], Any], offload: bool) -> Any:
    """把一条调用变成可 await 的东西。**三种形态的差别就是本模块的全部内容。**"""
    if inspect.iscoroutinefunction(fn):
        # 本来就是协程函数：它自己会让出，直接 await。
        return await fn()
    if offload:
        # ★ 同步阻塞函数挪到工作线程。这一行是「并发真的并发」的原因。
        return await asyncio.to_thread(fn)
    # ⚠️ 天真并发：在事件循环里**直接**调同步函数。
    # 它会一直占着循环直到返回，别的协程一步都跑不了。
    # 保留这条路径是为了让 experiments/29 能用**同一个函数、只切一个开关**
    # 复现出「gather 一批同步函数不提速」这个负结果 —— 两个跑法只差这一处，
    # 报告里才说得清是这一处造成的差别。
    return fn()


async def run_many(calls: Iterable[Callable[[], Any]], limit: int = DEFAULT_LIMIT,
                   timeout: float | None = None, offload: bool = True) -> list[dict]:
    """并发跑一批零参可调用，返回与入参**等长、同序**的结果列表。

    `calls` 里的每一项要么是零参同步函数，要么是零参协程函数；两者可以混着放。
    `timeout` 是**单条**的超时（秒），`None` = 不限时。
    """
    if limit < 1:
        # 静默把一个 0 当成 1 会让「我明明设了并发度」和实际行为对不上。
        raise ValueError(f"limit 至少为 1，收到 {limit!r}")

    items: list[Callable[[], Any]] = list(calls)
    # 信号量在**每次调用内部**创建：放模块级会让上一次调用剩余的额度泄漏到下一次
    # （比如上一次有一条卡住不返回，这一次就莫名其妙少一个名额）。
    sem = asyncio.Semaphore(limit)

    async def one(i: int, fn: Callable[[], Any]) -> dict:
        async with sem:                      # 同时进入这里的协程数不超过 limit
            try:
                if timeout is None:
                    value = await _invoke(fn, offload)
                else:
                    value = await asyncio.wait_for(_invoke(fn, offload), timeout)
                return {"ok": True, "index": i, "value": value}
            except asyncio.TimeoutError:
                return {"ok": False, "index": i, "timeout": True,
                        "error": "TimeoutError",
                        # 记的是**设定值**（我们传进来的常量），不是实测耗时。
                        "timeout_seconds": timeout}
            except Exception as e:  # noqa: BLE001 —— 失败要变成数据，不是中断整批
                return {"ok": False, "index": i, "timeout": False,
                        "error": type(e).__name__, "detail": str(e)[:DETAIL_LIMIT]}

    raw = await asyncio.gather(*(one(i, fn) for i, fn in enumerate(items)),
                               return_exceptions=True)

    out: list[dict] = []
    for i, r in enumerate(raw):
        if isinstance(r, BaseException):
            # 规矩 3：到了这里说明 `one()` 自己都没兜住（比如被外部取消）。
            # 也降级成一条记录，保证等长。
            out.append({"ok": False, "index": i, "timeout": False,
                        "error": type(r).__name__, "detail": str(r)[:DETAIL_LIMIT]})
        else:
            out.append(r)
    return out


def summarize(results: Sequence[dict]) -> dict:
    """数一数：总共几条、成了几条、超时几条、异常几条。

    超时**同时**算「没成功」，但只计入 `timeout` 一栏，不会在 `error` 里再算一次 ——
    两栏相加必须正好等于 `n`，否则这张表自己就对不上账。
    """
    return {
        "n": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "timeout": sum(1 for r in results if r.get("timeout")),
        "error": sum(1 for r in results
                     if not r.get("ok") and not r.get("timeout")),
    }


def successes(results: Sequence[dict]) -> list[tuple[int, Any]]:
    """成功的结果 `(index, value)`，按 index 升序。

    返回带下标而不是只返回值：失败的位置没有值，只返回值列表会让下标错位，
    而错位的结果集看起来完全正常 —— 那是这类接口最难查的一种错。
    """
    return [(r["index"], r["value"]) for r in results if r.get("ok")]
