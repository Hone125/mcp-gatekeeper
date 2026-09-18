"""Text2SQL 的评分口径与统计量。**纯函数，不联网、不碰模型、不碰数据库。**

## 为什么把口径抽出来单独放

评分口径是评测报告里最容易「悄悄变松」的东西。写在脚本里、用几个 if 拼出来的话，
没人说得清上一版和这一版的 0.72 是不是同一个 0.72。抽成模块之后：

- 它能被单元测试逐条钉住（`tests/test_eval_grading.py`），
- 报告可以直接引用它文件头的定义，
- 「口径变了」这件事会在 diff 里露出来，而不是藏在某个脚本的中间。

## 两条口径，都要报

| 口径 | 含义 | 为什么要有 |
|---|---|---|
| **严格 EX** | 列数相同，且**行内容的多重集**完全相同（列序也算） | 这是最不含糊的那条 |
| **投影容忍 EX** | 列数相同，且**存在一个列的重排**使行多重集相同 | `SELECT LastName, FirstName` 和 `SELECT FirstName, LastName` 是同一个正确答案 |

两条都报，是为了让「模型对列序敏不敏感」这件事有数据 —— 只报宽松口径会把
「列选错了但巧了」也算对，只报严格口径会把一堆正确答案判错。

## 三个刻意的选择（都有代价，写清楚）

### 1. 行序**不**参与评分

`ORDER BY` 写进参考 SQL 是为了让「前 5 名」这种题在并列处有确定的答案，
不是为了要求模型也用同样的顺序输出。模型返回同样 5 行、顺序不同，内容是对的。

**代价**：真有模型 `ORDER BY` 写错方向、恰好取到同一批行时，会被判对。
对本数据集来说这种情况要靠并列才能发生，而参考 SQL 里的次序键已经把并列排除了。

### 2. 列**名**不参与评分

模型写 `COUNT(*)` 得到列名 `COUNT(*)`，写 `COUNT(*) AS n` 得到 `n`。
两者是同一个答案。要求列名一致等于在考别名起得好不好。

### 3. 浮点带容差：小数点后 6 位

`AVG(Total)` 和 `SUM(Total)/COUNT(*)` 是两个都对、末位不同的写法。
不容差的话，评分测的是浮点误差，不是 SQL 对不对。

**代价**：理论上会把「差在小数点后第 7 位」的错答案判对。对本数据集
（金额两位小数、时长整数毫秒）来说，这个宽容度远小于任何真实错误的量级。

## 措辞纪律（§4 D3）

`describe_difference()` 是唯一允许用来描述版本差异的函数，它的词表是封闭的 ——
「显著 / 大幅 / 提升 / 极大」不在里面。噪声带之内的差异只能写成
「方向为 X，未达可判定水平」。`tests/test_eval_grading.py` 里有一条测试
直接断言这几个词不会从它嘴里出来。
"""
from __future__ import annotations

import math
from collections import Counter

from mcp_server.selfcheck import OVERSTATED

FLOAT_NDIGITS = 6

# 列重排的枚举上限。超过这个列数就不枚举了 —— 阶乘会长到没法跑，
# 而且那种宽表本来也不该出现在这个题集里（参考解最宽 2 列）。
MAX_PERM_COLS = 6

# 描述差异时**禁用**的词。它们会把噪声带之内的波动说成事实。
#
# ★ **不要在这里另起一份词表。** 原先是自成一份，与 `selfcheck.OVERSTATED`
#   不一样，于是有一个能同时骗过两边的空洞（详见 `mcp_server/selfcheck.py`
#   里 `OVERSTATED` 上方的注释）。现在只有一个定义处，本模块从那里导入。
#   本模块仍然是纯的：`selfcheck` 只 import `re` 与 `pathlib`，导入时没有 I/O。
BANNED_WORDS = OVERSTATED


