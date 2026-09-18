"""Text2SQL 评测：36 题 × 3 个提示词版本，严格 EX / 投影容忍 EX 双口径。

## 口径（数字必须能对到这里）

- **题集**：`data/eval/questions.json`，36 题 —— 32 题可答 + 4 题**本来就不该答得出来**。
- **参考解**：每题一条**可执行的**参考 SQL，不是口头描述的标准答案。
- **评分**：`mcp_server/eval_grading.py`，两条口径的定义在那个文件头里，
  `tests/test_eval_grading.py` 逐条钉住。行序与列名不参与评分，浮点带 6 位容差。
- **分母**：只数可答题（32）。模型说「答不了」的那 4 题单独统计 —— 它是一个有效结论，
  混进分母等于在惩罚 v3 提示词里那段正确的引导。
- **调用次数与 token**：按**每次模型调用**累加，重试花的钱也算进去。

## 为什么参考解要单独核验（`--check-questions`）

参考 SQL 是我手写的。手写的东西会错，而且错得很安静：一条写错的参考解会让
模型「答对却判错」，报告上表现为提示词版本之间的差异，排查方向会指向提示词。
所以这个脚本带一个**不需要 LLM** 的模式，把 32 条参考解逐条实跑并核验：

1. 能跑通（语法、表名、列名都对）；
2. 结果非空（除非这题就是要求「零行」—— 本集没有这种题）；
3. 行数 ≤ 200（护栏会自动补 `LIMIT 200`，超出的题考的是我自己的行数上限，不是模型）；
4. **连跑两次结果一致**（否则这题在抽签，不是参考解）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 跑完了 |
| 1 | 跑的过程中有题抛了异常（模型层的问题，不是判错） |
| 2 | 题集核验不通过（`--check-questions`）、题集文件有问题，或报告里出现了禁用词 |
| 3 | 示例库没构建 |
| 4 | 用法错误 |
| 5 | **LLM 未配置** —— 这是唯一「什么都没跑」的码，见 BLOCKERS.md |

## 环境变量

需要 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（照 `.env.example` 填）。
不填的话**不会编任何数字**：写一份 `reports/text2sql_NOT_RUN.md` 说明卡在哪一步，
以退出码 5 结束，并且**不创建** `text2sql_results.*` —— 免得有人把空报告当成结果读。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, eval_grading as eg, eval_runner as runner, paths  # noqa: E402
from mcp_server import t2sql_core  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_RUN_ERROR = 1
E_BAD_QUESTIONS = 2
E_NO_DB = 3
E_USAGE = 4
E_NO_LLM = 5

# 跑参考解、判一题、跑一版这三件事在 `mcp_server/eval_runner.py` 里，
# `13_text2sql_colloquial.py` 与 `21_noise_band.py` 用的是同一份 —— 口径只能有一处定义。
MAX_REF_ROWS = runner.MAX_REF_ROWS
load_questions = runner.load_questions
grade_one = runner.grade_one
run_version = runner.run_version


# ---------------------------------------------------------------- 题集核验
def check_questions(db: Path | None = None) -> int:
    """把每条参考解放到真库里实跑一遍。**不需要 LLM。**"""
    data = load_questions()
    qs = [q for q in data["questions"] if q["expect"] == "rows"]
    db = db or paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        print("         先跑：python experiments/01_build_db.py", file=sys.stderr)
        return E_NO_DB

    conn = runner.connect(db)
    problems: list[str] = []
    print(f"核验 {len(qs)} 条参考解（连跑两次比一致性）…")
    try:
        for q in qs:
            try:
                rows_a, cols_a = runner.run_ref(conn, q["ref_sql"])
                rows_b, cols_b = runner.run_ref(conn, q["ref_sql"])
            except sqlite3.Error as e:
                problems.append(f"{q['id']} 执行失败：{type(e).__name__}: {e}")
                print(f"  [FAIL] {q['id']} 执行失败：{e}")
                continue
            if not rows_a:
                problems.append(f"{q['id']} 参考结果是零行 —— 这题没法判对错")
            if len(rows_a) > MAX_REF_ROWS:
                problems.append(f"{q['id']} 参考结果 {len(rows_a)} 行，"
                                f"超过护栏的 {MAX_REF_ROWS} 行上限")
            if rows_a != rows_b or cols_a != cols_b:
                problems.append(f"{q['id']} 连跑两次结果不一致 —— 这题在抽签")
            print(f"  [ok]   {q['id']} {len(rows_a)} 行 {len(cols_a)} 列  {q['tag']}")

        ids = [q["id"] for q in data["questions"]]
        if len(set(ids)) != len(ids):
            problems.append("题号有重复")
        for q in data["questions"]:
            if q["expect"] == "rows" and not q.get("ref_sql"):
                problems.append(f"{q['id']} 标了可答却没有参考 SQL")
            if q["expect"] == "unsupported" and q.get("ref_sql"):
                problems.append(f"{q['id']} 标了不可答却给了参考 SQL —— 两者只能有一个")
    finally:
        conn.close()

    if problems:
        print(f"\n[FAIL] 题集核验不通过，{len(problems)} 处问题：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print(f"\n[OK] {len(qs)} 条参考解全部核验通过"
          f"（非空、≤{MAX_REF_ROWS} 行、两次一致）")
    return E_OK


# ---------------------------------------------------------------- 假模型自检
# 这一节回答的是：「评测流水线本身可信吗？」
#
# `--check-questions` 只证明了参考解能跑；它没有证明 `grade_one` / `run_version` /
# `write_report` 这条路在真实数据上是对的。没有真模型的时候，唯一能验证这条路
# 的办法就是**换一个行为已知的假模型进去**：
#
#   oracle —— 每题都回参考 SQL（本该满分）
#   naive  —— 每题都回 `SELECT * FROM Artist`（本该接近零分）
#   flaky  —— 第一次回错列名、第二次回参考 SQL（本该满分，且 attempts 应当为 2）
#
# oracle 拿不到 100% 就说明评分或流水线有 bug；naive 拿到高分说明评分太松。
# **两个方向都要有**，否则「全判对」和「全判错」这两种坏实现都能蒙混过关。
_FAKE_MODES = runner.FAKE_MODES
make_fake_llm = runner.make_fake_llm


def run_selftest(mode: str, db: Path) -> int:
    """用假模型把整条流水线跑一遍，并把结论断言出来。**不需要 LLM。**"""
    data = load_questions()
    qs = data["questions"]
    by_question = {q["question"]: q for q in qs}
    calls: dict[str, int] = {}
    llm = make_fake_llm(mode, by_question, calls)

    REPORTS.mkdir(parents=True, exist_ok=True)
    out_json = REPORTS / f"text2sql_selftest.{mode}.json"
    conn = runner.connect(db)
    try:
        print(f"=== 假模型自检：{mode} ===")
        # ★ record_cost=False：假模型的 token 不许混进真账本，否则成本报告成了假的。
        recs, errors = run_version("v3", qs, conn, db, out_json,
                                   llm=llm, record_cost=False)
    finally:
        conn.close()

    s = eg.summarize(recs)
    out_json.write_text(json.dumps({
        "mode": mode, "summary": s, "records": recs, "errors": errors,
        "calls_per_question": calls,
        "note": [
            "这是**假模型自检**，不是评测结果。它证明的是评分流水线在真实数据上跑得通、"
            "且能区分对错，不产生任何关于真实模型能力的数字。",
            "假模型的用量没有写进成本账本（record_cost=False）。",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  严格 {s['n_strict']}/{s['n_answerable']}、"
          f"容忍 {s['n_tolerant']}/{s['n_answerable']}、"
          f"答不了命中 {s['unsupported_correct']}/{s['unsupported_total']}、"
          f"总调用 {s['calls']} 次")

    # ---- 断言。这里是自检真正干活的地方。
    problems: list[str] = []
    if errors:
        problems.append(f"有 {len(errors)} 题在调用层报错：{errors[:2]}")
    if mode == "oracle":
        if s["ex_strict"] != 1.0:
            problems.append(f"满分模型没拿到满分（严格 {s['ex_strict']:.4f}）——"
                            f"要么评分有 bug，要么参考解与评分对不上")
        if s["unsupported_correct"] != s["unsupported_total"]:
            problems.append("满分模型在「答不了」的题上没有全部命中")
        for r in recs:
            if r["calls"] != 1:
                problems.append(f"{r['id']} 本该一次就过，实际调用了 {r['calls']} 次")
                break
    elif mode == "naive":
        if s["ex_strict"] > 0.2:
            problems.append(f"故意答错的模型拿到了 {s['ex_strict']:.4f} —— 评分太松了")
        if s["unsupported_correct"] > 0:
            problems.append("naive 硬写了 SQL 却被算成「老实说答不了」")
    else:  # flaky
        if s["ex_strict"] != 1.0:
            problems.append(f"重写一次后本该全对（严格 {s['ex_strict']:.4f}）")
        retried = [r for r in recs if r["expect"] == "rows" and r["attempts"] == 2]
        if len(retried) != s["n_answerable"]:
            problems.append(f"可答题里只有 {len(retried)}/{s['n_answerable']} 走了两次 ——"
                            f"重写路径没有被完整走到")
        # ★ 只查可答题。那 4 道「本来就答不了」的题**第一次就该停**
        #   （说了 UNSUPPORTED 不该再试），调用 1 次才是对的。
        #   第一版把全部题都要求成 2 次，于是自检报了一条假问题。
        bad = [q["id"] for q in qs
               if q["expect"] == "rows" and calls.get(q["id"]) != 2]
        if bad:
            problems.append(f"这些可答题的模型调用次数不是 2：{bad[:5]}")

    if problems:
        print(f"\n[FAIL] 自检不通过，{len(problems)} 处：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print(f"[OK] 自检通过：{mode} 的表现与预期一致")
    return E_OK


def write_report(all_records: dict[str, list[dict]],
                 noise_band: int | None) -> list[str]:
    """把三版汇总成一份 markdown。**每个数字都带口径。**

    返回报告里命中的禁用词（正常为空）。措辞检查在**真实产物**上做，
    理由见 `mcp_server/eval_runner.py` 的 `write_report()`。
    """
    lines: list[str] = []
    A = lines.append
    A("# Text2SQL 评测结果")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/09_text2sql_eval.py`")
    A("- **题集**：`data/eval/questions.json`，36 题 = 32 题可答 + 4 题本来就不该答得出来")
    A("- **参考解**：每题一条可执行 SQL，由 `09 --check-questions` 实跑核验"
      "（非空、≤200 行、连跑两次一致）")
    A("- **严格 EX**：列数相同且行内容的多重集完全相同（行序无关、列名无关、浮点 6 位容差）")
    A("- **投影容忍 EX**：在严格的基础上，允许列顺序不同")
    A("- **分母**：只数 32 道可答题。模型答「答不了」的 4 题单独统计 —— "
      "它是一个有效结论，混进分母等于惩罚 v3 提示词里正确的引导")
    A("- **「严格 / 容忍」两列只对可答题有意义**：不可答题没有参考行集可比，"
      "判据是「有没有老实说答不了」（见下面「「答不了」的 4 题」那一节），"
      "所以那两列在不可答题上写 `—`，**不写「否」** —— "
      "写「否」会让一道答对了的题看起来像答错了")
    A("- **调用次数 / token**：按每次模型调用累加，重写重试花的也算")
    A("")
    A("## 汇总")
    A("")
    A("| 版本 | 严格 EX | 投影容忍 EX | 可答题数 | 状态分布 | 调用次数 | prompt tokens | completion tokens |")
    A("|---|---|---|---|---|---|---|---|")
    for v, recs in all_records.items():
        s = eg.summarize(recs)
        A(f"| {v} | {s['n_strict']}/{s['n_answerable']} = {s['ex_strict']:.4f} "
          f"| {s['n_tolerant']}/{s['n_answerable']} = {s['ex_tolerant']:.4f} "
          f"| {s['n_answerable']} | {s['by_status']} | {s['calls']} "
          f"| {s['prompt_tokens']} | {s['completion_tokens']} |")
    A("")
    A("## 「答不了」的 4 题")
    A("")
    A("| 版本 | 老实说答不了 | 硬写了一条 SQL |")
    A("|---|---|---|")
    for v, recs in all_records.items():
        s = eg.summarize(recs)
        ok = s["unsupported_correct"]
        A(f"| {v} | {ok}/{s['unsupported_total']} | {s['unsupported_total'] - ok} |")
    A("")
    A("## 逐题")
    A("")
    for v, recs in all_records.items():
        A(f"### {v}")
        A("")
        A("| 题号 | 类别 | 状态 | 严格 | 容忍 | 尝试 | 说明 |")
        A("|---|---|---|---|---|---|---|")
        for r in recs:
            # 这两格显示什么由 `eg.strict_cells` 定（不可答题写 `—`，不写「否」）——
            # 判定逻辑不留在脚本里，它要能被单元测试直接喂数据。
            cell, cell_t = eg.strict_cells(r)
            A(f"| {r['id']} | {r['tag']} | {r['status']} | "
              f"{cell} | {cell_t} | "
              f"{r['attempts']} | {r['why']} |")
        A("")
    if noise_band is not None:
        A("## 噪声带")
        A("")
        A(f"实测噪声带 = **{noise_band} 题**（`experiments/21_noise_band.py`："
          f"同一臂同一温度连跑两遍，逐题比 EX，翻转的题数）。")
        A("")
        A("任何版本之间的差异都要先与它比较；落在噪声带之内的只能写"
          "「方向为 X，未达可判定水平」，禁用「显著 / 大幅 / 提升 / 极大」。")
        A("")
    # 报告**先落盘**再查措辞：一份措辞越界的报告也比没有报告有用。
    # 命中时把禁用词返回给调用方，由它决定退出码。
    return runner.write_report(REPORTS / "text2sql_results.md", "\n".join(lines))


def write_not_run(reason: str) -> None:
    """没跑成时写的东西。**故意不叫 `text2sql_results.md`。**

    免得有人把这页当成结果读走 —— 一张空表格比没有表格更容易造成误解。

    ## 为什么「未完成」的报告也要有口径小节

    这页里出现了「36 题」这样的数字。数字出现在报告里就得说清它是什么 ——
    「未完成」不等于「可以不写口径」。而且这一节正好是这条纪律的说明处：
    **下面没有任何一行是跑出来的结果**，所以也没有任何 EX / token / 成本数字。
    """
    qs = runner.load_questions()["questions"]
    n = len(qs)
    n_rows = sum(1 for q in qs if q["expect"] == "rows")
    (REPORTS / "text2sql_NOT_RUN.md").write_text(
        "# Text2SQL 评测：未完成\n\n"
        f"**卡在哪一步**：{reason}\n\n"
        "## 口径\n\n"
        f"- **脚本**：`experiments/09_text2sql_eval.py`，退出码 {E_NO_LLM}（未完成，非失败）\n"
        f"- **题量**：`data/eval/questions.json` 共 **{n} 题** = "
        f"**{n_rows} 题可答**（各带一条参考解）+ **{n - n_rows} 题本就答不了**"
        "（模型正确应答「答不了」）。逐题定义见该文件的 `expect` 字段\n"
        "- **下方所有数字的性质**：**没有一个是跑出来的结果**。本页不含 EX / token / "
        "成本 / 噪声带数字\n\n"
        "## 已经就绪的部分\n\n"
        f"- 题集 `data/eval/questions.json`（{n} 题）已就绪，参考解可用 "
        "`python experiments/09_text2sql_eval.py --check-questions` 实跑核验，**不需要 LLM**。\n"
        "- 评分口径 `mcp_server/eval_grading.py` 已就绪，由 `tests/test_eval_grading.py` 钉住。\n"
        "- 状态机 `mcp_server/t2sql_core.py` 已就绪，控制流由假模型测过（不花钱）。\n\n"
        "## 缺的是什么\n\n"
        "只有一份能调用的模型凭据。**本仓库没有编造任何 EX / token / 成本数字** —— "
        "跑不出来的地方就写「未完成」，见 `BLOCKERS.md`。\n",
        encoding="utf-8")


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="Text2SQL 评测（36 题 × 3 版）")
    ap.add_argument("--check-questions", action="store_true",
                    help="只核验题集里的参考解，不需要 LLM")
    ap.add_argument("--fake", choices=_FAKE_MODES, default="",
                    help="用假模型自检整条流水线，不需要 LLM（oracle 该满分、naive 该低分、"
                         "flaky 该走一次重写）")
    ap.add_argument("--versions", default=",".join(t2sql_core.VERSIONS),
                    help=f"要跑的提示词版本，逗号分隔（默认 {','.join(t2sql_core.VERSIONS)}）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（试跑用，默认全跑）")
    ap.add_argument("--ids", default="", help="只跑这些题号，逗号分隔（续跑用）")
    ap.add_argument("--render-only", action="store_true",
                    help="不调模型：按已有的 text2sql_results.v*.json 重出一份报告。"
                         "改报告措辞/表格时用，不花调用")
    args = ap.parse_args()

    if args.check_questions:
        return check_questions()

    if args.render_only:
        return render_from_disk(args.versions)

    db = paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        print("         先跑：python experiments/01_build_db.py", file=sys.stderr)
        return E_NO_DB

    if args.fake:
        return run_selftest(args.fake, db)

    try:
        data = load_questions()
    except (FileNotFoundError, ValueError) as e:
        print(f"[FAIL] 题集有问题：{e}", file=sys.stderr)
        return E_BAD_QUESTIONS

    qs = data["questions"]
    if args.ids:
        want = {s.strip() for s in args.ids.split(",") if s.strip()}
        qs = [q for q in qs if q["id"] in want]
        if not qs:
            print(f"[FAIL] --ids 一个题号都没匹配上：{args.ids}", file=sys.stderr)
            return E_USAGE
    if args.limit:
        qs = qs[:args.limit]

    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    for v in versions:
        try:
            t2sql_core.prompt_for(v)
        except ValueError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return E_USAGE

    # ★ 先确认模型凭据齐不齐，再去跑。跑到一半才发现没 key，等于白跑。
    try:
        t2sql_core.real_llm([{"role": "user", "content": "ping"}], "preflight")
    except RuntimeError as e:
        msg = f"缺少模型配置：{e}"
        print(f"[未完成] {msg}", file=sys.stderr)
        print("         见 .env.example；这一步需要一份可用的模型凭据。", file=sys.stderr)
        write_not_run(msg)
        return E_NO_LLM
    except Exception as e:  # noqa: BLE001
        # 凭据在但调用不通（网络、限流、余额）。**不许当成跑完了。**
        msg = f"模型调用失败（凭据已配置，但请求没成功）：{type(e).__name__}: {e}"
        print(f"[未完成] {msg}", file=sys.stderr)
        write_not_run(msg)
        return E_NO_LLM

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "text2sql_NOT_RUN.md").unlink(missing_ok=True)

    conn = runner.connect(db)
    all_records: dict[str, list[dict]] = {}
    total_errors: list[str] = []
    try:
        for v in versions:
            print(f"\n=== 提示词版本 {v} ===")
            out_json = REPORTS / f"text2sql_results.{v}.json"
            recs, errors = run_version(v, qs, conn, db, out_json)
            all_records[v] = recs
            total_errors.extend(errors)
            s = eg.summarize(recs)
            print(f"  {v}: 严格 {s['n_strict']}/{s['n_answerable']}、"
                  f"容忍 {s['n_tolerant']}/{s['n_answerable']}、"
                  f"调用 {s['calls']} 次、"
                  f"token {s['prompt_tokens']}+{s['completion_tokens']}")
    finally:
        conn.close()

    band = load_noise_band()
    banned = write_report(all_records, band)

    if banned:
        print(f"\n[FAIL] 报告里出现禁用词 {sorted(set(banned))}："
              f"{REPORTS / 'text2sql_results.md'}\n"
              f"       报告已落盘（数字是真的），但措辞越界，这一轮不算通过。",
              file=sys.stderr)
        return E_BAD_QUESTIONS
    if total_errors:
        print(f"\n[FAIL] 有 {len(total_errors)} 题在调用层失败（不是判错）：", file=sys.stderr)
        for e in total_errors:
            print(f"  - {e}", file=sys.stderr)
        return E_RUN_ERROR
    print(f"\n[OK] 全部跑完，报告见 reports/text2sql_results.md")
    return E_OK


def render_from_disk(versions_arg: str) -> int:
    """按**已有的**逐题 JSON 重出一份报告，一次模型调用都不发。

    ## 为什么需要它

    报告的文字（表头、口径小节、表格里怎么表示「不适用」）是**会改的** ——
    改一次就跑一遍 108 次调用，既费钱又让账本里的调用次数翻倍，
    而账本按 `(source, model)` 累计，翻倍之后成本报告就不再等于「这一轮用了多少」。

    重渲染不引入新的数字：它读的就是上一次真跑写下的 `records`。
    所以它的口径是「**同一批记录，换一种排版**」，而不是「又跑了一轮」。

    ## 它**不能**做什么

    不能拿它在没有真跑过的情况下生成一份报告 —— 没有 JSON 就直接判 FAIL 退出，
    不会去写一份空表。一张写着 0 的空表比没有表更容易被读成结果。
    """
    versions = [v.strip() for v in versions_arg.split(",") if v.strip()]
    all_records: dict[str, list[dict]] = {}
    missing: list[str] = []
    for v in versions:
        p = REPORTS / f"text2sql_results.{v}.json"
        if not p.is_file():
            missing.append(p.name)
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        all_records[v] = data["records"]
    if missing:
        print(f"[FAIL] 没有这些逐题记录，不能凭空渲染报告：{missing}", file=sys.stderr)
        print("       先真跑一次：python experiments/09_text2sql_eval.py", file=sys.stderr)
        return E_NO_DB

    banned = write_report(all_records, load_noise_band())
    if banned:
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_BAD_QUESTIONS
    print("[OK] 已按现有逐题记录重出报告（未调用模型）")
    return E_OK


def load_noise_band() -> int | None:
    """有实测噪声带就带上，没有就返回 None（报告里那一节直接不出现）。"""
    p = REPORTS / "noise_band.json"
    if not p.is_file():
        return None
    try:
        return int(json.loads(p.read_text(encoding="utf-8"))["noise_band"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    sys.exit(main())
