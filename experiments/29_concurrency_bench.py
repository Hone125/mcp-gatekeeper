"""并发实测：同一批调用，三种跑法，量出「同时在跑几条」。

## 三种跑法只差一处

| 跑法 | 怎么写 | 差在哪 |
|---|---|---|
| **A 纯串行** | 一条一条 `await`，每条都丢进线程 | 对照组：完全没有并发 |
| **B 天真并发** | `run_many(..., offload=False)`：`gather` 一批**同步**函数 | 协程之间没有让出点 |
| **C 正确并发** | `run_many(..., limit=6, offload=True)`：同步函数挪进线程 | ★ 唯一正确的那个 |

B 与 C 用的是**同一个函数、同一份调用清单**，唯一的差别就是 `offload` 这个开关。
这样报告里的差别才说得清是这一处造成的，而不是「两段不同的代码各有各的写法」。

## 量的是什么，不是量什么

量的是**实测最大并发度**：每次调用进出时过一个带锁的计数器，记下同时在里面
的最大数量。它是一个**量出来的数**，不是「我设了 6 所以是 6」的推论 ——
B 的失败恰恰在于「设了 6 但实际是 1」，不量就看不见。

**不量「快了多少倍」当结论**：耗时读数只打终端、另存 `cache/`（那个目录不入库）。
报告里只有计数，所以同一份代码跑两遍，`git status` 是干净的。

## 那条固定的 20ms 延迟是干什么的

每条调用额外背一个固定的 `IO_DELAY`（见下）。它是**测量工具**，不是工作负载：

- 真的活是照干的（真 SQL、真 FTS 检索、真返回行），延迟只是叠在上面；
- 没有它，一次查询在微秒级跑完，6 个线程还没都站起来前一批就结束了 ——
  「同时在跑几条」会变成纯粹的调度噪声，**同样的代码跑两遍能量出 5 也能量出 6**，
  那样报告就没法逐字节复现了；
- 20ms 是一次本地磁盘 I/O 或一次局域网站内请求的量级，不夸张。

报告里的「口径」一节把这件事写明了。不说明白的延迟叫造假，说明白的叫量具。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments import _tiny_data  # noqa: E402
from mcp_server import auth, concurrency, console, dispatch, paths  # noqa: E402

REPORTS = paths.reports_dir()

N = 24                 # 调用条数
LIMIT = 6              # 并发上限
IO_DELAY = 0.02        # 每条调用额外背的固定 I/O 延迟（秒）—— 见文件头
SLOW_DELAY = 1.0       # 超时探针里那条「必然超时」的调用的延迟
PROBE_TIMEOUT = 0.25   # 超时探针的单条超时设定

KINDS = ("run_sql", "get_schema", "search_passages")
QUERIES = ("茶", "兵法")


class Peak:
    """带锁的并发计量器：进一次 +1、出一次 -1，记下最大值。

    锁是必须的：几个工作线程会同时改这两个数。没有锁的话**读数本身**
    就是数据竞争的结果，那样量出来的「最大并发」比不量还糟 —— 它看起来像个数。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.entries = 0

    def __enter__(self) -> "Peak":
        with self._lock:
            self.now += 1
            self.entries += 1
            self.peak = max(self.peak, self.now)
        return self

    def __exit__(self, *exc) -> None:
        with self._lock:
            self.now -= 1


def tokens() -> dict[str, str]:
    """三把钥匙，各自只开自己那扇门。"""
    return {
        "run_sql": auth.make_token("bench", ["db:query"]),
        "get_schema": auth.make_token("bench", ["db:read"]),
        "search_passages": auth.make_token("bench", ["kb:read"]),
    }


def call_args(kind: str, i: int, tok: dict[str, str]) -> dict:
    if kind == "run_sql":
        who = i % 2 + 1
        return {"sql": ("SELECT a.title, a.year FROM album a "
                        "JOIN artist ar ON ar.id = a.artist_id "
                        f"WHERE ar.id = {who} ORDER BY a.id"),
                "token": tok["run_sql"]}
    if kind == "get_schema":
        return {"token": tok["get_schema"]}
    return {"query": QUERIES[i % len(QUERIES)], "top_k": 3, "token": tok["search_passages"]}


