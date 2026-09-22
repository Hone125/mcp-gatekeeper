"""并发下的账本对账：并发跑一批问题，再串行跑同一批，核对账本新增行数。

## 对账口径

`reports/usage_ledger.jsonl` 里**一行 = 一次模型调用**（`mcp_server/cost.py`
每次调用追加一行，重写重试各占一行）。本脚本把这条关系拿实测数字验一遍：

    账本新增行数  ==  实际模型调用次数

「实际调用次数」不靠估：`t2sql_core.run` 的返回体里带 `usage.calls`，它每发一次
请求就 +1，逐题加起来就是期望值。两个数都对得上，账本才是可信的 —— 否则
「这次评测花了多少」这句话就没有依据。

## 为什么这件事值得单独跑一遍

账本的写入是**旁路**的：`cost.record()` 包在 try 里，失败静默放过，绝不冒泡到
主链路（这是对的 —— 记账写不进去不该让用户的问题答不出来）。代价是：**它坏掉
的时候不会有任何报错**。并发一起跑，多个工作线程同时往同一个文件追加，
没有锁就可能写坏行、丢行、或者把两行粘在一起。所以「账本在并发下还准不准」
只能靠对账验，不能靠「它能跑」。

顺带一提：`cost.LOCK` 是 `threading.Lock`，而 `to_thread` 之前的调用全在事件循环
线程里、天然串行 —— 换句话说，**这把锁在有并发之前基本是个摆设，是并发层才让它
真正变得必要**。这个脚本跑的就是那个场景。

## 两批的差别只有并发度这一处

同一批问题、同一个令牌、同一份数据，第一批用 `run_many(limit=6)`，第二批用
`run_many(limit=1)`（并发上限压到 1 = 串行，但走的是同一条链路，不是另写一段
循环）。这样「并发 vs 串行」的差别指得出是哪一处造成的。

**注意：token 数两批不要求相等。** 模型不保证逐字复现（就算 temperature=0，
服务端也可能有非确定性）。要相等的是「账本行数 == 调用次数」这条对账关系，
不是两批的 token 数。

## 口径里要写明的两件事

- 数据是**现造在临时目录**的两表小库（`experiments/_tiny_data.py`），不依赖
  `data/` 是否已构建。对账关系与库有多大无关。
- **跑的时候不要同时开闸门**：对账算的是「账本行数增量」，另一个进程往同一个
  账本里写会把增量算到本脚本头上。脚本自己会核对新增行的来源标签是不是
  `t2sql:v3`，不是就判未完成 —— 这条自证能把「别人写的行」挡在门外。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 两批都对账成立（账本行数增量 == 调用次数），报告已写出 |
| 4 | 用法错误 |
| 5 | **未完成**：凭据不可用、或有调用没成功、或账本增量对不上。写出 `reports/ledger_under_concurrency_NOT_RUN.md`，**不编数字** |

凭据从**进程环境变量** `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 读（`env_file.apply()`
只负责把 `.env` 补进环境，显式环境变量永远优先）。本仓库**不放** `.env`：
`26_final_selfcheck.py` 第 7 项用 `**/.env` 全仓 glob，落一个 `.env` 会让发布自检
直接判 FAIL（见 `BLOCKERS.md` 卡点 1）。所以凭据只从环境变量注入，明文密钥不落仓库盘。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments import _tiny_data  # noqa: E402
from mcp_server import auth, concurrency, console, cost, dispatch, env_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_USAGE = 4
E_NOT_RUN = 5

LIMIT = concurrency.DEFAULT_LIMIT      # 并发批的上限
TIMEOUT = 90.0                          # 单题超时（秒）。超时算「没成功」→ 判未完成

SOURCE = "t2sql:v3"                     # 本脚本跑的链路在账本里的来源标签

# 12 道题，全部问得清、答案在两表小库里。刻意选**不重不漏**的题面，
# 好让「每题一次调用就成」成为常态 —— 但**不假设**它一定一次就成：
# 真重写了也算调用次数，对账口径本来就包含重试。
QUESTIONS = [
    "一共有几张专辑",
    "一共有几位歌手",
    "2005 年发行的专辑叫什么名字",
    "每位歌手各有几张专辑",
    "最早的专辑是哪一年发行的",
    "标题叫 First 的专辑是谁的",
    "有几张专辑是 2000 年以后发行的",
    "列出所有专辑的标题和发行年份",
    "哪位歌手的专辑最多",
    "发行专辑最多的年份是哪一年",
    "有没有 1999 年发行的专辑",
    "专辑标题里带 ir 的有几张",
]


def quiet_http_client_logs() -> list[str]:
    """把 HTTP 客户端库的 INFO 噪声压到 WARNING，返回**实际压过的 logger 名字**。

    ★ 这里不能只写 `logging.getLogger("httpx")`。本环境里 `openai==3.14.0` 实际用的
    是另一个包 `httpx2`（HTTPX2 是它 3.x 的默认客户端），日志 logger 名就叫 `httpx2`——
    压错了名字一行都压不掉，而且**看不出来**：现象只是「噪声还在」。第一次写这一行时
    压的就是 `httpx`，24 行噪声一行没少，查了一段才查出是名字不对。
    所以这里按名字前缀扫一遍当前已存在的 logger，并把压过的名字回报出来
    （可见 = 可复核）。
    """
    quieted: list[str] = []
    for name in sorted(logging.Logger.manager.loggerDict):
        if name.split(".")[0] in ("httpx", "httpx2", "httpcore", "httpcore2"):
            logging.getLogger(name).setLevel(logging.WARNING)
            quieted.append(name)
    return quieted


def _rel(p: Path) -> str:
    """报告里一律写仓库相对路径（绝对路径会把作者机器的目录结构带进对外产物）。"""
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- 账本快照
def _snapshot() -> dict:
    """读一次账本，记下「行数 + 逐行摘要」。两次快照相减就是这一批的增量。"""
    rows = cost.read_ledger()
    return {"n": len(rows), "rows": rows}


def _delta(before: dict, after: dict) -> dict:
    """本批新增的行（按追加顺序）。**按行数增量切**，不是按汇总表相减 ——
    汇总表是聚合过的，看不出一行一行到底写进去几条。"""
    new = after["rows"][before["n"]:]
    return {
        "n": len(new),
        "tok_in": sum(int(r.get("prompt_tokens") or 0) for r in new),
        "tok_out": sum(int(r.get("completion_tokens") or 0) for r in new),
        "sources": sorted({str(r.get("source")) for r in new}),
        "models": sorted({str(r.get("model")) for r in new}),
        "attempts_recorded": sum(1 for r in new if "attempt" in r),
    }


# ---------------------------------------------------------------- 跑一批
def _run_batch(label: str, limit: int, token: str, timeout: float) -> dict:
    """跑一批问题，返回「结果 + 账本增量」。**不改任何全局状态。**"""
    calls = [(lambda q=q: dispatch.dispatch("ask_database", {"question": q, "token": token}))
             for q in QUESTIONS]

    before = _snapshot()
    results = asyncio.run(concurrency.run_many(calls, limit=limit, timeout=timeout))
    after = _snapshot()

    summary = concurrency.summarize(results)
    ok_values = [v for _, v in concurrency.successes(results)]
    expected_calls = sum(int((v.get("usage") or {}).get("calls") or 0) for v in ok_values)
    # 每题各自几次调用，一并记下来：某题多花了一次，报告里能直接指出来。
    per_question = [{"question": v.get("question", ""), "status": v.get("status", ""),
                     "attempts": int(v.get("attempts") or 0),
                     "calls": int((v.get("usage") or {}).get("calls") or 0)}
                    for v in ok_values]
    return {
        "label": label,
        "limit": limit,
        "summary": summary,
        "expected_calls": expected_calls,
        "per_question": per_question,
        "ledger": _delta(before, after),
    }


# ---------------------------------------------------------------- 报告
def write_report(batches: list[dict], money_rows: list[dict], note: str) -> None:
    lines: list[str] = []
    A = lines.append

    A("# 并发下的账本对账（实测结果）")
    A("")
    A("本文件由 `experiments/31_ledger_under_concurrency.py` 生成，**不要手改**；"
      "重跑该脚本即可复现。")
    A("")
    A("## 口径")
    A("")
    A("- 工作量：**12 道题 × 2 批**（第一批并发上限 6，第二批并发上限 1 = 串行），"
      "同一个令牌、同一份数据、同一条链路（`dispatch` → `ask_database` → `t2sql_core`）。"
      "两批的差别只有并发度这一处。")
    A("- 数据：现造在临时目录的**两表小库**（`experiments/_tiny_data.py`），"
      "不依赖 `data/` 是否已构建。对账关系与库有多大无关。")
    A("- **一行账本 = 一次模型调用。** 状态机重写重试的那几次也各占一行 —— 重试是真花了钱的。")
    A("- 「实际调用次数」取自每题返回体里的 `usage.calls`（每发一次请求 +1），逐题相加，"
      "**不是估的**。")
    A("- 对账判据：**账本新增行数 == 期望调用次数**，逐批各判一次。")
    A("- **耗时读数不在本报告里**：只打终端。所以本文件不含任何秒数、日期、端口或绝对路径。")
    A("- 运行方式：`python experiments/31_ledger_under_concurrency.py`"
      "（退出码 0 = 两批对账都成立）。")
    if note:
        A(f"- {note}")
    A("")

    A("## 一、两批对照")
    A("")
    A("| 批次 | 并发上限 | 题数 | 调用成功 | 超时 | 异常 | 期望调用次数 | 账本新增行数 | 对账 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for b in batches:
        s = b["summary"]
        lg = b["ledger"]
        verdict = "**成立**" if lg["n"] == b["expected_calls"] else "**不成立**"
        A(f"| {b['label']} | {b['limit']} | {s['n']} | {s['ok']} | {s['timeout']} | "
          f"{s['error']} | {b['expected_calls']} | {lg['n']} | {verdict} |")
    A("")

    A("## 二、新增行的 token 与来源")
    A("")
    A("| 批次 | 新增行数 | 输入 token | 输出 token | 带 attempt 字段的行 | 来源标签 | 模型 |")
    A("|---|---|---|---|---|---|---|")
    for b in batches:
        lg = b["ledger"]
        A(f"| {b['label']} | {lg['n']} | {lg['tok_in']} | {lg['tok_out']} | "
          f"{lg['attempts_recorded']} | {', '.join(lg['sources']) or '—'} | "
          f"{', '.join(lg['models']) or '—'} |")
    A("")
    A("**两批的 token 数不要求相等。** 模型不保证逐字复现（就算 temperature=0，"
      "服务端也可能有非确定性），所以两批的输出长度可以不一样。要相等的是"
      "「账本行数 == 调用次数」这条对账关系 —— 把它和「两批花的一样多」混起来，"
      "就会拿一个本来不该成立的等式去判一个本来成立的关系。")
    A("")

    A("## 三、逐题明细")
    A("")
    for b in batches:
        A(f"**{b['label']}**（并发上限 {b['limit']}）")
        A("")
        A("| 题 | 状态 | 状态机尝试次数 | 记入账本的调用次数 |")
        A("|---|---|---|---|")
        for q in b["per_question"]:
            A(f"| {q['question']} | {q['status']} | {q['attempts']} | {q['calls']} |")
        A("")
    A("这两栏**都是同一个来源**（返回体里的 `usage.calls`），所以它们相等是构造性的，"
      "不算证据 —— 写出来只是为了看清「每题花了几次」。**真正的证据是第二节那一列**："
      "「账本新增行数」数的是**文件里真的多了几行**，与「状态机自己说发了几次请求」"
      "是两条独立的路径；两者相等，才说明记账没有漏行、没有重复记。")
    A("")

    A("## 四、金额")
    A("")
    A("| 模型 | 金额 | 说明 |")
    A("|---|---|---|")
    for m in money_rows:
        A(f"| {m['model']} | {m['amount']} | {m['reason']} |")
    A("")
    A("**金额留空不是遗漏。** 算金额要单价，单价要一个可引用的出处（价目页 + 抓取日期）；"
      "抓不到出处时唯一诚实的做法是留空并写明原因 —— 拿别家同尺寸模型的单价顶上、"
      "或者填 0（看起来像免费），都是编造。想填单价就把 `MCP_TOOLKIT_PRICES` 指向一个"
      "带 `source` 与 `as_of` 的 JSON 文件，见 `mcp_server/cost.py`。")
    A("")

    A("## 五、判定")
    A("")
    A("| 判定 | 结果 |")
    A("|---|---|")
    for b in batches:
        s = b["summary"]
        lg = b["ledger"]
        A(f"| {b['label']}：账本新增 {lg['n']} 行 == 期望 {b['expected_calls']} 次调用 | "
          f"{'✓' if lg['n'] == b['expected_calls'] else '✗'} |")
        A(f"| {b['label']}：无超时、无异常（否则对账口径不成立） | "
          f"{'✓' if s['timeout'] == 0 and s['error'] == 0 else '✗'} |")
        A(f"| {b['label']}：新增行的来源标签只有 `{SOURCE}`（没混进别的进程写的行） | "
          f"{'✓' if lg['sources'] == [SOURCE] else '✗'} |")
    A("")
    A("## 六、这把锁为什么不是摆设")
    A("")
    A("`cost.record()` 用 `threading.Lock` 护住「追加一行」这一步。在并发层出现之前，"
      "全部调用都发生在事件循环那一个线程里、天然串行，这把锁基本没机会起作用 ——"
      "它更像一个「以后会用到」的预留。`asyncio.to_thread` 把阻塞调用挪进工作线程之后，"
      "同时可能有 6 个线程各自 `record()` 一次：一行 JSON 长度超过一次 `write` 能保证"
      "原子的范围时，没有锁就可能出现两行粘在一起、或者半行。所以这个测试真正回答的是"
      "**「那把锁现在是不是真的在干活」**：两批对账都成立，说明并发写入没有丢行。")
    A("")
    A("诚实说明一句：账本是**单机评测账本，不是计费系统**。锁只管进程内，"
      "多进程同时写同一个文件仍然可能交错（`cost.py` 里写明了这一点）。这里跑的是"
      "一次进程内 6 并发，那个范围之外没有验。")
    A("")

    (REPORTS / "ledger_under_concurrency.md").write_text("\n".join(lines), encoding="utf-8")


def write_json(batches: list[dict], money_rows: list[dict]) -> None:
    payload = {
        "caliber": "账本新增行数 == 状态机实际调用次数（usage.calls 逐题相加）",
        "questions": len(QUESTIONS),
        "timeout_seconds_per_question": TIMEOUT,
        "batches": [{
            "label": b["label"], "limit": b["limit"],
            "summary": b["summary"], "expected_calls": b["expected_calls"],
            "ledger": b["ledger"], "per_question": b["per_question"],
        } for b in batches],
        "money": money_rows,
    }
    (REPORTS / "ledger_under_concurrency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_not_run(reason: str, detail: str) -> None:
    """未完成的合法痕迹。**同时把可能过期的既有产物撤掉** ——

    `verify_kit.artifact_checks` 的 A1 判「跑过了」与「没跑成」两块牌子同时挂着，
    读报告的人只会看到其中一块。所以这里不是「删掉一个麻烦的文件」，是**把不再成立
    的结论撤下来**：对账没做成的这一轮，上一轮的 json 不再代表任何东西。
    """
    stale = REPORTS / "ledger_under_concurrency.json"
    removed = ""
    if stale.is_file():
        stale.unlink()
        removed = f"\n- 已撤下上一轮的 `{_rel(stale)}`：这一轮没做成，那份结论不再代表任何东西。"
    text = "\n".join([
        "# 并发下的账本对账：未完成",
        "",
        f"**卡在哪一步**：{reason}",
        "",
        f"- 说明：{detail}",
        "- 口径：账本新增行数应当等于状态机实际调用次数（逐题 `usage.calls` 相加）。"
        "这一轮**没有跑通**，所以没有可报的数字。",
        "- **最小动作**：在进程环境里提供 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`"
        "（`.env` 也认，但本仓库刻意不落 `.env`，见 `BLOCKERS.md` 卡点 1），"
        "然后重跑 `python experiments/31_ledger_under_concurrency.py`。",
        removed,
        "",
    ])
    (REPORTS / "ledger_under_concurrency_NOT_RUN.md").write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    console.setup_stdio()
    # HTTP 客户端每次请求打一行 INFO（走 stderr），一次 12 题就是十几行噪声，
    # 把「跑出来什么」淹掉了。压到 WARNING，并把压了哪些名字打出来。
    print(f"[静音] HTTP 客户端库的 INFO 已压到 WARNING："
          f"{', '.join(quiet_http_client_logs()) or '（没找到）'}")
    ap = argparse.ArgumentParser(description="并发下的账本对账")
    ap.add_argument("--limit", type=int, default=LIMIT, help=f"并发批的上限，默认 {LIMIT}")
    args = ap.parse_args(argv)
    if args.limit < 1:
        print("[用法] --limit 至少为 1", file=sys.stderr)
        return E_USAGE

    env_file.apply()
    missing = [k for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL") if not os.environ.get(k)]
    if missing:
        reason = f"缺少环境变量：{', '.join(missing)}"
        print(f"[未完成] {reason}", file=sys.stderr)
        write_not_run(reason, "本脚本要真调模型才能对账，没有凭据就没有数字。")
        return E_NOT_RUN

    with tempfile.TemporaryDirectory() as td:
        os.environ["MCP_TOOLKIT_DATA_ROOT"] = str(_tiny_data.build(Path(td) / "data", docs=False))
        token = auth.make_token("ledger-bench", scopes=["db:query"])

        batches = []
        for label, limit in (("第一批（并发）", args.limit), ("第二批（串行）", 1)):
            print(f"[跑] {label}：上限 {limit}，{len(QUESTIONS)} 题")
            b = _run_batch(label, limit, token, TIMEOUT)
            print(f"     成功 {b['summary']['ok']} / 超时 {b['summary']['timeout']} / "
                  f"异常 {b['summary']['error']}；期望调用 {b['expected_calls']} 次，"
                  f"账本新增 {b['ledger']['n']} 行")
            batches.append(b)

    # 对账不成立就不是「通过」—— 这一轮没有可报的数字。
    problems = []
    for b in batches:
        s, lg = b["summary"], b["ledger"]
        if s["ok"] != s["n"]:
            problems.append(f"{b['label']}：{s['timeout']} 条超时、{s['error']} 条异常，"
                            "对账口径要求全部成功，否则期望值算不全")
        if lg["sources"] != [SOURCE]:
            problems.append(f"{b['label']}：新增行的来源标签是 {lg['sources']}，"
                            f"只有 {SOURCE} 才是本脚本写的（别的进程动过账本？）")
        if lg["n"] != b["expected_calls"]:
            problems.append(f"{b['label']}：账本新增 {lg['n']} 行，期望 {b['expected_calls']} 次")
    if problems:
        reason = "对账不成立"
        detail = "；".join(problems)
        print(f"[未完成] {reason}：{detail}", file=sys.stderr)
        write_not_run(reason, detail)
        return E_NOT_RUN

    prices = cost.load_prices()
    money_rows = []
    for model in sorted({m for b in batches for m in b["ledger"]["models"]}):
        tok_in = sum(b["ledger"]["tok_in"] for b in batches if model in b["ledger"]["models"])
        tok_out = sum(b["ledger"]["tok_out"] for b in batches if model in b["ledger"]["models"])
        amount, why = cost.money(model, tok_in, tok_out, prices)
        money_rows.append({
            "model": model,
            "amount": "—" if amount is None else str(amount),
            "reason": why,
        })

    note = ("凭据从**进程环境变量**注入，明文密钥不落仓库盘（本仓库不放 `.env`："
            "`26_final_selfcheck.py` 第 7 项用 `**/.env` 全仓 glob，落一个就判 FAIL）。")
    write_report(batches, money_rows, note)
    write_json(batches, money_rows)

    stale = REPORTS / "ledger_under_concurrency_NOT_RUN.md"
    if stale.is_file():          # 上一轮没跑成留下的牌子，这一轮跑成了就要撤掉
        stale.unlink()
        print(f"[清理] 撤下过期的 {_rel(stale)}")

    print(f"[OK] 两批对账都成立；已写入 {_rel(REPORTS / 'ledger_under_concurrency.md')}"
          f" 与 {_rel(REPORTS / 'ledger_under_concurrency.json')}")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
