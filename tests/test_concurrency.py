"""并发层：把「实测并发度」量出来，而不是从「我传了 limit=6」推出来。

`experiments/29_concurrency_bench.py` 量的是**真工具**（真 SQLite、真 FTS）在
三种跑法下的峰值；这里用**打桩任务**把同一批计数断言做得更小更快，两处是
「粗测一遍」和「逐条钉死」的关系。

四条计数断言：

1. 打桩任务**实测**同时在跑的最大数量 == limit；
2. 一条必然超时 → 超时计数 1、成功计数 N-1，且成功的那几条结果原样在位；
3. 注入一个抛异常的任务 → 异常计数 1、其余照常返回；
4. `offload=False`（天真并发）时实测峰值必须是 **1**。

第 4 条是**负结果**，必须有：它是 29 的 B 组的机理版 —— 同一个函数、同一批调用、
只切一个开关，峰值就从 limit 掉到 1。没有它，哪天有人把 `asyncio.to_thread`
删掉，全部用例会照样绿，而并发已经没了。

## 峰值是量出来的

每个打桩任务进出时用一个自己带锁的计数器记「现在有几个在跑」，取历史最大值。
这个计数器本身就是并发发生的证据：真串行的话它永远只会看到 1。

## 为什么用 `asyncio.run` 而不是 `async def test_...`

本仓库没有装 `pytest-asyncio`（`requirements.txt` 里只有 `pytest`）。为一个同步
阻塞型工具层引入异步测试插件不划算：`asyncio.run()` 在这里是等价的，而且把
「这个测试自带一个事件循环」写得比装饰器更直白。
"""
from __future__ import annotations

import asyncio
import shutil
import threading
import time

import pytest

from mcp_server import auth, concurrency, dispatch