def build_calls(n: int, meter: Peak, tok: dict[str, str], delay: float,
                slow_index: int | None = None) -> list:
    """造 n 条零参可调用，每条都在计量器里跑。"""
    calls = []
    for i in range(n):
        kind = KINDS[i % len(KINDS)]
        args = call_args(kind, i, tok)
        wait = delay if slow_index is None or i != slow_index else SLOW_DELAY

        def one(kind=kind, args=args, wait=wait):
            with meter:
                time.sleep(wait)          # 量具，见文件头
                return dispatch.dispatch(kind, args)

        calls.append(one)
    return calls


def _business_ok(value) -> bool:
    """这条调用**真的干成了活**吗 —— 不是「没抛异常」，是业务层说 ok。"""
    return isinstance(value, dict) and value.get("ok") is True and value.get("denied") is not True


def run_mode(mode: str, calls: list, meter: Peak) -> tuple[list[dict], float]:
    """跑一批，返回（结果, 耗时）。耗时只用于终端输出，不进产物。"""
    async def main() -> list[dict]:
        if mode == "A":
            # 纯串行：一条一条来，每条都丢进线程（所以阻塞 I/O 仍然不占事件循环，
            # 只是完全没有并发）。
            out = []
            for i, fn in enumerate(calls):
                try:
                    out.append({"ok": True, "index": i,
                                "value": await asyncio.to_thread(fn)})
                except Exception as e:  # noqa: BLE001
                    out.append({"ok": False, "index": i, "timeout": False,
                                "error": type(e).__name__, "detail": str(e)[:200]})
            return out
        if mode == "B":
            return await concurrency.run_many(calls, limit=LIMIT, offload=False)
        return await concurrency.run_many(calls, limit=LIMIT, offload=True)

    t0 = time.monotonic()
    results = asyncio.run(main())
    return results, time.monotonic() - t0


def tally(results: list[dict]) -> dict:
    """一条跑法的计数。**全部是数出来的，没有一个是从设定推出来的。**"""
    s = concurrency.summarize(results)
    values = [r["value"] for r in results if r.get("ok")]
    return {
        "calls": len(results),
        "business_ok": sum(1 for v in values if _business_ok(v)),
        "denied": sum(1 for v in values if isinstance(v, dict) and v.get("denied") is True),
        "timeout": s["timeout"],
        "error": s["error"],
    }


