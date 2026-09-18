"""交付物核验：**一个不认识这个仓库的人**拿到它，能不能照着跑通。

## 它和另外两套检查的分工

| 谁 | 管什么 |
|---|---|
| `pytest` | 某个函数对不对 |
| `experiments/19_verify.py` | 仓库作为一个整体、红线与退出码对不对 |
| **本脚本** | **文档里写的和仓库里的对不对得上** |

## 为什么这一层非有不可

前两套都只看**代码**。可是别人拿到仓库先看的是**文档**，而文档是最容易悄悄过期的东西：
脚本改了名、flag 改了写法、报告挪了位置，代码那边全绿，文档那边每一个字都还在
指着老地方。这类错的坏处不在于难修，而在于**它把责任推给了照着做的人** ——
对方按着文档敲，报错，然后开始怀疑是不是自己环境有问题。

## 判定在 `mcp_server/deliverable_kit.py`

本文件以数字开头、**没法被 import**，所以 G1~G8 那八条检查全在 `deliverable_kit` 里，
那里能被 `tests/test_deliverable_kit.py` 拿临时目录当家、逐条证明**它会红**。
本文件只负责有副作用的那一半：解析参数、拼表、落盘、把判定换成退出码。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部通过 |
| 1 | 有 FAIL（文档与仓库对不上），或报告里出现禁用词 |
| 2 | 有 ERROR（检查自己出问题） |
| 4 | 用法错误 |
| 5 | `README.md` 尚未创建（阶段 6 的产物）—— 依赖它的两项判 `SKIP`，不是 `PASS` |

最后一条是刻意的：`SKIP` 不等于通过。README 还没写的时候，
「README 里的链接都对」这句话**没有任何实测依据**。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, deliverable_kit as dk  # noqa: E402
from mcp_server import paths  # noqa: E402
from mcp_server import verify_kit as vk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_FAIL = 1
E_ERROR = 2
E_USAGE = 4
E_NO_README = 5

PASS, FAIL, SKIP, ERROR = vk.PASS, vk.FAIL, vk.SKIP, vk.ERROR


def render(results: list[vk.Result], n_cmds: int) -> str:
    n = {s: sum(1 for r in results if r.status == s)
         for s in (PASS, FAIL, SKIP, ERROR)}
    L: list[str] = []
    A = L.append
    A("# 交付物核验")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/25_deliverable_check.py`（不联网、不调用模型）；"
      "判定逻辑在 `mcp_server/deliverable_kit.py`")
    A("- **它回答的问题**：一个不认识这个仓库的人，照着文档能不能跑通。"
      "所以查的全是「文档说的」与「仓库里的」对不对得上，而不是代码内部对不对")
    A("- **G1**：`data/` 下的数据文件存在；清单 JSON 可解析；清单与实际文件**双向**一致")
    A("- **G2**：逐篇重算 sha256 与清单比；Chinook 另与 `NOTICE.md` 里手写的那一行比")
    A("- **G3**：清单里每一篇的 `source_url` 都要在 `NOTICE.md` 里找得到")
    A("- **G4**：扫描全部 `.md` 里的 `python <脚本名>.py …`，"
      "脚本要存在、`--flag` 要在**源码文本**里出现"
      "（不 import 脚本：`experiments/` 下文件名以数字开头，import 不进来）；"
      "带 `<占位符>` 的是模板，不算一条指令")
    A("- **G5/G6**：README 的工具表与 `auth.TOOL_SCOPES` 双向比对；"
      "README 里的仓库内路径查存在性。**README 未创建时判 `SKIP` 而不是 `PASS`**")
    A("- **G7**：全仓文档里出现的 `reports/*` 逐条查存在性；不存在的，"
      "若**同一个自然段**里写了「未生成 / 尚未 / 未完成 / 阶段 6 / 不存在 / "
      "被创建 / 生成后 / 将由」之一则算已交代")
    A("- **G8**：`mcp_server/` 的第三方 import 必须都在 `requirements.txt` 里声明；"
      "反方向（声明了没 import）只记录不判失败")
    A("- **本报告不参与本报告的扫描**：报告里的「命中的行」是**引用**，"
      "把引用当声明来判会自己触发自己、每轮还多套一层反引号（实测长过三代）。"
      "所以生成物不参与生成它的那条检查 —— 红线没有缺口，"
      "`19_verify.py` 的 S1~S9 是全仓范围的，包含这一份")
    A(f"- **G4 实测命令条数**：{n_cmds}")
    A("- **本表数字的性质**：全部是实测的（文件计数、行号、哈希）。"
      "没有估计值，没有「大概齐」")
    A("")
    A(f"**合计**：{len(results)} 条 —— PASS {n[PASS]} / FAIL {n[FAIL]} / "
      f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    A("")
    A("## 明细")
    A("")
    A("| # | 检查项 | 判定 | 说明 |")
    A("|---|---|---|---|")
    for r in results:
        A(r.line())
    A("")
    A("## 命中的行（若有）")
    A("")
    any_hits = False
    for r in results:
        if not r.hits:
            continue
        any_hits = True
        A(f"### {r.cid} {r.title} —— {len(r.hits)} 处")
        A("")
        for h in r.hits[:40]:
            where = f"{h['file']}:{h['line']}" if h.get("line") else h["file"]
            A(f"- `{where}` {h['needle']}")
            if h.get("text"):
                # 引用的原文里若本来就有反引号，去掉再放进代码 span：
                # 否则每引用一代就多套一层，报告会自己长起来（实测长过三代）。
                A(f"  `{h['text'].replace('`', '')}`")
        A("")
    if not any_hits:
        A("无。")
        A("")
    A("## 这张表**不能**说明什么")
    A("")
    A("- 它不判断文档**写得好不好**、够不够清楚 —— 那件事没有机械判据，"
      "硬编一个只会得到一条永远绿的假检查。它只查「说了的东西在不在」。")
    A("- `SKIP` 不等于 `PASS`。README 还没写的时候，「README 里的链接都对」"
      "这句话没有任何实测依据。")
    A("- G4 查的是「`--flag` 这个字符串在源码里出现过」，"
      "不是「argparse 真的注册了这个参数」。两者在极端写法下会分叉"
      "（比如 flag 只出现在注释里），这是这条检查的已知边界。")
    A("- G7 判「有没有交代」用的是一串**固定说法**（见口径），不是语义理解。"
      "换一种说法描述同一个事实（比如「将来才会有」）会被判成没交代 —— "
      "这是有意的：判据必须机械，代价是措辞要迁就它。")
    A("- G2 校验的是**内容与声明一致**，不是「这份数据没被篡改过」——"
      "清单和文件是同一个人写的，改了两边一起改是查不出来的。"
      "它能挡住的是「只有一边动了」这种意外。")
    A("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="交付物核验（不联网、不调用模型）")
    ap.add_argument("--json", action="store_true",
                    help="同时落一份 deliverable_check.json")
    args = ap.parse_args()

    results: list[vk.Result] = []
    n_cmds = 0
    for fn in dk.CHECKS:
        try:
            r = fn(ROOT)
        except Exception as e:  # noqa: BLE001
            r = vk.Result(fn.__name__, fn.__name__, "检查自身抛异常", ERROR,
                          f"{type(e).__name__}: {e}")
        if r.cid == "G4":
            m = re.search(r"(\d+) 条命令", r.detail)
            n_cmds = int(m.group(1)) if m else 0
        results.append(r)

    REPORTS.mkdir(parents=True, exist_ok=True)
    from mcp_server import eval_runner as runner
    # ★ 这份报告自己就渲染 `文件:行号`，而行号本身可能是被禁数字（比如第 N 行，
    #   N 取词表里某个数）。实测过：`某报告.md:<N>` 会被 `scan_report_carrier()`
    #   扫出来。★ N 具体是哪些数**不在这里写出来** —— 上一稿写了，自己成了命中。
    #   所以落盘前走一遍单一实现的自毒防护（`19` 与 `26` 各自手写过一遍，
    #   这里原先一遍都没有 —— 而掩码比「红了再查」便宜得多）。
    text, self_carrier = vk.finalize_report_text(
        render(results, n_cmds), REPORTS / "deliverable_check.md")
    if self_carrier:
        print(f"  注：本次渲染有 {len(self_carrier)} 处自毒（命中行里的行号/片段），"
              f"已按登记豁免规则掩码")
    banned = runner.write_report(REPORTS / "deliverable_check.md", text)

    for r in results:
        mark = {PASS: " ", FAIL: "F", SKIP: "-", ERROR: "E"}[r.status]
        print(f"  [{mark}] {r.cid:<4} {r.title:<38} {r.detail[:74]}")
    n = {s: sum(1 for x in results if x.status == s) for s in (PASS, FAIL, SKIP, ERROR)}
    print(f"\n合计 {len(results)} 条：PASS {n[PASS]} / FAIL {n[FAIL]} / "
          f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    print(f"报告：{paths.rel(REPORTS / 'deliverable_check.md')}")

    if args.json:
        (REPORTS / "deliverable_check.json").write_text(json.dumps({
            "results": [r.as_dict() for r in results],
            "note": "口径见 reports/deliverable_check.md 的「口径」小节。"
                    "SKIP 不等于 PASS：README 未创建时依赖它的项没有任何实测依据。",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    if banned:
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_FAIL
    if n[ERROR]:
        return E_ERROR
    if n[FAIL]:
        return E_FAIL
    if (ROOT / "README.md").is_file():
        print("[OK] 交付物核验全部通过")
        return E_OK
    print("[未完成] README.md 尚未创建（阶段 6）：依赖它的两项判 SKIP，不是通过")
    return E_NO_README


if __name__ == "__main__":
    sys.exit(main())