class _Gauge:
    """自己带锁的并发计量器：进 +1、出 -1，记历史最大同时数。

    锁在这里不是装饰：`offload=True` 时进出发生在不同的工作线程里，
    没有锁就是数据竞争，量出来的峰值会是个随机数。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.calls = 0

    def enter(self) -> None:
        with self._lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)

    def leave(self) -> None:
        with self._lock:
            self.now -= 1


def _sleepy(gauge: _Gauge, delay: float = 0.02, value: str = "ok"):
    """一个「占住并发额度一小会儿」的打桩任务。"""
    def task() -> str:
        gauge.enter()
        try:
            time.sleep(delay)
            return value
        finally:
            gauge.leave()
    return task


# ---------------------------------------------------------------- 断言 1
def test_the_measured_peak_equals_the_limit():
    """★ 断言 1：实测峰值 == limit。9 条调用、limit=3 → 峰值必须是 3。"""
    gauge = _Gauge()
    results = asyncio.run(concurrency.run_many([_sleepy(gauge) for _ in range(9)], limit=3))

    assert gauge.peak == 3, f"实测峰值 {gauge.peak}，limit 是 3"
    assert gauge.calls == 9, "9 条调用一条都不能少跑"
    assert concurrency.summarize(results) == {"n": 9, "ok": 9, "timeout": 0, "error": 0}


def test_the_peak_is_capped_by_the_limit_not_by_the_call_count():
    """limit 是**上限**：12 条调用、limit=4 → 峰值 4，不是 12。"""
    gauge = _Gauge()
    asyncio.run(concurrency.run_many([_sleepy(gauge) for _ in range(12)], limit=4))
    assert gauge.peak == 4


def test_the_limit_is_a_ceiling_not_a_quota():
    """调用数少于 limit 时跑不满 —— 峰值 == 调用数，不是 limit。"""
    gauge = _Gauge()
    asyncio.run(concurrency.run_many([_sleepy(gauge) for _ in range(3)], limit=8))
    assert gauge.peak == 3


# ---------------------------------------------------------------- 断言 4（负结果）
def test_naive_gather_measures_a_peak_of_one():
    """★ 断言 4：只把 `offload` 切成 False，实测峰值就从 limit 掉到 1。

    同一批调用、同一个函数、同一个 limit，差别只有「同步函数是在工作线程里跑
    还是在事件循环里直调」这一处。事件循环被阻塞函数按住，协程之间没有让出点，
    于是 `gather` 退化成串行 —— 这就是 29 的 B 组量到的那个 1。
    """
    peaks = {}
    for offload in (True, False):
        gauge = _Gauge()
        results = asyncio.run(concurrency.run_many(
            [_sleepy(gauge) for _ in range(6)], limit=3, offload=offload))
        peaks[offload] = gauge.peak
        # 慢不等于错：两种跑法的**结果**都必须一样，区别只在耗时。
        assert concurrency.summarize(results) == {"n": 6, "ok": 6, "timeout": 0, "error": 0}

    assert peaks[True] == 3
    assert peaks[False] == 1, "offload=False 还能跑出并发，说明这个开关没接上"


# ---------------------------------------------------------------- 断言 2
def test_one_timeout_leaves_the_other_calls_untouched():
    """★ 断言 2：超时 1 条 + 成功 N-1 条，且成功的那几条结果原样在位。"""
    gauge = _Gauge()
    calls = [_sleepy(gauge, value=f"q{i}") for i in range(5)]
    calls.append(_sleepy(gauge, delay=0.6, value="slow"))

    results = asyncio.run(concurrency.run_many(calls, limit=6, timeout=0.1))

    assert concurrency.summarize(results) == {"n": 6, "ok": 5, "timeout": 1, "error": 0}
    assert results[5]["timeout"] is True
    assert results[5]["error"] == "TimeoutError"
    # 记的是**设定值**（我们传进去的常量），不是实测耗时 —— 实测耗时进产物会让
    # 报告没法逐字节复现。
    assert results[5]["timeout_seconds"] == 0.1
    # 值必须对得上位：哪一条超时是确定的，其余五条的值是各自那条的。
    assert concurrency.successes(results) == [(0, "q0"), (1, "q1"), (2, "q2"),
                                              (3, "q3"), (4, "q4")]


def test_a_timeout_does_not_kill_the_thread_it_abandoned():
    """`wait_for` 超时只能**放弃 await**，不能杀线程。

    这条断言不判耗时（看耗时的断言会飘），只判「被放弃的那段代码跑到了最后一行」——
    它把 Event 置上了，说明没有任何机制真的中断了它。这是一个诚实的坏消息：
    超时保护的是调用方不会无限等，不是被调用的代码会停下来。
    """
    finished = threading.Event()

    def slow() -> str:
        time.sleep(0.3)
        finished.set()
        return "late"

    results = asyncio.run(concurrency.run_many([slow], limit=1, timeout=0.05))

    assert results[0]["timeout"] is True
    assert finished.wait(2.0), "被超时放弃的任务应当继续跑到底（Python 没法强杀线程）"


def test_no_timeout_means_no_timeout():
    """`timeout=None` 是「不限时」，不是「用默认值」。慢任务照样成功返回。"""
    results = asyncio.run(concurrency.run_many([_sleepy(_Gauge(), delay=0.15)], limit=2))
    assert concurrency.summarize(results)["ok"] == 1


# ---------------------------------------------------------------- 断言 3
def test_one_exception_is_counted_and_does_not_take_down_the_batch():
    """★ 断言 3：注入一个抛异常的任务 → 异常 1 条、其余照常返回。"""
    class Boom(RuntimeError):
        pass

    def bad() -> str:
        raise Boom("炸了")

    calls = [lambda: "a", bad, lambda: "b", bad, lambda: "c"]
    results = asyncio.run(concurrency.run_many(calls, limit=4))

    assert concurrency.summarize(results) == {"n": 5, "ok": 3, "timeout": 0, "error": 2}
    assert results[1]["error"] == "Boom", "记的是异常**类名**，不是一整篇 traceback"
    assert results[1]["timeout"] is False, "异常不是超时，两栏不能混着算"
    assert "炸了" in results[1]["detail"]
    assert concurrency.successes(results) == [(0, "a"), (2, "b"), (4, "c")]


def test_an_exception_is_not_mistaken_for_a_refusal():
    """**工具自己的拒绝**（无令牌）是一条成功的调用：没抛、没超时。

    容易混的一点：`run_many` 的 `ok` 说的是「这次调用没抛异常也没超时」，
    不是「工具说 ok」。工具自己的 `ok` / `denied` / `blocked` 原样在 `value` 里。
    这两件事混起来，报告里「成功率」就变成了另一件事的统计量。
    """
    refusal = dispatch.dispatch("list_tables", {})
    assert refusal["denied"] is True

    results = asyncio.run(concurrency.run_many([lambda: refusal], limit=1))
    assert concurrency.summarize(results) == {"n": 1, "ok": 1, "timeout": 0, "error": 0}
    assert results[0]["value"]["code"] == auth.E_NO_TOKEN


# ---------------------------------------------------------------- 形状不变量
def test_every_call_gets_exactly_one_result_row():
    """`len(results) == len(calls)` 恒成立。少一条比多一条错误难查得多。"""
    def bad() -> str:
        raise ValueError("坏了")

    calls = [lambda: "a", bad, _sleepy(_Gauge(), delay=0.4), lambda: "d"]
    results = asyncio.run(concurrency.run_many(calls, limit=2, timeout=0.05))

    assert len(results) == len(calls) == 4
    assert [r["index"] for r in results] == [0, 1, 2, 3], "顺序与入参一一对应，不是完成顺序"


def test_an_empty_batch_is_an_empty_result_not_an_error():
    assert asyncio.run(concurrency.run_many([], limit=3)) == []


def test_a_coroutine_function_is_awaited_where_it_stands():
    """协程函数不该被丢进线程池 —— 它自己会让出，直接 await 才是对的。

    判法：它跑在**有运行中的事件循环**的地方（`to_thread` 的线程里
    `asyncio.get_running_loop()` 会抛，`await fn()` 里不会）。
    顺带把此刻挂着的任务数也钉住：外层 `run_many` 一个 + `gather` 给每条调用
    各包一个 = 3。这个数变了说明调度形状变了（比如协程被多包了一层），
    那种改动值得有人看一眼。
    """
    seen: list[int] = []

    async def co() -> int:
        asyncio.get_running_loop()      # 不在事件循环上就会抛
        seen.append(len(asyncio.all_tasks()))
        return 7

    results = asyncio.run(concurrency.run_many([co, lambda: "sync"], limit=4))

    assert [v for _, v in concurrency.successes(results)] == [7, "sync"]
    assert seen == [3], "外层 run_many 1 个 + gather 给两条调用各包 1 个"


def test_a_limit_below_one_is_refused_loudly():
    """`limit=0` 静默当成 1，会让「我明明设了并发度」和实际行为对不上。"""
    with pytest.raises(ValueError):
        asyncio.run(concurrency.run_many([lambda: 1], limit=0))
    with pytest.raises(ValueError):
        asyncio.run(concurrency.run_many([lambda: 1], limit=-3))


def test_the_summary_columns_add_up_to_n():
    """`ok + timeout + error == n`。相加对不上的表，自己就先把读者骗了。"""
    def bad() -> str:
        raise ValueError("坏了")

    calls = [lambda: "a", bad, _sleepy(_Gauge(), delay=0.4), bad]
    results = asyncio.run(concurrency.run_many(calls, limit=3, timeout=0.05))
    s = concurrency.summarize(results)

    assert s == {"n": 4, "ok": 1, "timeout": 1, "error": 2}
    assert s["ok"] + s["timeout"] + s["error"] == s["n"]


# ---------------------------------------------------------------- 真工具走一遍
def test_the_real_tools_run_through_this_layer(tiny_db, tmp_path, monkeypatch):
    """打桩证明得了「并发是真的」，证明不了「这层能驱动真工具」。

    真 SQLite、真护栏、真派发，只是数据换成两表小库（理由同 `tests/conftest.py`：
    没跑过构建脚本的人也应当能跑测试）。顺带钉住一件事：**被护栏拦下的 SQL
    是一条成功的调用** —— 它没抛也没超时，`blocked` 在返回值里。
    """
    root = tmp_path / "data"
    (root / "chinook").mkdir(parents=True)
    shutil.copyfile(tiny_db, root / "chinook" / "chinook.db")
    monkeypatch.setenv("MCP_TOOLKIT_DATA_ROOT", str(root))

    token = auth.make_token("t", scopes=["db:read", "db:query"])
    calls = [
        lambda: dispatch.dispatch("list_tables", {"token": token}),
        lambda: dispatch.dispatch("get_schema", {"token": token, "table": "album"}),
        lambda: dispatch.dispatch("run_sql", {"token": token,
                                              "sql": "SELECT count(*) AS n FROM album"}),
        lambda: dispatch.dispatch("run_sql", {"token": token, "sql": "DROP TABLE album"}),
    ]
    results = asyncio.run(concurrency.run_many(calls, limit=2))

    assert concurrency.summarize(results) == {"n": 4, "ok": 4, "timeout": 0, "error": 0}
    by_index = {i: v for i, v in concurrency.successes(results)}

    assert by_index[0]["n_tables"] == 2
    assert sorted(t["name"] for t in by_index[0]["tables"]) == ["album", "artist"]
    assert by_index[1]["n_tables"] == 1
    assert [c["name"] for c in by_index[1]["tables"][0]["columns"]] == \
        ["id", "artist_id", "title", "year"]
    assert by_index[2]["rows"] == [[3]], "真查出来的行数：小库里 3 张专辑"
    assert by_index[3]["blocked"] is True, "DROP 必须被护栏拦下"
    assert by_index[3]["ok"] is False, "工具自己的 ok 在返回值里，与 run_many 的 ok 无关"