# ---------------------------------------------------------------- 值的归一
def _cell(v):
    """把一个单元格归一成可比较的元组。

    `None` 单列成一种，不和 0 / 空串混同 —— 「没有作曲家」和「作曲家是空串」
    在数据上是两回事。

    整数和浮点统一成数值：`COUNT(*)` 回 25、`AVG()` 回 25.0 都是 25。
    """
    if v is None:
        return ("null",)
    if isinstance(v, bool):
        return ("num", float(v))
    if isinstance(v, int):
        return ("num", round(float(v), FLOAT_NDIGITS))
    if isinstance(v, float):
        if math.isnan(v):
            return ("nan",)
        return ("num", round(v, FLOAT_NDIGITS))
    if isinstance(v, (bytes, bytearray)):
        return ("str", bytes(v).decode("utf-8", "replace"))
    return ("str", str(v))


def _row_key(rows, perm=None):
    """把结果集变成「行的多重集」。多重集而不是集合：重复行本身是信息。"""
    out = Counter()
    for row in rows:
        cells = tuple(row)
        if perm is not None:
            cells = tuple(cells[i] for i in perm)
        out[tuple(_cell(v) for v in cells)] += 1
    return out


def compare(model_rows, model_cols, ref_rows, ref_cols) -> dict:
    """按两条口径比较两个结果集。返回体里带**为什么会不一致**。"""
    n_m, n_r = len(model_cols), len(ref_cols)
    cols_equal = n_m == n_r

    strict = cols_equal and _row_key(model_rows) == _row_key(ref_rows)

    tolerant = False
    if cols_equal:
        if n_m <= MAX_PERM_COLS:
            ref_key = _row_key(ref_rows)
            for perm in _permutations(n_m):
                if _row_key(model_rows, perm) == ref_key:
                    tolerant = True
                    break
        else:
            # 列太多，不枚举重排。此时宽松口径退化成严格口径，并在 why 里说明 ——
            # 悄悄退化会让报告的措辞与事实不符。
            tolerant = strict

    return {
        "strict": strict,
        "tolerant": tolerant,
        "cols_model": n_m,
        "cols_ref": n_r,
        "rows_model": len(model_rows),
        "rows_ref": len(ref_rows),
        "why": _why(strict, tolerant, n_m, n_r, model_rows, ref_rows),
    }


def _permutations(n: int):
    """0..n-1 的全排列。n 很小（≤ 6），直接手写递归，不引 itertools 之外的依赖。"""
    import itertools

    return itertools.permutations(range(n))


def _why(strict, tolerant, n_m, n_r, model_rows, ref_rows) -> str:
    if strict:
        return ""
    if n_m != n_r:
        return f"列数不同：模型 {n_m} 列，参考 {n_r} 列"
    if tolerant:
        return "列序与参考不同，内容一致（宽松口径算对）"
    if len(model_rows) != len(ref_rows):
        return f"行数不同：模型 {len(model_rows)} 行，参考 {len(ref_rows)} 行"
    return "行内容与参考不一致（行数相同）"


