"""评测流水线的公共部分：读题集、跑参考解、判一题、跑一版。

## 为什么要单独一个模块

`09_text2sql_eval.py`、`13_text2sql_colloquial.py`、`21_noise_band.py` 都要做
同样三件事：跑参考解、把状态机的返回判成分数、逐题落盘。**如果各写一份**，
「口径」就不再是一处定义了 —— 三份里随便哪一份改了，报告上的数字就悄悄换了含义，
而且没有任何现象能让人看出来。这是这个仓库最在意的一类坏味道。

放在 `mcp_server/` 而不是 `experiments/`，是因为它是被复用的口径代码，
要能被测试直接 import（见 `tests/test_eval_grading.py` 的邻居们）。

## 与 `eval_grading.py` 的分工

- `eval_grading.py` —— 纯函数：怎么算两个结果集相等、p 值怎么算、措辞怎么选。
  **不碰数据库、不碰模型。**
- `eval_runner.py`（本文件）—— 有副作用的那一半：连库跑参考解、驱动状态机、
  把结果写盘。它把「判分」这件事委托给 `eval_grading`，自己不做口径判断。
"""
from __future__ import annotations

import json
import sqlite3
import time
import traceback
from pathlib import Path

from mcp_server import eval_grading as eg
from mcp_server import guardrails, paths, t2sql_core

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
QUESTIONS = ROOT / "data" / "eval" / "questions.json"

# 与护栏自动补的 `LIMIT 200` 对齐。参考解超过这个行数就没法公平比较：
# 模型答对了也会被截断，那时考的是我自己的行数上限，不是模型。
MAX_REF_ROWS = 200