def main() -> int:
    console.setup_stdio()
    REPORTS.mkdir(parents=True, exist_ok=True)
    cache = paths.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mcp-bench-") as td:
        # 数据现造：这个实验的结论（并发度是多少）跟数据有多大毫无关系，
        # 所以不该依赖 `data/` 下那两份构建产物是否已经构建过。
        os.environ["MCP_TOOLKIT_DATA_ROOT"] = str(_tiny_data.build(Path(td)))
        tok = tokens()

        # 预热：第一次 jieba 分词要把词典读进内存，第一次连库要建连接。
        # 把它们留在计时之外，否则 A 组（跑在最前面）会独自背下这份一次性开销。
        for kind in KINDS:
            dispatch.dispatch(kind, call_args(kind, 0, tok))

        modes = {}
        for mode in ("A", "B", "C"):
            meter = Peak()
            calls = build_calls(N, meter, tok, IO_DELAY)
            results, dt = run_mode(mode, calls, meter)
            modes[mode] = {"tally": tally(results), "peak": meter.peak,
                           "entries": meter.entries, "seconds": dt}

        # 超时探针：1 条必然超时 + N-1 条正常，断言超时只吃掉那一条。
        probe_meter = Peak()
        probe_calls = build_calls(N, probe_meter, tok, IO_DELAY, slow_index=0)
        probe_results = asyncio.run(concurrency.run_many(
            probe_calls, limit=LIMIT, timeout=PROBE_TIMEOUT, offload=True))
        probe = concurrency.summarize(probe_results)
        probe_slow = probe_results[0]

    data = {
        "n": N, "limit": LIMIT, "io_delay_ms": int(IO_DELAY * 1000),
        "modes": {m: {**v["tally"], "peak": v["peak"], "entries": v["entries"]}
                  for m, v in modes.items()},
        "probe": {"n": probe["n"], "ok": probe["ok"], "timeout": probe["timeout"],
                  "error": probe["error"],
                  "first_is_timeout": bool(probe_slow.get("timeout")),
                  "first_error": probe_slow.get("error")},
    }
    data["verdict"] = {
        "serial_peak_is_1": data["modes"]["A"]["peak"] == 1,
        "naive_peak_is_1": data["modes"]["B"]["peak"] == 1,
        "correct_peak_hits_limit": data["modes"]["C"]["peak"] == LIMIT,
        "all_modes_did_the_same_work": len({data["modes"][m]["business_ok"]
                                            for m in "ABC"}) == 1,
        "probe_degraded_exactly_one": data["probe"]["timeout"] == 1
        and data["probe"]["ok"] == N - 1,
    }
    data["ok"] = all(data["verdict"].values())

    (REPORTS / "concurrency.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / "concurrency.md").write_text(render_md(data), encoding="utf-8", newline="\n")
    # 耗时只到这里，不进 reports/。cache/ 在 .gitignore 里。
    (cache / "concurrency_timing.json").write_text(
        json.dumps({m: round(v["seconds"], 3) for m, v in modes.items()},
                   ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    print("=" * 72)
    print("并发实测：同一批调用，三种跑法")
    print("=" * 72)
    print(f"  调用条数 {N}，并发上限 {LIMIT}，"
          f"每条额外背 {data['io_delay_ms']} ms 固定 I/O 延迟（量具，见报告的「口径」）")
    print("-" * 72)
    print(f"  {'跑法':<26}{'调用':>6}{'成功':>6}{'超时':>6}{'异常':>6}{'实测峰值':>10}{'耗时':>10}")
    for mode, label in (("A", "A 纯串行"), ("B", "B 天真 gather"), ("C", "C 正确并发")):
        t, v = modes[mode]["tally"], modes[mode]
        print(f"  {label:<26}{t['calls']:>6}{t['business_ok']:>6}{t['timeout']:>6}"
              f"{t['error']:>6}{v['peak']:>10}{v['seconds']:>9.2f}s")
    print("-" * 72)
    print(f"  超时探针：{probe['ok']} 条成功 + {probe['timeout']} 条超时"
          f"（超时那条的 error={probe_slow.get('error')}）")
    print("-" * 72)
    if data["ok"]:
        print(f"  ✓ A 峰值 1、B 峰值 1、C 峰值 {LIMIT} —— "
              "「设了 6」与「真的是 6」这次对上了")
    else:
        print("  ✗ 有判定没通过，逐条看下面：")
        for k, v in data["verdict"].items():
            print(f"      {'✓' if v else '✗'} {k}")
    print("\n已写入 reports/concurrency.md 与 reports/concurrency.json")
    print(f"耗时读数已另存 {paths.cache_dir().name}/concurrency_timing.json（不入库）")
    return 0 if data["ok"] else 1


def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_md(d: dict) -> str:
    M = d["modes"]
    L: list[str] = []
    L.append("# 并发调用实测：三种跑法的对照（实测结果）\n")
    L.append("本文件由 `experiments/29_concurrency_bench.py` 生成，**不要手改**；"
             "重跑该脚本即可复现。\n")

    L.append("## 口径\n")
    L.append(f"- 工作量：**零凭据**工具混着跑，共 **{d['n']}** 条调用"
             "（`run_sql` 连表查询 / `get_schema` 全库结构 / `search_passages` 检索），"
             "令牌由 `auth.make_token` 现造。不走 HTTP、不调模型、不联网。")
    L.append(f"- 并发上限 **{d['limit']}**（`concurrency.DEFAULT_LIMIT`）。")
    L.append(f"- **每条调用额外背一个固定的 {d['io_delay_ms']} ms 延迟**，这是"
             "**量具**不是工作负载：真活照干（真 SQL、真 FTS、真返回行），延迟叠在上面。"
             "没有它，一次查询在微秒级跑完，多个线程还没都站起来前一批就结束了，"
             "「同时在跑几条」会变成调度噪声 —— 同样的代码跑两遍能量出不同的峰值，"
             "报告也就没法逐字节复现。")
    L.append("- **实测最大并发度是量出来的**：每条调用进出时过一个带 `threading.Lock` "
             "的计数器，取「同时在里面」的最大值。它是读数，不是「设了 6 所以是 6」"
             "的推论 —— B 的失败恰恰是「设了 6、实际 1」，不量就看不见。")
    L.append("- **耗时读数不在本报告里**：只打终端，并另存"
             " `cache/concurrency_timing.json`（`cache/` 在 `.gitignore` 里）。"
             "所以本文件不含任何秒数、日期、端口或绝对路径，可以逐字节复现。")
    L.append("- 数据现造在临时目录，**不依赖 `data/` 是否已构建**；"
             "计时前先预热三种工具各一次（第一次 jieba 分词要读词典），"
             "避免这份一次性开销被跑在最前面的那一组独自背下。")
    L.append("- 运行方式：`python experiments/29_concurrency_bench.py`（退出码 0 = 全部判定通过）\n")

    L.append("## 一、三种跑法\n")
    L.append(_table(
        [[label,
          M[m]["calls"], M[m]["business_ok"], M[m]["timeout"], M[m]["error"],
          f"**{M[m]['peak']}**"] for m, label in
         (("A", "A 纯串行（一条一条 await）"),
          ("B", "B 天真 gather（同步函数直接并发）"),
          ("C", "C 正确并发（`to_thread` + 信号量）"))],
        ["跑法", "调用数", "业务成功", "超时", "异常", "实测最大并发"]))
    L.append("")
    L.append("B 与 C 用的是**同一个函数、同一份调用清单**，唯一差别是 `offload` 开关。"
             "C 的峰值等于并发上限，说明限流真的生效了；B 的峰值是 1，"
             "说明那 24 条调用**实际上一条一条跑完的**。\n")

    L.append("## 二、为什么 B 不提速\n")
    L.append("因为**同步阻塞函数没有让出点**。`asyncio.gather` 能让一批协程交错，"
             "前提是每个协程在等 I/O 时把控制权**交还**给事件循环。"
             "而一个普通的同步函数从头跑到尾，中间一次都不交还 —— "
             "于是 24 个协程排队进场，每一个都把事件循环按到底，"
             "下一个连开始的机会都没有。**`gather` 退化成串行。**\n")
    L.append("这不是 `asyncio` 的缺陷，是它的契约：协程只在 `await` 处让出。"
             "要让阻塞调用真的并发，得把它挪到别的线程上（`asyncio.to_thread`），"
             "让事件循环只负责调度 —— 那就是 C 组。\n")
    L.append(f"顺带一句：C 组的线程数正好是 {d['limit']}，不是 24。"
             "限流那层（`asyncio.Semaphore`）挡住的是**同时在跑的条数**，"
             "不是「总共能跑多少条」—— 24 条全部跑完，任何时刻最多 "
             f"{d['limit']} 条在里面。\n")

    L.append("## 三、超时降级\n")
    L.append(f"另跑一批：{d['probe']['n']} 条里第 1 条必然超时，单条超时设定"
             f"取自 `PROBE_TIMEOUT`，其余正常。\n")
    L.append(_table([[d["probe"]["n"], d["probe"]["ok"], d["probe"]["timeout"],
                      d["probe"]["error"],
                      "是" if d["probe"]["first_is_timeout"] else "否"],
                     ], ["调用数", "成功", "超时", "异常", "第 1 条确实是超时那条"]))
    L.append("")
    L.append("**一条超时没有拖垮其余**：超时被降级成一条结果记录（`timeout: True`），"
             "同一批里其余照常返回。这就是「降级」的含义 —— 不是把整批判失败，"
             "是让调用方拿到「哪几条没成、为什么没成」。\n")
    L.append("诚实说明一句：`wait_for` 超时只能**放弃 `await`**。被超时的那条如果是"
             "跑在线程里的阻塞调用，那个线程会继续跑到自然结束（Python 没法强杀线程）。"
             "所以超时保护的是**调用方**不会无限等下去，不是那段代码被中断了。\n")

    L.append("## 四、判定\n")
    L.append(_table([["A（串行）实测峰值 == 1", "1",
                      "✓" if d["verdict"]["serial_peak_is_1"] else "✗"],
                     ["★ B（天真并发）实测峰值 == 1 —— 负结果，如实保留", "1",
                      "✓" if d["verdict"]["naive_peak_is_1"] else "✗"],
                     [f"★ C（正确并发）实测峰值 == 上限 {d['limit']}", str(d["limit"]),
                      "✓" if d["verdict"]["correct_peak_hits_limit"] else "✗"],
                     ["三种跑法的业务成功数相同（干的是同一份活）",
                      str(M["A"]["business_ok"]),
                      "✓" if d["verdict"]["all_modes_did_the_same_work"] else "✗"],
                     ["超时探针：恰好 1 条超时 + 其余全成",
                      f"{d['probe']['timeout']} / {d['probe']['ok']}",
                      "✓" if d["verdict"]["probe_degraded_exactly_one"] else "✗"]],
                    ["判定", "期望", "结果"]))
    L.append("")
    L.append("**B 那一行是刻意留在报告里的负结果。** 它没有通过，也没有被删掉 ——"
             "一个「并发封装」如果不把阻塞调用挪出事件循环，"
             "它给出的速度是假的，而调用方看不出来：接口一样、结果一样、只有耗时不一样。"
             "这正是要靠实测抓住的那类问题。\n")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