# ---------------------------------------------------------------- 配对检验
def mcnemar_exact_p(b: int, c: int) -> float:
    """McNemar 精确检验的双尾 p 值。

    `b` / `c` 是两臂的**不一致对数**：`b` = A 对 B 错，`c` = A 错 B 对。
    一致的那些题（都对、都错）不提供信息，所以不进分母。

    双尾定义：`2 * P(X <= min(b, c))`，`X ~ Binomial(b + c, 0.5)`，上限截到 1.0。
    `b + c == 0` 时返回 1.0 —— 「没有任何不一致」不是「有差异」的证据。

    ★ 这条纪律是给报告兜底的：36 题里不一致对数通常只有个位数，
    p 值必然在 0.5～1.0 这个量级，**不可能显著**。所以报告里不许出现
    「显著 / 大幅 / 提升」这类词。见 `describe_difference()`。
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def beyond_noise(delta: int, noise_band: int) -> bool:
    """净差题数是否超出了噪声带。

    `noise_band` 是**实测**出来的（`experiments/21_noise_band.py`：同一臂同一温度
    连跑两遍，逐题比 EX，翻转的题数）。不是文档里声明的常数。

    判据取「严格大于」：差值**等于**噪声带时，观测到的波动完全可以用噪声解释，
    这时候说「有差异」就是过度解读。
    """
    return abs(delta) > noise_band


def describe_difference(name_a: str, name_b: str, only_a: int, only_b: int,
                        p: float, noise_band: int) -> str:
    """把一次配对比较写成一句话。**词表是封闭的。**

    这是唯一允许用来描述版本差异的函数 —— 别的写法容易顺手加上「显著」。
    """
    delta = only_a - only_b
    head = (f"{name_a} 与 {name_b} 的不一致对数为 {only_a + only_b} 题"
            f"（{name_a} 独对 {only_a} 题、{name_b} 独对 {only_b} 题），"
            f"净差 {delta:+d} 题，精确双尾 p = {p:.4f}，噪声带 {noise_band} 题。")

    if only_a + only_b == 0:
        return head + "两臂逐题结果完全一致，未观测到差异。"
    if not beyond_noise(delta, noise_band):
        direction = "为正" if delta > 0 else ("为负" if delta < 0 else "为零")
        return (head + f"净差方向{direction}，**未超出噪声带**，"
                       f"只能写作「方向{direction}，未达可判定水平」。")
    direction = "为正" if delta > 0 else "为负"
    return head + f"净差方向{direction}，**超出噪声带**。p 值仍需照实报出。"


def check_wording(text: str) -> list[str]:
    """返回文本里出现的禁用词。报告生成后自检用。"""
    return [w for w in BANNED_WORDS if w in text]


# ---------------------------------------------------------------- 噪声带
def flip_verdict(rec: dict) -> bool:
    """一道题在噪声带统计里的**判定结果**。

    - 可答题 → 严格 EX
    - 不可答题 → 模型有没有老实说「答不了」

    放在这里而不是放在 `experiments/21_noise_band.py` 里，是因为
    **以数字开头的模块没法被 import**，写在脚本里就只能靠跑脚本来测。
    这是纯函数，它该有单元测试（`tests/test_noise_band.py`）。
    """
    if rec.get("expect") == "unsupported":
        return rec.get("status") == "unsupported"
    return bool(rec.get("strict"))


def compare_runs(run_a: list[dict], run_b: list[dict]) -> dict:
    """逐题比对两遍的判定结果（噪声带的实测）。

    两遍按题号对齐。长度不同或题号对不齐时**抛异常**，不做任何猜测 ——
    某题在一遍里缺失是调用层掉了题，不是模型波动。把它当波动报出去，
    噪声带会凭空变大，之后所有版本差异都会被这个假噪声带掩盖。
    """
    if len(run_a) != len(run_b):
        raise ValueError(
            f"两遍的题数不同（{len(run_a)} vs {len(run_b)}）——"
            f"这是调用层掉题，不是模型波动，不能当噪声带报。")

    flips: list[dict] = []
    for ra, rb in zip(run_a, run_b):
        if ra["id"] != rb["id"]:
            raise ValueError(f"两遍题号对不齐：{ra['id']} vs {rb['id']}")
        va, vb = flip_verdict(ra), flip_verdict(rb)
        if va != vb:
            flips.append({
                "id": ra["id"], "tag": ra.get("tag", ""), "expect": ra.get("expect", ""),
                "run_a": va, "run_b": vb,
                "sql_a": ra.get("sql", ""), "sql_b": rb.get("sql", ""),
            })

    return {
        "noise_band": len(flips),
        "n_questions": len(run_a),
        "n_answerable": sum(1 for r in run_a if r.get("expect") != "unsupported"),
        "n_answerable_flips": sum(1 for f in flips if f["expect"] != "unsupported"),
        "flips": flips,
    }


def pair_compare(recs_a: list[dict], recs_b: list[dict], band: int,
                 name_a: str, name_b: str) -> dict:
    """两臂逐题配对：McNemar + 噪声带对照。

    只有可答题进统计（与 `summarize()` 的分母口径一致）；
    「答不了」的 4 题单独报翻转，不进 EX 分母。

    题目对不齐时抛异常，理由同 `compare_runs()`。
    """
    by_a = {r["id"]: r for r in recs_a if r.get("expect") != "unsupported"}
    by_b = {r["id"]: r for r in recs_b if r.get("expect") != "unsupported"}
    if set(by_a) != set(by_b):
        only = sorted(set(by_a) ^ set(by_b))
        raise ValueError(f"两臂的题目对不齐，差异题号：{only}（某一段的题在另一段里缺失，"
                         f"这不是模型答错，是数据不全）")

    only_a = only_b = both = neither = 0
    for qid in by_a:
        a, b = bool(by_a[qid].get("strict")), bool(by_b[qid].get("strict"))
        if a and not b:
            only_a += 1
        elif b and not a:
            only_b += 1
        elif a and b:
            both += 1
        else:
            neither += 1

    p = mcnemar_exact_p(only_a, only_b)
    sentence = describe_difference(name_a, name_b, only_a, only_b, p, band)

    un_a = {r["id"]: r for r in recs_a if r.get("expect") == "unsupported"}
    un_b = {r["id"]: r for r in recs_b if r.get("expect") == "unsupported"}
    unsup_flips = [qid for qid in sorted(set(un_a) & set(un_b))
                   if (un_a[qid].get("status") == "unsupported")
                   != (un_b[qid].get("status") == "unsupported")]

    return {
        "name_a": name_a, "name_b": name_b,
        "n_paired": len(by_a),
        "n_only_a": only_a, "n_only_b": only_b, "n_both": both, "n_neither": neither,
        "delta": only_a - only_b,
        "p": p, "noise_band": band,
        "beyond_noise": beyond_noise(only_a - only_b, band),
        "sentence": sentence,
        "banned_words": check_wording(sentence),
        "unsupported_flips": unsup_flips,
    }


# ---------------------------------------------------------------- 汇总
def summarize(records: list[dict]) -> dict:
    """把逐题记录汇总成一份能直接进报告的统计。

    `records` 每条至少要有 `status`（`ok`/`blocked`/`failed`/`unsupported`）、
    可答题时还要有 `strict` 和 `tolerant` 两个布尔。

    **分母口径**：可答题才算分母，`unsupported`（模型说答不了）单独统计。
    把两类混在一起的话，「模型明确知道超纲」会被算成失败 ——
    而它其实是一个有效结论，v3 提示词专门引导模型这么做。
    """
    answerable = [r for r in records if r.get("expect") != "unsupported"]
    unsupported = [r for r in records if r.get("expect") == "unsupported"]

    n = len(answerable)
    n_strict = sum(1 for r in answerable if r.get("strict"))
    n_tol = sum(1 for r in answerable if r.get("tolerant"))
    by_status = Counter(r["status"] for r in answerable)

    return {
        "n_answerable": n,
        "n_unsupported_q": len(unsupported),
        "n_strict": n_strict,
        "n_tolerant": n_tol,
        "ex_strict": (n_strict / n) if n else 0.0,
        "ex_tolerant": (n_tol / n) if n else 0.0,
        "by_status": dict(by_status),
        "unsupported_correct": sum(1 for r in unsupported if r.get("status") == "unsupported"),
        "unsupported_total": len(unsupported),
        "calls": sum(r.get("calls", 0) for r in records),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in records),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in records),
    }


def strict_cells(rec: dict) -> tuple[str, str]:
    """逐题表里「严格 / 容忍」两格该显示的字。

    ## 为什么「不可答」的题写 `—` 而不是「否」

    实测踩过：模型在一道本来答不了的题上老实说了答不了（判对了），
    而报告里那两列写着「否 / 否」—— 一道**答对**的题看起来像答错了。
    同一种坑见过一次（`DECISIONS.md` D-39：一个错的期望栏配一个 ✅）。

    `—` 说的是「这一格对它不适用」：不可答题没有参考行集可比，
    它的判据是「有没有老实说答不了」，那份统计在汇总里单列
    （`summarize()` 的 `unsupported_correct`）。

    这个函数放在这里而不是放在 `experiments/09_*.py` 里，是因为
    **以数字开头的模块没法被 import** —— 写在脚本里就只能靠跑脚本来验，
    而这条恰恰是「排版错一个字就误导读者」的那种逻辑，它该有单元测试。
    """
    if rec.get("expect") == "unsupported":
        return "—", "—"
    return (("是" if rec.get("strict") else "否"),
            ("是" if rec.get("tolerant") else "否"))