def load_questions(path: Path | None = None) -> dict:
    p = path or QUESTIONS
    if not p.is_file():
        raise FileNotFoundError(f"题集文件不存在：{p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not data.get("questions"):
        raise ValueError(f"题集文件里没有 questions：{p}")
    return data


def connect(db: Path | None = None) -> sqlite3.Connection:
    """评测用的只读连接。

    ★ **故意不挂 L3 authorizer**：参考解是**我写的**，不是用户输入，
    它要走的是「拿到正确答案」这条路，而不是「被护栏挡住」那条路。
    这一点与 `db_tools._introspect_connect` 是同一个道理（那条路也只看只读属性）。
    只读属性照旧（`mode=ro`），所以写不进去任何东西。
    """
    p = Path(db) if db is not None else paths.chinook_db()
    if not p.is_file():
        raise FileNotFoundError(
            f"数据库文件不存在：{p}\n先跑一次构建脚本：python experiments/01_build_db.py")
    return sqlite3.connect(guardrails._readonly_uri(p), uri=True, timeout=5.0)


def run_ref(conn: sqlite3.Connection, sql: str) -> tuple[list[list], list[str]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in (cur.description or [])]
    rows = [list(r) for r in cur.fetchall()]
    return rows, cols


# ---------------------------------------------------------------- 判分
def grade_one(q: dict, result: dict, conn: sqlite3.Connection) -> dict:
    """把状态机的返回值判成分数。**不抛异常。**

    一道题判分失败不该让整轮评测崩掉 —— 那会丢掉前面已经花钱跑出来的结果。
    """
    rec = {
        "id": q["id"], "tag": q.get("tag", ""), "question": q["question"],
        # 口语化题集用它指回正式题集的题号，配对比较靠这个字段对齐。
        "same_as": q.get("same_as", ""),
        "expect": q["expect"], "version": result.get("version", ""),
        "status": result.get("status", ""), "attempts": result.get("attempts", 0),
        "sql": result.get("sql", ""), "error": result.get("error", ""),
        "calls": result.get("usage", {}).get("calls", 0),
        "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
        "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
        "strict": False, "tolerant": False, "why": "",
    }
    if q["expect"] == "unsupported":
        # 「答不了」的题只有一种正确表现：老实说答不了。
        # 模型要是硬写了一条 SQL，就算它跑得通也不算对 —— 那正是这几题要抓的。
        rec["why"] = ("模型说了答不了" if result.get("status") == "unsupported"
                      else f"这题本来答不了，模型却给了 status={result.get('status')!r}")
        return rec

    if result.get("status") != "ok":
        rec["why"] = (f"没拿到可用结果：status={result.get('status')}，"
                      f"{str(result.get('error', ''))[:80]}")
        return rec

    try:
        ref_rows, ref_cols = run_ref(conn, q["ref_sql"])
    except sqlite3.Error as e:
        rec["why"] = f"参考解执行失败（题集的问题，不是模型的）：{type(e).__name__}: {e}"
        return rec

    cmp = eg.compare(result.get("rows") or [], result.get("columns") or [],
                     ref_rows, ref_cols)
    rec.update(strict=cmp["strict"], tolerant=cmp["tolerant"], why=cmp["why"])
    return rec


# ---------------------------------------------------------------- 跑一版
def run_version(version: str, qs: list[dict], conn: sqlite3.Connection,
                db: Path, out_json: Path, llm=None, record_cost: bool = True,
                label: str = "") -> tuple[list[dict], list[str]]:
    """跑完一版。**逐题落盘** —— 中断之后能看出跑到哪儿了，也方便续跑。

    `label` 只影响落盘文件里的标记与进度输出（噪声带那一步要跑两遍同一个版本，
    两遍得能分开）。
    """
    records: list[dict] = []
    errors: list[str] = []

    for i, q in enumerate(qs, 1):
        t0 = time.monotonic()
        try:
            result = t2sql_core.run(q["question"], version=version, db_path=db,
                                    llm=llm, record_cost=record_cost)
        except Exception as e:  # noqa: BLE001
            # 模型层的问题（网络、限流、配额）**不许**伪装成「模型答错了」。
            errors.append(f"{q['id']} 调用失败：{type(e).__name__}: {e}")
            print(f"  [{i:2d}/{len(qs)}] {q['id']} ✗ 调用失败：{type(e).__name__}: {e}",
                  file=__import__("sys").stderr)
            traceback.print_exc(file=__import__("sys").stderr)
            continue

        rec = grade_one(q, result, conn)
        # ★ 耗时**只打印，不写进落盘记录**。
        #
        # 落盘的 JSON 是**可复现产物**：同一份代码跑两遍应当逐字节相同 ——
        # 「跑完闸门 `git status` 还是干净的」本身就是一条可验证的声明，
        # 而它靠的正是这里不写时钟读数。实测：同一份代码两次得到 0.02 与 0.03，
        # 于是每跑一次闸门就有 6 个 JSON 显示「已修改」，把 `git status` 变成了噪声，
        # 也让「这份报告是不是刚跑的」没法再靠 diff 判断。
        #
        # 不损失必需的数字：这一层的口径要的是**调用次数与双向 token**
        # （见 `DECISIONS.md` D-9），耗时只在这行进度里给人看。
        dt = round(time.monotonic() - t0, 2)
        records.append(rec)
        mark = _mark(rec)
        # ★ 「不可答」的题没有「严格 / 容忍」可言 —— 它压根没有参考行集，
        # 判据是「有没有老实说答不了」。硬打 `strict=False` 会让人以为模型答错了：
        # 实测屏幕上出现过「✓ strict=False tolerant=False」，而那一题
        # 模型明明老实说了答不了（`_mark` 判对，这行字却在拆它的台）。
        verdict = (f"老实说答不了={rec['status'] == 'unsupported'}"
                   if rec["expect"] == "unsupported"
                   else f"strict={rec['strict']} tolerant={rec['tolerant']}")
        print(f"  [{i:2d}/{len(qs)}] {q['id']} {mark} {verdict} "
              f"calls={rec['calls']} {dt}s")
        dump_progress(out_json, records, version, errors, label)

    return records, errors


def _mark(rec: dict) -> str:
    if rec["expect"] == "unsupported":
        return "✓" if rec["status"] == "unsupported" else "✗"
    if rec["strict"]:
        return "✓"
    return "~" if rec["tolerant"] else "✗"


def write_report(path: Path, text: str) -> list[str]:
    """把报告落盘，并**全文查一遍禁用词**。返回命中的词（正常应当为空）。

    ## 为什么要在真实产物上查，而不是靠静态扫描

    `reports/` 里的报告文字是脚本拼出来的。静态扫描源码会把
    「禁用显著/大幅/提升/极大」这种**列举禁用词的文档字符串**也算成违规
    （实测 26 处命中，全部是这类），于是真违规反而淹没在假命中里。
    所以分工是：

    - 静态扫描管**手写**的成品（范围见 `tests/test_repo_hygiene.py`）；
    - 这个函数管**程序生成**的成品 —— 在真实产物上查，比在源码上查更准，
      也不会误伤执行机制本身。

    ★ 命中时**先落盘再返回**，不抛异常。理由：一份措辞越界的报告，
    也比一份不存在的报告有用 —— 后者意味着「这一轮结果丢了」。
    调用方拿到非空返回值后必须以非零码结束，不许当没事发生。
    """
    Path(path).write_text(text, encoding="utf-8")
    return eg.check_wording(text)


def dump_progress(path: Path, records: list[dict], version: str,
                  errors: list[str], label: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": version,
        "label": label,
        "summary": eg.summarize(records),
        "records": records,
        "errors": errors,
        "note": [
            "口径见 mcp_server/eval_grading.py 文件头；题集见 data/eval/questions.json。",
            "分母只数可答题；模型说『答不了』的题单独统计。",
            "这个文件是**逐题追加式**写的（每题覆盖一次），所以中途看到的它是「跑到哪儿了」。",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 假模型
# 这一节回答的是：「评测流水线本身可信吗？」
#
# 参考解核验只证明了参考解能跑；它没有证明判分与流水线在真实数据上是对的。
# 没有真模型的时候，唯一能验证这条路的办法就是**换一个行为已知的假模型进去**：
#
#   oracle —— 每题都回参考 SQL（本该满分）
#   naive  —— 每题都回 `SELECT * FROM Artist`（本该接近零分）
#   flaky  —— 第一次回错列名、第二次回参考 SQL（本该满分，且每题调用两次）
#
# oracle 拿不到 100% 说明评分或流水线有 bug；naive 拿到高分说明评分太松。
# **两个方向都要有**，否则「全判对」和「全判错」这两种坏实现都能蒙混过关。
FAKE_MODES = ("oracle", "naive", "flaky")

_NAIVE_SQL = "SELECT * FROM Artist"


def question_of(messages: list[dict]) -> str:
    """从拼好的对话里把原始问题抠出来（对应 `t2sql_core.build_messages` 的格式）。"""
    import re

    pat = re.compile(r"问题：(.*?)\n\n", re.S)
    for m in messages:
        if m.get("role") == "user":
            hit = pat.search(m.get("content", ""))
            if hit:
                return hit.group(1).strip()
    return ""


def make_fake_llm(mode: str, by_question: dict[str, dict], calls: dict | None = None):
    """造一个假模型。它**不联网、不花钱**，行为完全由 `mode` 决定。

    `calls` 传一个字典进来可以记录每题被调用了几次 —— 光看结果里的 `attempts`
    不足以证明「真的重试了」，还要看它确实被调了两次。
    """
    if mode not in FAKE_MODES:
        raise ValueError(f"未知的假模型 {mode!r}，可选：{list(FAKE_MODES)}")
    calls = calls if calls is not None else {}
    usage = {"prompt_tokens": 100, "completion_tokens": 10}

    def _llm(messages: list[dict], label: str = "") -> dict:
        q_text = question_of(messages)
        q = by_question.get(q_text)
        if q is None:
            # 抠不出题目说明对话格式变了 —— 这时候必须炸，不许悄悄回一句无关的 SQL，
            # 那会让自检给出一个看起来很正常的假分数。
            raise RuntimeError(
                f"假模型抠不出问题原文，对话格式可能变了。首条用户消息："
                f"{next((m['content'][:120] for m in messages if m.get('role') == 'user'), '')!r}")

        calls[q["id"]] = calls.get(q["id"], 0) + 1
        attempt = calls[q["id"]]

        if q["expect"] == "unsupported":
            # oracle 与 flaky 老实说答不了；naive 硬写一条 SQL —— 这正是那几题要抓的。
            text = (_NAIVE_SQL if mode == "naive"
                    else f"-- UNSUPPORTED {q.get('why', '')}")
            return {"text": text, "model": f"fake-{mode}", "usage": usage}

        if mode == "oracle":
            sql = q["ref_sql"]
        elif mode == "naive":
            sql = _NAIVE_SQL
        else:  # flaky：第一次故意写错列名，逼出一次重写
            sql = ("SELECT no_such_column FROM Artist" if attempt == 1 else q["ref_sql"])
        return {"text": f"```sql\n{sql}\n```", "model": f"fake-{mode}", "usage": usage}

    return _llm
