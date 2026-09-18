"""两个提示词版本的配对检验：McNemar 精确检验 + 噪声带对照。

## 它是**分析**脚本，不是**跑**脚本

数据来自 09 号脚本落盘的两份逐题记录（`reports/text2sql_results.vX.json`），
本脚本只做统计。所以它**不需要 LLM** —— 09 跑过了它就能跑。

## 三条纪律（这个脚本存在的全部理由）

### 1. 读不到噪声带就**拒绝下结论**

`reports/noise_band.json` 缺失时，这里**不会**假定噪声带为 0，而是直接退出。
假定为 0 的后果很具体：任何一点抖动都会被写成「版本差异」，
而报告读的人无从分辨。宁可报「未完成」，不可报一个没法核对的差异。

### 2. 只在噪声带之外才说「超出」

`eval_grading.beyond_noise()` 取严格大于：净差**等于**噪声带时，
观测到的差异完全可以由噪声解释，那时说「有差异」就是过度解读。

### 3. 措辞由 `eval_grading.describe_difference()` 独家生产

它是唯一允许描述版本差异的函数，词表是封闭的 ——「显著 / 大幅 / 提升 / 极大」
不在里面。这不是谦虚，是算术：36 题里不一致对数通常个位数，
McNemar 精确双尾 p 必然落在 0.5～1.0，**不可能**小于 0.05。
`tests/test_eval_grading.py` 里有一条测试专门钉住这件事。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 算完了，`reports/pair_test.md` 已写出 |
| 2 | 自检不通过（`--selftest`） |
| 3 | 缺输入：某版的逐题记录不存在，或噪声带没实测过 |
| 4 | 用法错误（版本名不对、两版是同一版） |

## 自检（`--selftest`，不读文件、不联网）

统计是纯数学，所以能脱离模型和数据库直接测。自检覆盖：完全一致、超出噪声带、
落在噪声带内、题目对不齐要炸、以及**每一句生成的措辞里都没有禁用词**。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, eval_grading as eg  # noqa: E402
from mcp_server import eval_runner as runner  # noqa: E402
from mcp_server import paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
NOISE_BAND = REPORTS / "noise_band.json"

E_OK = 0
E_SELFTEST = 2
E_MISSING_INPUT = 3
E_USAGE = 4


def set_reports_dir(path: Path) -> None:
    """把输入输出目录换到别处。

    存在的理由：报告生成这条路要有办法在**不碰 `reports/`** 的前提下被走通。
    否则要么不测它（那报告模板里的错字没人发现），要么拿假数据往真目录里写
    （那真报告就可能被假数字污染）。临时目录是唯一的第三条路。
    """
    global REPORTS, NOISE_BAND
    REPORTS = Path(path)
    NOISE_BAND = REPORTS / "noise_band.json"


# ---------------------------------------------------------------- 纯统计
# 配对逻辑放在 `mcp_server/eval_grading.py` 里：本文件以数字开头，**没法被 import**，
# 写在里面就只能靠跑脚本来测它。定义见那个模块的 `pair_compare()`。
pair_compare = eg.pair_compare


# ---------------------------------------------------------------- 自检
def run_selftest() -> int:
    """用合成数据把统计与措辞逐条钉住。**不读文件、不联网、不需要 LLM。**"""
    problems: list[str] = []

    def rec(qid, strict, expect="rows", status="ok"):
        return {"id": qid, "expect": expect, "strict": strict, "status": status}

    def arms(n_a, n_b, n_both=0):
        """造两臂：n_a 题只有 A 对、n_b 题只有 B 对，其余同对同错。"""
        a, b = [], []
        for i in range(n_a):
            a.append(rec(f"x{i}", True)); b.append(rec(f"x{i}", False))
        for i in range(n_a, n_a + n_b):
            a.append(rec(f"x{i}", False)); b.append(rec(f"x{i}", True))
        for i in range(n_a + n_b, n_a + n_b + n_both):
            a.append(rec(f"x{i}", True)); b.append(rec(f"x{i}", True))
        return a, b

    # 1) 两臂完全一致 → p = 1.0，不该出现「超出噪声带」
    a, b = arms(0, 0, 10)
    r = pair_compare(a, b, band=0, name_a="v1", name_b="v2")
    if r["p"] != 1.0 or r["delta"] != 0:
        problems.append(f"完全一致时 p={r['p']} delta={r['delta']}，应当是 1.0 / 0")
    if r["beyond_noise"]:
        problems.append("完全一致却报「超出噪声带」")
    if "未观测到差异" not in r["sentence"] and "未超出" not in r["sentence"]:
        problems.append(f"完全一致的措辞不对：{r['sentence']}")

    # 2) 净差远大于噪声带 → 报「超出」
    a, b = arms(8, 1)
    r = pair_compare(a, b, band=2, name_a="v1", name_b="v2")
    if not r["beyond_noise"]:
        problems.append(f"净差 7、噪声带 2，应当报超出，实际 {r['beyond_noise']} "
                        f"(p={r['p']})")
    if "超出噪声带" not in r["sentence"]:
        problems.append(f"该说「超出」却没说：{r['sentence']}")

    # 3) 净差落在噪声带**之内** → 只能说「方向，未达可判定水平」
    a, b = arms(3, 2)
    r = pair_compare(a, b, band=2, name_a="v1", name_b="v2")
    if r["beyond_noise"]:
        problems.append("净差 1、噪声带 2，不该报超出")
    if "未超出噪声带" not in r["sentence"] or "未达可判定水平" not in r["sentence"]:
        problems.append(f"落在噪声带内的措辞不对：{r['sentence']}")

    # 4) 净差**等于**噪声带 → 也不算超出（严格大于）
    a, b = arms(5, 3)
    r = pair_compare(a, b, band=2, name_a="v1", name_b="v2")
    if r["beyond_noise"]:
        problems.append("净差正好等于噪声带时不该报超出")

    # 5) 题目对不齐必须炸 —— 掉题不是答错
    try:
        pair_compare([rec("q1", True)], [rec("q2", True)], 0, "v1", "v2")
        problems.append("两臂题号不同却没有报错 —— 掉题会被当成答错")
    except ValueError:
        pass

    # 6) ★ 每一句生成的措辞都不许含禁用词。这是措辞纪律的机械检查。
    for na, nb, band in ((0, 0, 0), (8, 1, 2), (3, 2, 2), (1, 0, 0), (9, 1, 0)):
        a, b = arms(na, nb)
        r = pair_compare(a, b, band=band, name_a="v1", name_b="v3")
        if r["banned_words"]:
            problems.append(f"措辞里出现了禁用词 {r['banned_words']}：{r['sentence']}")

    # 7) 「答不了」的题翻转要单独报出来，且不进 EX 分母
    a = [rec("q1", True), rec("u1", False, "unsupported", "unsupported")]
    b = [rec("q1", True), rec("u1", False, "unsupported", "ok")]
    r = pair_compare(a, b, band=0, name_a="v1", name_b="v2")
    if r["n_paired"] != 1:
        problems.append(f"不可答题混进了 EX 分母：n_paired={r['n_paired']}")
    if r["unsupported_flips"] != ["u1"]:
        problems.append(f"「答不了」的翻转没报出来：{r['unsupported_flips']}")

    if problems:
        print(f"[FAIL] 自检不通过，{len(problems)} 处：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_SELFTEST
    print("[OK] 自检通过：7 组统计与措辞都符合预期")
    return E_OK


# ---------------------------------------------------------------- 主流程
def load_records(version: str) -> list[dict]:
    p = REPORTS / f"text2sql_results.{version}.json"
    if not p.is_file():
        raise FileNotFoundError(
            f"找不到 {version} 的逐题记录：{p}\n先跑：python experiments/09_text2sql_eval.py "
            f"--versions {version}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["records"]


def load_band() -> int:
    """读实测噪声带。**读不到就抛异常，绝不返回 0。**"""
    if not NOISE_BAND.is_file():
        raise FileNotFoundError(
            f"没有实测噪声带：{NOISE_BAND}\n"
            f"先跑：python experiments/21_noise_band.py\n"
            f"★ 这里**故意不假定噪声带为 0** —— 那样任何抖动都会被写成版本差异。")
    data = json.loads(NOISE_BAND.read_text(encoding="utf-8"))
    if "noise_band" not in data:
        raise ValueError(f"噪声带文件里没有 noise_band 字段：{NOISE_BAND}")
    return int(data["noise_band"])


def write_report(results: list[dict], band_meta: dict) -> list[str]:
    lines: list[str] = []
    A = lines.append
    A("# 版本配对检验")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/20_pair_test.py`（纯分析，不需要 LLM）")
    # ★ 这里原来写的是 `reports/text2sql_results.vX.json`（模板占位符），
    #   交付物核验把 `vX` 当成真文件名，判成「引用了不存在的报告」（实测 G7 红）。
    #   写出**实际读的那几个文件**更好：占位符消失了，读者也不用猜。
    _versions = sorted({r["name_a"] for r in results} | {r["name_b"] for r in results})
    _src = "、".join(f"`reports/text2sql_results.{v}.json`" for v in _versions)
    A(f"- **数据**：{_src}，均由 `experiments/09_text2sql_eval.py` 产出")
    A("- **配对单位**：一道可答题。两臂都对的、都错的**不进**不一致对数 —— "
      "它们提供不了「哪一版更好」的信息")
    A("- **只对题数（only_a / only_b）**：A 对而 B 错、B 对而 A 错的题数")
    A("- **净差**：only_a − only_b，正数表示 A 更好")
    A("- **检验**：McNemar 精确双尾，`p = min(1, 2·P(X ≤ min(b,c)))`，"
      "`X ~ Binomial(b+c, 0.5)`")
    A("- **噪声带**：实测值，来自 `reports/noise_band.json`"
      f"（同一臂连跑两遍、逐题判定翻转的题数，实测 = **{band_meta['noise_band']} 题**，"
      f"分母 {band_meta['n_questions']} 题）")
    A("- **判据**：净差的绝对值 **严格大于** 噪声带才算超出")
    A("")
    A("## 结果")
    A("")
    A("| A | B | 不一致对数 | A 独对 | B 独对 | 净差 | 精确 p | 是否超出噪声带 |")
    A("|---|---|---|---|---|---|---|---|")
    for r in results:
        A(f"| {r['name_a']} | {r['name_b']} | {r['n_only_a'] + r['n_only_b']} "
          f"| {r['n_only_a']} | {r['n_only_b']} | {r['delta']:+d} | {r['p']:.4f} "
          f"| {'是' if r['beyond_noise'] else '否'} |")
    A("")
    A("## 逐对说明")
    A("")
    # ★ 这里**故意不把那几个词列出来**。原先这句是把它们逐个写出来的，
    #   结果报告为了声明「我不用这几个词」而写下了这几个词 ——
    #   报告自己撞上了自己的措辞检查（实测：20 退出码 2，pytest 里
    #   test_no_overstated_wording_in_the_reports_people_read 报红）。
    #   改成指向唯一的定义处：既不自撞，词表以后变了这句话也不会变成过期文档。
    A("下面每一句都由 `mcp_server/eval_grading.py` 的 `describe_difference()` 生成，"
      f"它的词表是封闭的 —— 里面没有那几个描述「变大」的词"
      f"（见 `eval_grading.BANNED_WORDS`，共 {len(eg.BANNED_WORDS)} 个）。")
    A("")
    for r in results:
        A(f"- {r['sentence']}")
    A("")
    A("## 「答不了」的 4 题")
    A("")
    A("这 4 题不进 EX 分母（模型说「答不了」是一个有效结论），"
      "但它们也会在两次运行之间翻转，照实报出来。")
    A("")
    A("| A | B | 翻转的题 |")
    A("|---|---|---|")
    for r in results:
        flips = "、".join(r["unsupported_flips"]) or "无"
        A(f"| {r['name_a']} | {r['name_b']} | {flips} |")
    A("")
    A("## 这个检验**不能**说明什么")
    A("")
    A(f"- 题目只有 {results[0]['n_paired'] if results else 0} 道，"
      "不一致对数通常是个位数，p 值必然落在 0.5～1.0 量级。"
      "**p 值大不等于「两版一样」**，只说明这批题量分辨不出差别。")
    A("- 噪声带是**一对**重复对照量出来的，不是置信区间，"
      "只能当「低于这个数就别当回事」的下限用。")
    A("- 净差没超出噪声带时，正确写法是「方向为 X，未达可判定水平」，"
      "不是「持平」，也不是「无差异」。")
    A("")
    return runner.write_report(REPORTS / "pair_test.md", "\n".join(lines))


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="两版配对检验（读 09 的产物，不需要 LLM）")
    ap.add_argument("--selftest", action="store_true",
                    help="用合成数据自检统计与措辞，不读文件、不需要 LLM")
    ap.add_argument("--pairs", default="v1:v2,v1:v3,v2:v3",
                    help="要比较的版本对，逗号分隔（默认 v1:v2,v1:v3,v2:v3）")
    ap.add_argument("--reports-dir", default="",
                    help="换一个输入输出目录（测试报告生成用；默认 reports/）")
    args = ap.parse_args()

    if args.reports_dir:
        set_reports_dir(Path(args.reports_dir).resolve())

    if args.selftest:
        return run_selftest()

    try:
        band = load_band()
        band_meta = json.loads(NOISE_BAND.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as e:
        print(f"[未完成] {e}", file=sys.stderr)
        return E_MISSING_INPUT

    pairs: list[tuple[str, str]] = []
    for item in args.pairs.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            print(f"[FAIL] 版本对要写成 A:B，收到 {item!r}", file=sys.stderr)
            return E_USAGE
        a, b = (s.strip() for s in item.split(":", 1))
        if a == b:
            print(f"[FAIL] 两版是同一版：{a}", file=sys.stderr)
            return E_USAGE
        pairs.append((a, b))

    results: list[dict] = []
    cache: dict[str, list[dict]] = {}
    for a, b in pairs:
        try:
            for v in (a, b):
                if v not in cache:
                    cache[v] = load_records(v)
        except FileNotFoundError as e:
            print(f"[未完成] {e}", file=sys.stderr)
            return E_MISSING_INPUT
        try:
            r = pair_compare(cache[a], cache[b], band, a, b)
        except ValueError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return E_MISSING_INPUT
        results.append(r)
        print(f"  {a} vs {b}：{r['sentence']}")

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "pair_test.json").write_text(json.dumps({
        "noise_band": band,
        "noise_band_meta": band_meta,
        "results": results,
        "note": [
            "口径见 reports/pair_test.md 的「口径」小节与 mcp_server/eval_grading.py 文件头。",
            "措辞由 describe_difference() 独家生产，禁用词表是封闭的。",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    banned = write_report(results, band_meta)
    banned += [w for r in results for w in r["banned_words"]]
    if banned:
        # 走到这里说明 describe_difference 被改坏了 —— 报告已经写出去了，
        # 但要以非零码结束，免得这件事被当成正常跑完。
        print(f"[FAIL] 报告里出现了禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_SELFTEST
    print(f"\n[OK] 报告见 {paths.rel(REPORTS / 'pair_test.md')}（{len(results)} 对）")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
