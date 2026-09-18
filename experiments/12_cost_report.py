"""用量与成本报告：读账本 → 汇总成 `reports/cost_report.md`。

## 这个脚本**不调用任何模型**

它只读 `reports/usage_ledger.jsonl`（`mcp_server/cost.py` 每次调用追加一行）。
所以它随时能跑，包括在还没有任何调用的时候。

## 口径

- **一行 = 一次模型调用。** 状态机重写重试的那几次也各占一行 ——
  重试是真花了钱的，不记进来账本就是假的。
- **调用次数 / token 是实测的**（模型返回的 usage 里带的），
  **金额是算出来的**。两者性质不同，报告里分栏摆。
- **金额允许为空。** 算金额要单价，单价要一个可引用的出处（价目页 + 抓取日期）。
  没有出处时唯一诚实的做法是**留空并写明原因** —— 见 `cost.money()`：
  它**绝不返回 0**，也绝不用别家模型的单价兜底。
  想填单价就把 `MCP_TOOLKIT_PRICES` 指向一个 JSON 文件。
- **`source` 字段**记的是**提示词版本标签**（`v1` / `v2` / `v3`），不是链路名。
  ★ 原本这里写的是「说明哪条链路花的（`text2sql` / `colloquial` / `noise_band` …）」——
  那句话与实测不符：跑完之后账本里只出现 `v1` / `v2` / `v3` 三种取值，
  口语对照与噪声带两轮的调用都记在 `v3` 名下。**「哪个实验烧钱」因此只能靠算术反推，
  账本自己答不了** —— 实测数字见 `RESULTS.md` 第四节。
- **假模型的用量不进账本**（评测脚本传 `record_cost=False`），
  否则自检会把成本报告变成假的。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 账本里有内容，报告已写出 |
| 4 | 用法错误 |
| 5 | **账本为空或不存在** —— 报告照写，但内容写明「未完成」而不是「花了 0 元」 |
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, cost, paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_USAGE = 4
E_NO_LEDGER = 5


def _rel(p: Path) -> str:
    """报告里一律写**仓库相对路径**。

    ★ 绝对路径会把作者机器的目录结构带进对外产物（`C:\\Users\\19070\\...`），
    别人 clone 下来看到的是一串和他无关的东西，而且这条信息没有任何用处。
    `tests/test_repo_hygiene.py` 里有一条检查专门扫这个。
    """
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def write_report(rows: list[dict], ledger_rows: list[dict], prices: dict,
                 prices_note: str) -> None:
    lines: list[str] = []
    A = lines.append
    A("# 用量与成本")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/12_cost_report.py`（只读账本，**不调用模型**）")
    A(f"- **账本**：`{_rel(cost.ledger_path())}`，JSONL，一行 = 一次模型调用")
    A("- **调用次数 / token**：实测值，来自模型返回的 usage；**重写重试的各占一行、各算一次**")
    A("- **金额**：算出来的，需要单价。单价必须有出处（价目页 URL + 抓取日期），"
      "否则金额栏留 `—` 并写明原因 —— **不用别家单价兜底，也不填 0**")
    A(f"- **单价来源**：{prices_note}")
    A("- **`source` 是什么**：账本按 (`source`, 模型) 汇总，而 `source` 记的是"
      "**提示词版本标签**（`v1` / `v2` / `v3`），**不是链路名**。")
    A("  ★ 这是**已知边界**，照实写在这里：口语对照的那一轮与噪声带的两轮，"
      "调用都记在 `v3` 名下 —— 账本自己分不出它们，只能看出「v3 这个名字下花了多少次」。"
      "原因见 `RESULTS.md` 第四节。")
    A("- **不在账本里的**：假模型（`--fake`）的用量。评测脚本传 `record_cost=False`，"
      "自检不产生成本")
    A("")

    if not ledger_rows:
        A("## 未完成：账本为空")
        A("")
        A(f"`{_rel(cost.ledger_path())}` 不存在或没有任何有效行。**这不是「花了 0 元」** —— "
          "它是「还没有任何一次真实模型调用被记过账」。")
        A("")
        A("原因见 `BLOCKERS.md`：Text2SQL 评测需要一份可用的模型凭据，"
          "本仓库没有配置，也没有编造任何数字。")
        A("")
        A("已经就绪、不需要模型的部分：")
        A("")
        A("- 记账本身由 `tests/test_cost.py` 钉住（含「坏行不该让报告生成不出来」）。")
        A("- 金额留空的规则由 `cost.money()` 强制：缺 `source` / `as_of` 就返回 `None`。")
        A("- 假模型自检证明整条评测链路跑得通，且不往账本里写任何东西"
          "（`experiments/09_text2sql_eval.py --fake oracle|naive|flaky`）。")
        A("")
        (REPORTS / "cost_report.md").write_text("\n".join(lines), encoding="utf-8")
        return

    A("## 汇总")
    A("")
    A("| 来源标签 | 模型 | 调用次数 | 输入 token | 输出 token | 金额 | 单价口径 |")
    A("|---|---|---|---|---|---|---|")
    total_calls = total_in = total_out = 0
    for r in rows:
        amount, why = cost.money(r["model"], r["tok_in"], r["tok_out"], prices)
        total_calls += r["calls"]
        total_in += r["tok_in"]
        total_out += r["tok_out"]
        money_cell = "—" if amount is None else f"{amount:.6f}"
        why_cell = why if amount is None else f"{why}（{amount:.6f}）"
        A(f"| {r['src']} | {r['model']} | {r['calls']} | {r['tok_in']} | {r['tok_out']} "
          f"| {money_cell} | {why_cell} |")
    A(f"| **合计** | — | **{total_calls}** | **{total_in}** | **{total_out}** | — | — |")
    A("")
    if all(cost.money(r["model"], r["tok_in"], r["tok_out"], prices)[0] is None
           for r in rows):
        A("> 金额全部留空：没有可引用的单价。**这不等于免费** —— "
          "要算金额，把 `MCP_TOOLKIT_PRICES` 指向一个带 `source` / `as_of` 的单价 JSON。")
        A("")

    A("## 按来源标签分布")
    A("")
    by_src = Counter()
    for r in ledger_rows:
        by_src[r.get("source") or "(未标注)"] += 1
    A("| 来源标签 | 调用次数 |")
    A("|---|---|")
    for src, n in by_src.most_common():
        A(f"| {src} | {n} |")
    A("")
    A("## 逐条明细")
    A("")
    A("| 时间 | 来源标签 | 模型 | 输入 token | 输出 token |")
    A("|---|---|---|---|---|")
    for r in ledger_rows:
        A(f"| {r.get('ts', '')} | {r.get('source', '')} | {r.get('model', '')} "
          f"| {r.get('prompt_tokens', 0)} | {r.get('completion_tokens', 0)} |")
    A("")
    (REPORTS / "cost_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="用量与成本报告（只读账本）")
    ap.add_argument("--json", action="store_true", help="同时落一份 cost_report.json")
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)

    try:
        prices = cost.load_prices()
        prices_note = ("来自 `MCP_TOOLKIT_PRICES` 指向的文件"
                       if prices else "未配置 —— **金额留空**（本仓库不带内置单价）")
    except (cost.PriceError, OSError, json.JSONDecodeError) as e:
        # 单价表本身坏了要当场说，不许降级成「无单价」——
        # 那会把「我配错了」伪装成「本来就没有单价」。
        print(f"[FAIL] 单价表有问题：{e}", file=sys.stderr)
        return E_USAGE

    ledger_rows = cost.read_ledger()
    rows = cost.ledger_summary()
    write_report(rows, ledger_rows, prices, prices_note)

    if not ledger_rows:
        print(f"[未完成] 账本为空：{paths.rel(cost.ledger_path())}")
        print("         报告已写出（内容写明是「未完成」而不是「花了 0 元」）：")
        print(f"         {paths.rel(REPORTS / 'cost_report.md')}")
        return E_NO_LEDGER

    total_calls = sum(r["calls"] for r in rows)
    total_in = sum(r["tok_in"] for r in rows)
    total_out = sum(r["tok_out"] for r in rows)
    print(f"[OK] 账本共 {len(ledger_rows)} 行，"
          f"{total_calls} 次调用，token {total_in}+{total_out}")
    print(f"     报告：{paths.rel(REPORTS / 'cost_report.md')}")
    if all(cost.money(r["model"], r["tok_in"], r["tok_out"], prices)[0] is None
           for r in rows):
        print("     金额留空：没有可引用的单价（不等于免费）")

    if args.json:
        (REPORTS / "cost_report.json").write_text(json.dumps({
            "ledger": _rel(cost.ledger_path()),
            "n_lines": len(ledger_rows),
            "by_source_model": rows,
            "prices_note": prices_note,
            "note": "token 是实测的，金额是算出来的；没有单价时金额为 null 而不是 0。",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
