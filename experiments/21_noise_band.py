"""实测噪声带：同一臂、同一温度、连跑两遍，逐题比判定结果。

## 为什么噪声带要**实测**而不是写一个常数

「v2 比 v1 多对了 2 题」这句话有没有信息量，取决于**什么都不改**的情况下
这个数字会晃多少。晃 0 题和多对 2 题是发现；本来就会晃 3 题，那多对 2 题什么都不是。

写死一个「噪声带 = 1」看着很专业，但那个 1 是从哪来的？没有来源的常数
就是一句没法核对的断言。这里改成实跑：同一版提示词、同一批题、`temperature=0`
（`t2sql_core.real_llm` 里写死的），**连跑两遍**，逐题比对判定结果，
翻转的题数就是噪声带。

## 为什么 `temperature=0` 还会有波动

`temperature=0` 通常只保证「同一批输入尽量选概率最高的那个 token」，它**不保证**
两次请求拿到完全一样的输出：服务端的批处理装填、MoE 路由、浮点归约顺序、
版本灰度都可能让它变。所以这个波动必须量，不能假定为零。

## 噪声带的定义（口径）

逐题的**判定结果**在两次运行之间翻转，就算一次波动。判定结果是：

- 可答题（32 道）→ 严格 EX（`strict`，列数与行多重集都相同）
- 不可答题（4 道）→ 模型有没有老实说「答不了」

分母是全部 36 题。**同时**单独报「只数可答题」的翻转数，方便与
`eval_grading.summarize()` 的分母口径对上。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 量完了，`reports/noise_band.json` 已写出 |
| 1 | 跑的过程中有题在调用层报错 |
| 2 | 自检不通过（`--fake`） |
| 3 | 示例库没构建 |
| 4 | 用法错误 |
| 5 | **LLM 未配置** —— 不写 `noise_band.json`，改写 `noise_band_NOT_RUN.md` |

## 自检（`--fake`，不花钱）

噪声带 = 0 这个结论有两种截然不同的成因：**真的稳**，或者**尺子是坏的**。
所以自检要给两个方向：

- `--fake stable`：两遍完全一样 → 噪声带**必须**是 0；
- `--fake jitter`：第二遍故意在 3 道题上给出不同答案 → 噪声带**必须**正好是 3。

只做前者的话，一个「永远报 0」的坏实现也能通过。
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

E_OK = 0
E_RUN_ERROR = 1
E_SELFTEST = 2
E_NO_DB = 3
E_USAGE = 4
E_NO_LLM = 5

# 自检里「故意抖」的那几道题的题号与数量。写死是为了让断言能写死。
JITTER_IDS = ("q07", "q15", "q22")

# 自检用：一个把指定题号答错的假模型。
_WRONG_SQL = "SELECT 0"


# 比对逻辑是纯函数，放在 `mcp_server/eval_grading.py` 里 ——
# 本文件以数字开头，**没法被 import**，写在里面就只能靠跑脚本来测它。
# 定义见那个模块的 `flip_verdict()` 与 `compare_runs()`。
compare_runs = eg.compare_runs


# ---------------------------------------------------------------- 自检
def run_selftest(mode: str, db: Path, version: str) -> int:
    """用假模型证明「这把尺子量得出波动」。**不联网、不花钱。**"""
    data = runner.load_questions()
    qs = data["questions"]
    by_question = {q["question"]: q for q in qs}
    by_id = {q["id"]: q for q in qs}

    if mode == "stable":
        llm_a = runner.make_fake_llm("oracle", by_question)
        llm_b = runner.make_fake_llm("oracle", by_question)
        expect = 0
        why = "两遍都用同一个满分假模型，判定结果不该有任何翻转"
    else:  # jitter
        llm_a = runner.make_fake_llm("oracle", by_question)
        inner = runner.make_fake_llm("oracle", by_question)
        # 用**问题原文**（而不是题号）来认题：假模型只拿得到对话文本，
        # 这正是真实模型看到的全部信息。用题号认题等于作弊。
        jitter_qs = {by_id[i]["question"] for i in JITTER_IDS if i in by_id}
        missing = [i for i in JITTER_IDS if i not in by_id]
        if missing:
            # 题号写错就要当场炸。悄悄少抖一题的话，自检会量出一个更小的数，
            # 而断言期望的仍是 3 —— 那时报出来的是「尺子坏了」，方向完全指错。
            raise ValueError(f"自检用的题号不在题集里：{missing}")

        def llm_b(messages: list[dict], label: str = "") -> dict:
            out = inner(messages, label)
            if runner.question_of(messages) in jitter_qs:
                out = dict(out, text=f"```sql\n{_WRONG_SQL}\n```")
            return out

        expect = len(JITTER_IDS)
        why = f"第二遍故意让 {len(JITTER_IDS)} 道题（{', '.join(JITTER_IDS)}）给出不同答案"

    conn = runner.connect(db)
    try:
        print(f"=== 噪声带自检：{mode}（{why}）===")
        # ★ record_cost=False：假模型的用量不许混进真账本。
        a, err_a = runner.run_version(version, qs, conn, db,
                                      REPORTS / f"noise_band_selftest.{mode}.a.json",
                                      llm=llm_a, record_cost=False, label=f"selftest-{mode}-a")
        b, err_b = runner.run_version(version, qs, conn, db,
                                      REPORTS / f"noise_band_selftest.{mode}.b.json",
                                      llm=llm_b, record_cost=False, label=f"selftest-{mode}-b")
    finally:
        conn.close()

    problems: list[str] = []
    if err_a or err_b:
        problems.append(f"调用层报错：{(err_a + err_b)[:2]}")
    try:
        got = compare_runs(a, b)
    except ValueError as e:
        print(f"[FAIL] 自检不通过：{e}", file=sys.stderr)
        return E_SELFTEST

    print(f"  实测噪声带 = {got['noise_band']} 题（期望 {expect}）")
    if got["noise_band"] != expect:
        problems.append(f"噪声带实测 {got['noise_band']}，期望 {expect} —— "
                        f"尺子要么量不出来，要么把没波动当成了波动")
    if mode == "jitter":
        got_ids = tuple(sorted(f["id"] for f in got["flips"]))
        if got_ids != tuple(sorted(JITTER_IDS)):
            problems.append(f"翻转的题不是预期那几道：{got_ids}")

    if problems:
        print(f"\n[FAIL] 自检不通过，{len(problems)} 处：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_SELFTEST
    print(f"[OK] 自检通过：{mode} 的噪声带与预期一致")
    return E_OK


# ---------------------------------------------------------------- 主流程
def measure(db: Path, version: str, tag_a: str, tag_b: str) -> int:
    data = runner.load_questions()
    qs = data["questions"]
    REPORTS.mkdir(parents=True, exist_ok=True)

    conn = runner.connect(db)
    try:
        print(f"=== 第 1 遍（{tag_a}）===")
        a, err_a = runner.run_version(version, qs, conn, db,
                                      REPORTS / f"noise_band.{tag_a}.json",
                                      label=tag_a)
        print(f"\n=== 第 2 遍（{tag_b}）===")
        b, err_b = runner.run_version(version, qs, conn, db,
                                      REPORTS / f"noise_band.{tag_b}.json",
                                      label=tag_b)
    finally:
        conn.close()

    if err_a or err_b:
        print(f"\n[FAIL] 有 {len(err_a) + len(err_b)} 题在调用层失败，"
              f"**没有**写出噪声带：掉题不是波动，不能当波动报。", file=sys.stderr)
        for e in (err_a + err_b)[:5]:
            print(f"  - {e}", file=sys.stderr)
        return E_RUN_ERROR

    try:
        got = compare_runs(a, b)
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return E_RUN_ERROR

    out = {
        "noise_band": got["noise_band"],
        "n_questions": got["n_questions"],
        "n_answerable": got["n_answerable"],
        "n_answerable_flips": got["n_answerable_flips"],
        "version": version,
        "temperature": 0,
        "runs": [tag_a, tag_b],
        "flips": got["flips"],
        "definition": [
            "噪声带 = 同一臂（同一个提示词版本）、temperature=0、连跑两遍，"
            "逐题判定结果翻转的题数。分母是全部 36 题。",
            "可答题的判定结果取严格 EX（strict）；不可答题取『模型有没有老实说答不了』。",
            "n_answerable_flips 是同一批翻转里只数那 32 道可答题的部分，"
            "方便与 eval_grading.summarize() 的分母口径对上。",
        ],
        "caveat": [
            "这是**一对**重复对照量出来的波动。一对样本估不出波动的分布，"
            "所以它只能当「低于这个数就别当回事」的下限用，不是置信区间。",
            "它也不排除更小概率的更大波动：真正的极端抖动需要更多重复才看得见。",
        ],
    }
    (REPORTS / "noise_band.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "noise_band_NOT_RUN.md").unlink(missing_ok=True)

    print(f"\n[OK] 噪声带实测 = {got['noise_band']} 题"
          f"（其中可答题 {got['n_answerable_flips']} 题），"
          f"见 reports/noise_band.json")
    for f in got["flips"]:
        print(f"  · {f['id']}（{f['tag']}）{f['run_a']} → {f['run_b']}")
    return E_OK


def write_not_run(reason: str) -> None:
    """没量成时写的东西。**故意不叫 `noise_band.json`。**

    一个内容为零的噪声带文件会让 20 号脚本读到一个假的「没有波动」，
    那比缺文件危险得多 —— 缺文件会报错，假文件会安静地把差异说成真实差异。

    ## 为什么「未完成」的报告也要有口径小节

    这页里出现了 0 和 3 两个数字（自检的**期望值**，不是测出来的噪声带）。
    期望值和实测值长得一模一样，不写清楚就会被读成「噪声带量出来是 0」——
    这正是本页最需要防的误读。
    """
    (REPORTS / "noise_band_NOT_RUN.md").write_text(
        "# 噪声带：未完成\n\n"
        f"**卡在哪一步**：{reason}\n\n"
        "## 口径\n\n"
        f"- **脚本**：`experiments/21_noise_band.py`，退出码 {E_NO_LLM}（未完成，非失败）\n"
        "- **本页出现的 `0` 与 `3` 是自检的期望值，不是实测噪声带**：`--fake stable` "
        "要求量出 0，`--fake jitter` 要求正好量出 3。它们是「尺子准不准」的判据，"
        "和「这批题有多稳」是两件事\n"
        "- **实测噪声带**：**不存在**。`reports/noise_band.json` 没有被创建 —— "
        "这是刻意的，见下\n\n"
        "## 已经就绪的部分\n\n"
        "- 测量脚本 `experiments/21_noise_band.py` 已就绪，口径写在它的文件头。\n"
        "- 尺子本身由 `--fake stable` / `--fake jitter` 两个自检证明过："
        "前者要求量为 0，后者要求正好量出 3。**不需要 LLM。**\n"
        "- 比对逻辑 `compare_runs()` 是纯函数，由 `tests/test_noise_band.py` 钉住。\n\n"
        "## 缺的是什么\n\n"
        "只有一份能调用的模型凭据。**本仓库没有编造噪声带** —— "
        "`20_pair_test.py` 读不到这个文件时会拒绝给出版本差异的结论，"
        "而不是假装噪声带是 0。见 `BLOCKERS.md`。\n",
        encoding="utf-8")


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="实测噪声带：同一臂连跑两遍")
    ap.add_argument("--fake", choices=("stable", "jitter"), default="",
                    help="用假模型自检这把尺子，不需要 LLM"
                         "（stable 该量出 0，jitter 该正好量出 3）")
    ap.add_argument("--version", default=t2sql_core.DEFAULT_VERSION,
                    help=f"量哪一版（默认 {t2sql_core.DEFAULT_VERSION}）")
    args = ap.parse_args()

    db = paths.chinook_db()
    if not db.is_file():
        print(f"[未完成] 示例库还没构建：{db}", file=sys.stderr)
        print("         先跑：python experiments/01_build_db.py", file=sys.stderr)
        return E_NO_DB

    try:
        t2sql_core.prompt_for(args.version)
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return E_USAGE

    if args.fake:
        return run_selftest(args.fake, db, args.version)

    # 与 09 同一套预检：跑到一半才发现没 key，等于白跑。
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

    return measure(db, args.version, "run1", "run2")


if __name__ == "__main__":
    sys.exit(main())
