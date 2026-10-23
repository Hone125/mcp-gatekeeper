"""手写版 vs LangGraph 版：同一天、同一个模型、同一套 36 题的逐题对照。

## 它回答什么

两版的控制流已经由 `tests/test_t2sql_state_machine.py` 证明等价：同一剧本下，发给模型的
每一轮消息、返回体逐字相同。所以接上真模型以后，两版之间的任何差异**只可能来自模型自己的
不确定性** —— 这份对照因此同时是一次「同一臂连跑两遍」：既报两版各自的数，也报两版之间
翻转了几道题，再拿翻转数去和 `21_noise_band.py` 早先实测的噪声带比。

## 用法（三条命令，前两条各跑一遍 36 题）

    python experiments/09_text2sql_eval.py --versions v3 --impl hand --tag 20261023
    python experiments/09_text2sql_eval.py --versions v3 --impl graph --tag 20261023
    python experiments/33_impl_compare.py --tag 20261023

本脚本**不调模型**：只读前两条落盘的逐题记录，产出 `reports/impl_compare.<tag>.md`。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 对照报告已写出 |
| 2 | 报告里出现禁用词（报告已落盘，但措辞越界） |
| 3 | 缺输入：那两次 09 还没跑，或两份记录的题号对不齐 |
| 4 | 用法错误 |
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, env_file, eval_grading as eg  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_BAD_WORDING = 2
E_NO_INPUT = 3
E_USAGE = 4

_RE_TAG = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# (键, 给人看的名字, 09 的产物后缀) —— 后缀规则与 `09_text2sql_eval.suffix_for` 一致
IMPLS = (("hand", "手写版 `t2sql_core.run()`", ""),
         ("graph", "LangGraph 版 `t2sql_graph.run()`", ".graph"))


def cell(text, n: int = 150) -> str:
    """放进表格的一格：压成一行、截断、转义竖线（SQL 里的 `||` 会把表格切碎）。"""
    s = " ".join(str(text).split())
    s = s if len(s) <= n else s[:n] + "…"
    return s.replace("|", "\\|")


def avg(total: int, n: int) -> str:
    return f"{total / n:.2f}" if n else "—"


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="手写版 vs LangGraph 版：真模型逐题对照（不调模型）")
    ap.add_argument("--tag", required=True, help="09 跑这两轮时用的 --tag")
    args = ap.parse_args()
    if not _RE_TAG.match(args.tag):
        print(f"[FAIL] --tag 只能用字母、数字、- 和 _：{args.tag!r}", file=sys.stderr)
        return E_USAGE

    runs: dict[str, dict] = {}
    for key, _, sfx in IMPLS:
        p = REPORTS / f"text2sql_results.v3{sfx}.{args.tag}.json"
        if not p.is_file():
            print(f"[未完成] 缺输入 reports/{p.name}：先跑 "
                  f"python experiments/09_text2sql_eval.py --versions v3 --impl {key} "
                  f"--tag {args.tag}", file=sys.stderr)
            return E_NO_INPUT
        runs[key] = json.loads(p.read_text(encoding="utf-8"))

    hand = {r["id"]: r for r in runs["hand"]["records"]}
    graph = {r["id"]: r for r in runs["graph"]["records"]}
    if list(hand) != list(graph):
        print("[FAIL] 两份记录的题号对不齐 —— 某一轮掉了题，那是调用层的问题，不是模型波动",
              file=sys.stderr)
        return E_NO_INPUT
    ids = list(hand)
    n = len(ids)

    # 判定口径与噪声带同一处定义：可答题看严格 EX，不可答题看有没有老实说答不了
    flips = [i for i in ids if eg.flip_verdict(hand[i]) != eg.flip_verdict(graph[i])]
    same_verdict_diff_sql = [i for i in ids if i not in flips and hand[i]["sql"] != graph[i]["sql"]]
    both_wrong = [i for i in ids if not eg.flip_verdict(hand[i]) and not eg.flip_verdict(graph[i])]

    band = None
    nb = REPORTS / "noise_band.json"
    if nb.is_file():
        band = json.loads(nb.read_text(encoding="utf-8"))

    # 模型名与端点不是密钥，写进口径；读不到就照实写「未记录」，不猜。
    env = env_file.read()
    model = os.environ.get("LLM_MODEL") or env.get("LLM_MODEL") or "（未记录）"
    base = os.environ.get("LLM_BASE_URL") or env.get("LLM_BASE_URL") or "（未记录）"

    lines: list[str] = []
    A = lines.append
    A(f"# 手写版 vs LangGraph 版：真模型逐题对照（标签 `{args.tag}`）")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/33_impl_compare.py`（不调模型，只读下面两份逐题记录）")
    for key, name, sfx in IMPLS:
        A(f"- **{name}**：`reports/text2sql_results.v3{sfx}.{args.tag}.json`，由 "
          f"`python experiments/09_text2sql_eval.py --versions v3 --impl {key} --tag {args.tag}` 产出")
    A(f"- **模型**：`{model}`；**端点**：`{base}`；两版同一天先后各跑一遍，提示词版本都是 v3")
    A(f"- **题集**：`data/eval/questions.json`，{n} 题 = 32 题可答 + 4 题本来就答不了")
    A("- **可答题完成数**：严格 EX 判对的可答题数 / 32（判分见 `mcp_server/eval_grading.py`）")
    A("- **不可答题拒答数**：4 道不可答题里模型老实说「答不了」的题数")
    A(f"- **调用次数 / token**：逐题 `usage` 累加，重写重试也算；「平均」= 总数 / {n} 题。"
      "不含 09 开跑前那 1 次连通性检查")
    A("- **翻转**：同一题两版的判定不同（可答题看严格 EX，不可答题看是否说了答不了；"
      "与 `21_noise_band.py` 同一口径）")
    A("- **两版控制流等价**是测试结论（`tests/test_t2sql_state_machine.py` 的两版对照用例），"
      "所以这里的任何差异都只能归到模型输出的不确定性上")
    A("- **表里的 SQL**：成功的题是护栏处理后实际执行的那条 —— 模型没写 `LIMIT` 的，"
      "末尾的 `LIMIT 200` 是护栏补的，不是模型写的")
    A("")
    A("## 汇总")
    A("")
    A("| 实现 | 可答题完成（严格 EX） | 不可答题拒答 | 调用次数（总 / 平均每题） "
      "| prompt tokens（总 / 平均） | completion tokens（总 / 平均） | 调用层错误 |")
    A("|---|---|---|---|---|---|---|")
    for key, name, _ in IMPLS:
        s = eg.summarize(runs[key]["records"])
        A(f"| {name} | {s['n_strict']}/{s['n_answerable']} "
          f"| {s['unsupported_correct']}/{s['unsupported_total']} "
          f"| {s['calls']} / {avg(s['calls'], n)} "
          f"| {s['prompt_tokens']} / {avg(s['prompt_tokens'], n)} "
          f"| {s['completion_tokens']} / {avg(s['completion_tokens'], n)} "
          f"| {len(runs[key].get('errors') or [])} |")
    A("")
    A("## 两版逐题对照")
    A("")
    A(f"- **判定翻转**：{len(flips)} 道" + (f"（{', '.join(flips)}）" if flips else ""))
    A(f"- **判定相同、但模型写出的 SQL 文本不同**：{len(same_verdict_diff_sql)} 道"
      + (f"（{', '.join(same_verdict_diff_sql)}）" if same_verdict_diff_sql else ""))
    A(f"- **两版都没判对**：{len(both_wrong)} 道" + (f"（{', '.join(both_wrong)}）" if both_wrong else ""))
    A("")
    shown = flips + same_verdict_diff_sql + [i for i in both_wrong if i not in flips]
    if shown:
        A("| 题号 | 类别 | 手写版：判定 / SQL | LangGraph 版：判定 / SQL | 判分说明（手写版 ‖ LangGraph 版） |")
        A("|---|---|---|---|---|")
        for i in shown:
            h, g = hand[i], graph[i]
            A(f"| {i} | {h['tag']} "
              f"| {'对' if eg.flip_verdict(h) else '错'} / `{cell(h['sql'] or h['status'])}` "
              f"| {'对' if eg.flip_verdict(g) else '错'} / `{cell(g['sql'] or g['status'])}` "
              f"| {cell(h['why'] or '—', 80)} ‖ {cell(g['why'] or '—', 80)} |")
        A("")
    A("## 和噪声带比")
    A("")
    if band is None:
        A("仓库里没有 `reports/noise_band.json`，这一节没有参照可比。")
    else:
        A(f"`reports/noise_band.json`（`experiments/21_noise_band.py`）：同一臂（{band.get('version')}）"
          f"在 temperature={band.get('temperature')} 下连跑两遍，翻转 **{band.get('noise_band')} 题**。"
          "那一轮是早先测的，模型与本次不一定相同，只作参照。")
        A("")
        k = len(flips)
        within = k <= int(band.get("noise_band") or 0)
        A(f"本次两版之间翻转 **{k} 题**，"
          + ("落在噪声带之内。" if within else "超出了噪声带，需要逐题查原因，不能直接归给模型波动。"))
    A("")
    A("结论的写法：两版完成率之差若落在噪声带之内，只能写「方向为 X，未达可判定水平」；"
      "两版控制流等价，所以这份对照**不能**被读成「哪一版更强」，它验证的是迁移没有改变行为。")
    A("")

    out = REPORTS / f"impl_compare.{args.tag}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    banned = eg.check_wording("\n".join(lines))
    print(f"判定翻转 {len(flips)} 道；SQL 文本不同 {len(same_verdict_diff_sql)} 道；"
          f"两版都没判对 {len(both_wrong)} 道")
    if banned:
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}：reports/{out.name}", file=sys.stderr)
        return E_BAD_WORDING
    print(f"[OK] 对照报告见 reports/{out.name}")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
