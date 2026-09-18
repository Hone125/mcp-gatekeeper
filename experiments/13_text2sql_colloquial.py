"""口语化问法：同一批题、同一批参考解，只把问句换成人话。

## 这个实验在问什么

正式题集里的问句是我照着表结构写的 —— 「一共有多少位艺术家？」这种话，
真实用户不会这么说。所以正式题集的分数里有一部分是**我的措辞**在帮忙，
而不是模型真的读懂了需求。

这个脚本把同一批问题换成口语（「这库里塞了多少个歌手啊？」），
**参考解一个字都没变**（程序化从正式题集抄过来的，见 `questions_colloquial.json`
的 meta），于是：

- 参考解相同 → 判分口径相同 → 两次的 EX 可以直接比；
- 只有问句不同 → 差异只能归因到措辞。

这是本仓库唯一一个**控制住了单一变量**的实验，所以它的样本量虽然小
（15 题），结论比 36 题那批更干净。

## 口径

与正式题集**完全一致**（`mcp_server/eval_grading.py` 文件头是唯一定义处）：
严格 EX / 投影容忍 EX 双口径、分母只数可答题、行序与列名不参与评分。

配对比较时"正式题"与"口语题"按 `same_as` 字段对齐，
统计用 `eval_grading.pair_compare()`（McNemar 精确检验 + 噪声带对照）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 跑完了 |
| 1 | 有题在调用层报错 |
| 2 | 题集核验不通过，或自检不通过 |
| 3 | 示例库没构建 |
| 4 | 用法错误 |
| 5 | **LLM 未配置** —— 写 `colloquial_NOT_RUN.md`，不写任何结果文件 |
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, eval_grading as eg, eval_runner as runner  # noqa: E402
from mcp_server import paths, t2sql_core  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
QUESTIONS = ROOT / "data" / "eval" / "questions_colloquial.json"

E_OK = 0
E_RUN_ERROR = 1
E_BAD_QUESTIONS = 2
E_NO_DB = 3
E_USAGE = 4
E_NO_LLM = 5


def check_questions(db: Path | None = None) -> int:
    """核验口语题集的参考解。**不需要 LLM。**

    额外核一条正式题集没有的：`same_as` 必须指得到一个真题号，
    且那条正式题的 `ref_sql` 与本条**逐字相同**。
    ★ 这一条是整个实验成立的前提 —— 参考解一旦不同，两次的 EX 就不能直接比了，
    而差异会伪装成「口语化的影响」。
    """
    data = runner.load_questions(QUESTIONS)
    db = db or paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        return E_NO_DB

    formal = {q["id"]: q for q in
              runner.load_questions()["questions"]}
    conn = runner.connect(db)
    problems: list[str] = []
    qs = data["questions"]
    print(f"核验 {len(qs)} 条口语题的参考解与 same_as 链接…")
    try:
        for q in qs:
            fid = q.get("same_as")
            if fid not in formal:
                problems.append(f"{q['id']} 的 same_as={fid!r} 在正式题集里找不到")
                print(f"  [FAIL] {q['id']} same_as 指不到")
                continue
            if q.get("ref_sql") != formal[fid].get("ref_sql"):
                problems.append(f"{q['id']} 的参考解与正式题 {fid} 不一致 —— "
                                f"整个实验的前提断了")
            if q["expect"] != formal[fid]["expect"]:
                problems.append(f"{q['id']} 的 expect 与正式题 {fid} 不一致")

            if q["expect"] != "rows":
                print(f"  [ok]   {q['id']} → {fid} 不可答（不跑参考解）")
                continue
            try:
                rows_a, cols_a = runner.run_ref(conn, q["ref_sql"])
                rows_b, _ = runner.run_ref(conn, q["ref_sql"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"{q['id']} 参考解执行失败：{type(e).__name__}: {e}")
                print(f"  [FAIL] {q['id']} 执行失败：{e}")
                continue
            if not rows_a:
                problems.append(f"{q['id']} 参考结果是零行 —— 这题没法判对错")
            if len(rows_a) > runner.MAX_REF_ROWS:
                problems.append(f"{q['id']} 参考结果 {len(rows_a)} 行，"
                                f"超过护栏的 {runner.MAX_REF_ROWS} 行上限")
            if rows_a != rows_b:
                problems.append(f"{q['id']} 连跑两次结果不一致 —— 这题在抽签")
            print(f"  [ok]   {q['id']} → {fid} {len(rows_a)} 行 {len(cols_a)} 列")
    finally:
        conn.close()

    ids = [q["id"] for q in qs]
    if len(set(ids)) != len(ids):
        problems.append("题号有重复")

    if problems:
        print(f"\n[FAIL] 口语题集核验不通过，{len(problems)} 处问题：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print(f"\n[OK] {len(qs)} 条口语题核验通过（参考解与正式题逐字相同、非空、两次一致）")
    return E_OK


# ---------------------------------------------------------------- 配对比较
def align(formal_recs: list[dict], coll_recs: list[dict]) -> tuple[list, list]:
    """把两批记录按 `same_as` 对齐成配对比较要的形状。

    题号统一成口语题的题号，所以两边能一一对上。正式题里缺的那几条
    （口语题集只覆盖了 15 道正式题）会被跳过，不做任何填补。
    """
    by_id = {r["id"]: r for r in formal_recs}
    a, b = [], []
    for r in coll_recs:
        fid = r.get("same_as")
        if fid not in by_id:
            continue
        fa = dict(by_id[fid])
        fa["id"] = r["id"]          # 统一题号，pair_compare 才能对齐
        a.append(fa)
        b.append(r)
    return a, b


def write_compare(formal_recs: list[dict], coll_recs: list[dict],
                  band: int | None, version: str,
                  out_dir: Path | None = None) -> str:
    """写口语 vs 正式的配对报告。返回 `(一行摘要, 报告里命中的禁用词)`。

    `out_dir` 默认 `reports/`。自检会把它指到别处 —— 报告模板要有办法在
    **不往 `reports/` 写假数据**的前提下被渲染出来检查，否则要么不检查它
    （模板里的错字没人发现），要么把假报告写进真报告目录。

    返回 `(一行摘要, 报告里命中的禁用词)`。
    """
    out = Path(out_dir) if out_dir is not None else REPORTS
    a, b = align(formal_recs, coll_recs)
    lines: list[str] = []
    A = lines.append
    A("# 口语化问法 vs 正式问法")
    A("")
    A("## 这个实验控制住了什么")
    A("")
    A("- **同一批参考解**：每条口语题的 `ref_sql` 是程序化从正式题集抄来的，逐字相同"
      "（由 `13 --check-questions` 机械核验）。所以两次的 EX 可以直接比。")
    A("- **同一批底层问题**：口语题按 `same_as` 一一对应到正式题。")
    A("- **唯一的变量是问句的措辞。**")
    A("")
    A("## 口径")
    A("")
    A(f"- **脚本**：`experiments/13_text2sql_colloquial.py`，提示词版本 `{version}`")
    A(f"- **题集**：`data/eval/questions_colloquial.json`（{len(coll_recs)} 题，"
      f"其中可答 {sum(1 for r in coll_recs if r.get('expect') != 'unsupported')} 题）")
    A("- **评分**：与正式题集完全一致，定义在 `mcp_server/eval_grading.py` 文件头")
    A("- **配对单位**：一道可答题，按 `same_as` 对齐")
    A("- **统计**：McNemar 精确双尾 + 噪声带对照，"
      "由 `eval_grading.pair_compare()` 生产措辞（词表封闭）")
    A("")
    A("## 汇总")
    A("")
    A("| 问法 | 严格 EX | 投影容忍 EX | 可答题数 | 「答不了」命中 | 调用次数 |")
    A("|---|---|---|---|---|---|")
    for name, recs in (("正式", formal_recs), ("口语", coll_recs)):
        s = eg.summarize(recs)
        A(f"| {name} | {s['n_strict']}/{s['n_answerable']} = {s['ex_strict']:.4f} "
          f"| {s['n_tolerant']}/{s['n_answerable']} = {s['ex_tolerant']:.4f} "
          f"| {s['n_answerable']} "
          f"| {s['unsupported_correct']}/{s['unsupported_total']} | {s['calls']} |")
    A("")
    A("> 两行的分母不同：正式题那一行是**全部 32 道可答题**，"
      "口语题那一行是**它覆盖到的那部分**。要比较请看下面的配对结果，"
      "不要直接比这两个比率。")
    A("")

    if band is None:
        A("## 配对比较：未完成")
        A("")
        A("**没有实测噪声带**（`reports/noise_band.json` 不存在），"
          "所以这里**不给**任何版本差异的结论。")
        A("")
        A("理由：噪声带是所有差异判据的分母。假定它为 0 会让任何一点抖动都被写成"
          "「口语化带来的差异」，而读的人无从分辨。先跑 "
          "`experiments/21_noise_band.py`。")
        A("")
        banned = runner.write_report(out / "colloquial_vs_formal.md", "\n".join(lines))
        return "配对比较未完成：缺实测噪声带", banned

    r = eg.pair_compare(a, b, band, "正式问法", "口语问法")
    A("## 配对比较")
    A("")
    A("| 不一致对数 | 只有正式对 | 只有口语对 | 净差 | 精确 p | 噪声带 | 是否超出 |")
    A("|---|---|---|---|---|---|---|")
    A(f"| {r['n_only_a'] + r['n_only_b']} | {r['n_only_a']} | {r['n_only_b']} "
      f"| {r['delta']:+d} | {r['p']:.4f} | {r['noise_band']} "
      f"| {'是' if r['beyond_noise'] else '否'} |")
    A("")
    A(f"- {r['sentence']}")
    A("")
    A("## 逐题")
    A("")
    A("| 题号 | 对应正式题 | 问法 | 正式 | 口语 | 状态 |")
    A("|---|---|---|---|---|---|")
    by_id = {x["id"]: x for x in a}
    for rec in b:
        fa = by_id.get(rec["id"], {})
        A(f"| {rec['id']} | {rec.get('same_as', '')} | {rec['question']} "
          f"| {'对' if fa.get('strict') else '错'} "
          f"| {'对' if rec.get('strict') else '错'} | {rec.get('status', '')} |")
    A("")
    A("## 这个实验**不能**说明什么")
    A("")
    A(f"- 只有 {r['n_paired']} 道题。不一致对数很小的时候，"
      "McNemar 精确双尾 p 必然落在 0.5～1.0，**p 值大不等于两种问法等效**，"
      "只说明这批题量分辨不出来。")
    A("- 口语题集覆盖的不是全部 32 道正式题，是按「这句话口语该怎么说」挑的，"
      "所以它不是一个随机样本。")
    A("- 单个提示词版本的结果，不能外推到别的版本。")
    A("")
    banned = runner.write_report(out / "colloquial_vs_formal.md", "\n".join(lines))
    (out / "colloquial_vs_formal.json").write_text(json.dumps({
        "version": version, "pair": r,
        "note": "口径见 reports/colloquial_vs_formal.md；措辞由 "
                "eval_grading.pair_compare() 生产。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return r["sentence"], banned


def load_band() -> int | None:
    p = REPORTS / "noise_band.json"
    if not p.is_file():
        return None
    try:
        return int(json.loads(p.read_text(encoding="utf-8"))["noise_band"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- 自检
def run_selftest(mode: str, db: Path, version: str) -> int:
    """用假模型跑一遍口语题集，断言整条路是通的。**不需要 LLM。**"""
    qs = runner.load_questions(QUESTIONS)["questions"]
    by_question = {q["question"]: q for q in qs}
    calls: dict[str, int] = {}
    llm = runner.make_fake_llm(mode, by_question, calls)

    out_json = REPORTS / f"colloquial_selftest.{mode}.json"
    conn = runner.connect(db)
    try:
        print(f"=== 口语题集假模型自检：{mode} ===")
        recs, errors = runner.run_version(version, qs, conn, db, out_json,
                                          llm=llm, record_cost=False,
                                          label=f"selftest-{mode}")
    finally:
        conn.close()

    s = eg.summarize(recs)
    print(f"  严格 {s['n_strict']}/{s['n_answerable']}、"
          f"答不了命中 {s['unsupported_correct']}/{s['unsupported_total']}")

    problems: list[str] = []
    if errors:
        problems.append(f"调用层报错：{errors[:2]}")
    if mode == "oracle" and s["ex_strict"] != 1.0:
        problems.append(f"满分模型没拿满分（{s['ex_strict']:.4f}）——"
                        f"要么参考解抄错了，要么 same_as 链接错了")
    if mode == "naive" and s["ex_strict"] > 0.2:
        problems.append(f"故意答错却拿到 {s['ex_strict']:.4f}")
    if mode == "flaky":
        retried = [r for r in recs if r["expect"] == "rows" and r["attempts"] == 2]
        if len(retried) != s["n_answerable"]:
            problems.append(f"可答题只有 {len(retried)}/{s['n_answerable']} 走了重写")

    # ★ 自检顺带证明了 same_as 是通的：如果链接断了，配对比较会静默少配几题。
    aligned_a, aligned_b = align(
        [{"id": q["same_as"], "expect": q["expect"], "strict": True} for q in qs
         if q.get("same_as") and q["expect"] == "rows"], recs)
    n_expected = sum(1 for q in qs if q.get("same_as") and q["expect"] == "rows")
    if len(aligned_a) != n_expected:
        problems.append(f"配对对齐后只剩 {len(aligned_a)}/{n_expected} 题 —— "
                        f"same_as 有断链")

    # ★ 把对比报告模板**两个分支都渲染一遍**，落到自检目录里。
    #   不这么做的话，模板只有在真跑完之后才会第一次被执行 ——
    #   那时它要是有个 KeyError，丢的是一整轮已经花钱跑出来的结果。
    #   而往 reports/ 里写假报告又是绝对不能做的事，所以写在这里。
    tmpl_dir = REPORTS / "selftest_templates"
    tmpl_dir.mkdir(parents=True, exist_ok=True)

    # 造一份"正式题"记录：把假模型的成绩安到 same_as 指的那些真题号上。
    fake_formal = [dict(r, id=r["same_as"]) for r in recs if r.get("same_as")]
    for band, tag in ((None, "no_band"), (3, "with_band")):
        try:
            summary, tmpl_banned = write_compare(fake_formal, recs, band, version,
                                                 out_dir=tmpl_dir)
        except Exception as e:  # noqa: BLE001
            problems.append(f"对比报告模板渲染失败（band={band}）："
                            f"{type(e).__name__}: {e}")
            continue
        md = (tmpl_dir / "colloquial_vs_formal.md").read_text(encoding="utf-8")
        for need in ("# 口语化问法 vs 正式问法", "## 汇总", "## 这个实验**不能**说明什么"
                     if band is not None else "## 配对比较：未完成"):
            if need not in md:
                problems.append(f"模板（band={band}）里缺章节：{need}")
        if band is not None:
            for need in ("## 配对比较", "## 逐题"):
                if need not in md:
                    problems.append(f"模板（band={band}）里缺章节：{need}")
            if eg.check_wording(md):
                problems.append(f"模板（band={band}）里出现了禁用词："
                                f"{eg.check_wording(md)}")
        if tmpl_banned:
            problems.append(f"模板（band={band}）里出现了禁用词 {tmpl_banned}")
        print(f"  模板渲染 {tag}：{len(md)} 字符，摘要「{summary[:40]}…」")

    if problems:
        print(f"\n[FAIL] 自检不通过，{len(problems)} 处：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print(f"[OK] 自检通过：{mode}；配对对齐 {len(aligned_a)} 题无断链；"
          f"报告模板两个分支都渲染成功")
    return E_OK


def write_not_run(reason: str) -> None:
    """没跑成时写的东西。**故意不叫 `colloquial_vs_formal.md`。**

    ## 为什么「未完成」的报告也要有口径小节

    这页里出现了「15 题」这样的数字。数字出现在报告里就得说清它是什么 ——
    「未完成」不等于「可以不写口径」。
    """
    n = len(json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"])
    (REPORTS / "colloquial_NOT_RUN.md").write_text(
        "# 口语化问法实验：未完成\n\n"
        f"**卡在哪一步**：{reason}\n\n"
        "## 口径\n\n"
        f"- **脚本**：`experiments/13_text2sql_colloquial.py`，退出码 {E_NO_LLM}"
        "（未完成，非失败）\n"
        f"- **题量**：`data/eval/questions_colloquial.json` 共 **{n} 题**，"
        "每条用 `same_as` 指回正式题集\n"
        "- **下方所有数字的性质**：**没有一个是跑出来的结果**。本页不含 EX / token / "
        "成本 / 配对 p 值\n\n"
        "## 已经就绪的部分\n\n"
        f"- 口语题集 `data/eval/questions_colloquial.json`（{n} 题）已就绪，"
        "参考解与正式题**逐字相同**，可用 `python experiments/13_text2sql_colloquial.py "
        "--check-questions` 机械核验，**不需要 LLM**。\n"
        "- 配对对齐 `align()` 与统计由 `--fake` 自检证明过（含 same_as 断链检查）。\n"
        # ★ 提成品报告的名字时，**必须在同一段里交代它还没生成**。
        #
        # 这一页是脚本每次退出码 5 都重新生成的：光去手改 markdown 没用，下一轮就被
        # 覆盖回去（实测踩过 —— 25 的 G7 因此报「1 处指向不存在的文件且没交代」）。
        # 而且模板渲染出来的那几份**故意落在子目录**里：`reports/` 根下是实测结果的
        # 位置，往那儿放一份假数字，会被读成真结果。
        "- 对比报告的排版由 `--fake` 自检证明过：两个分支各渲染一份，"
        "落在 `reports/selftest_templates/` 下。\n"
        "- 真跑之后才会有 `reports/colloquial_vs_formal.md`，本仓库**尚未生成**"
        "（它需要模型凭据，见 `BLOCKERS.md`）。\n\n"
        "## 缺的是什么\n\n"
        "只有一份能调用的模型凭据。**本仓库没有编造任何口语 EX 数字**。"
        "见 `BLOCKERS.md`。\n",
        encoding="utf-8")


def render_from_disk(version: str) -> int:
    """按**已有的**逐题 JSON 重出配对报告，一次模型调用都不发。

    ## 为什么需要它

    实测踩过：这一节的「配对比较」依赖 `reports/noise_band.json`，
    而噪声带是 `21_noise_band.py` 量的。**先跑 13、后跑 21**，
    报告里那一节就写成「配对比较未完成：缺实测噪声带」（见 `BLOCKERS.md`）。

    没有这个开关的话，补那一节只能把 13 整个重跑一遍。代价不只是 15 次调用：
    重跑后报告的 EX 会和刚才那次不一样（同一温度下仍会波动），
    于是「报告里的数字」和「账本里那次调用」就对不上了 ——
    为了补一节文字而让两份产物错位，不划算。

    逐题记录本来就在盘上（`colloquial_results.<version>.json`），
    配对又是纯函数（`write_compare` 不碰数据库、不碰模型），所以直接重渲染。
    """
    coll_path = REPORTS / f"colloquial_results.{version}.json"
    formal_path = REPORTS / f"text2sql_results.{version}.json"
    missing = [p.name for p in (coll_path, formal_path) if not p.is_file()]
    if missing:
        print(f"[FAIL] 没有这些逐题记录，不能凭空渲染报告：{missing}", file=sys.stderr)
        print(f"       先真跑一次：python experiments/13_text2sql_colloquial.py "
              f"--version {version}", file=sys.stderr)
        return E_NO_DB

    coll_recs = json.loads(coll_path.read_text(encoding="utf-8"))["records"]
    formal_recs = json.loads(formal_path.read_text(encoding="utf-8"))["records"]

    summary, banned = write_compare(formal_recs, coll_recs, load_band(), version)
    print(f"  配对比较：{summary}")
    if banned:
        print(f"\n[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print("\n[OK] 已按现有逐题记录重出配对报告（未调用模型）")
    return E_OK


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="口语化问法实验（15 题）")
    ap.add_argument("--check-questions", action="store_true",
                    help="只核验参考解与 same_as 链接，不需要 LLM")
    ap.add_argument("--fake", choices=runner.FAKE_MODES, default="",
                    help="用假模型自检整条流水线，不需要 LLM")
    ap.add_argument("--render-only", action="store_true",
                    help="只按已有的逐题 JSON 重出配对报告，一次模型调用都不发")
    ap.add_argument("--version", default=t2sql_core.DEFAULT_VERSION,
                    help=f"提示词版本（默认 {t2sql_core.DEFAULT_VERSION}）")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)

    if args.check_questions:
        return check_questions()

    if args.render_only:
        return render_from_disk(args.version)

    db = paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        return E_NO_DB

    try:
        t2sql_core.prompt_for(args.version)
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return E_USAGE

    if args.fake:
        return run_selftest(args.fake, db, args.version)

    try:
        t2sql_core.real_llm([{"role": "user", "content": "ping"}], "preflight")
    except RuntimeError as e:
        msg = f"缺少模型配置：{e}"
        print(f"[未完成] {msg}", file=sys.stderr)
        write_not_run(msg)
        return E_NO_LLM
    except Exception as e:  # noqa: BLE001
        msg = f"模型调用失败（凭据已配置，但请求没成功）：{type(e).__name__}: {e}"
        print(f"[未完成] {msg}", file=sys.stderr)
        write_not_run(msg)
        return E_NO_LLM

    qs = runner.load_questions(QUESTIONS)["questions"]
    if args.limit:
        qs = qs[:args.limit]

    (REPORTS / "colloquial_NOT_RUN.md").unlink(missing_ok=True)
    conn = runner.connect(db)
    try:
        recs, errors = runner.run_version(
            args.version, qs, conn, db,
            REPORTS / f"colloquial_results.{args.version}.json", label="colloquial")
    finally:
        conn.close()

    s = eg.summarize(recs)
    print(f"\n  口语题集：严格 {s['n_strict']}/{s['n_answerable']}、"
          f"容忍 {s['n_tolerant']}/{s['n_answerable']}、调用 {s['calls']} 次")

    # 配对比较需要正式题集那一版的结果。
    formal_path = REPORTS / f"text2sql_results.{args.version}.json"
    banned: list[str] = []
    if formal_path.is_file():
        formal_recs = json.loads(formal_path.read_text(encoding="utf-8"))["records"]
        summary, banned = write_compare(formal_recs, recs, load_band(), args.version)
        print(f"  配对比较：{summary}")
    else:
        print(f"  [跳过配对比较] 找不到 {formal_path} —— "
              f"先跑 09 号的 {args.version} 那一版")

    if errors:
        print(f"\n[FAIL] 有 {len(errors)} 题在调用层失败（不是判错）：", file=sys.stderr)
        for e in errors[:5]:
            print(f"  - {e}", file=sys.stderr)
        return E_RUN_ERROR
    if banned:
        print(f"\n[FAIL] 报告里出现禁用词 {sorted(set(banned))}："
              f"{paths.rel(REPORTS / 'colloquial_vs_formal.md')}\n"
              f"       报告已落盘（数字是真的），但措辞越界，这一轮不算通过。",
              file=sys.stderr)
        return E_BAD_QUESTIONS
    print("\n[OK] 跑完，报告见 reports/colloquial_vs_formal.md")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
