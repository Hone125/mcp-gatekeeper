"""评分口径的测试。

这个文件守的是**报告里的数字是什么意思**。口径写松一格，EX 就会凭空高几个点，
而且没有任何现象能让人看出来 —— 所以每条口径都要有一个具体例子钉住，
包括「哪些差异**算对**」和「哪些差异**不算对**」两个方向。

真实题集的参考 SQL 实跑核验在 `experiments/09_text2sql_eval.py --check-questions`，
这里只测纯逻辑，不碰数据库。
"""
from __future__ import annotations

import pytest

from mcp_server import eval_grading as eg


# ---------------------------------------------------------------- 归一
def test_integers_and_floats_are_the_same_number():
    """`COUNT(*)` 回 25、`AVG()` 回 25.0 —— 都是 25，不该判成不同。"""
    assert eg.compare([[25]], ["n"], [[25.0]], ["c"])["strict"] is True


def test_none_is_not_zero_and_not_empty_string():
    """★ 空值必须和 0、空串区分开。

    混同的话，「作曲家为空的有多少首」这类题会给出一个错的通过率：
    模型答 0、参考答 977，两个都是「一个值」，会被判成一致。
    """
    assert eg.compare([[None]], ["a"], [[0]], ["b"])["strict"] is False
    assert eg.compare([[None]], ["a"], [[""]], ["b"])["strict"] is False


def test_float_tolerance_is_in_the_sixth_decimal():
    """★ 浮点带容差，但要小到不会把真实错误放过去。

    `AVG(Total)` 与 `SUM(Total)/COUNT(*)` 末位会差一点，那是浮点，不是错。
    """
    assert eg.compare([[1.2345678]], ["a"], [[1.2345679]], ["b"])["strict"] is True
    assert eg.compare([[1.234567]], ["a"], [[1.234568]], ["b"])["strict"] is False


def test_bytes_and_str_are_the_same_value():
    """SQLite 有些驱动回 bytes、有些回 str，不该因此判错。"""
    assert eg.compare([[b"AC/DC"]], ["a"], [["AC/DC"]], ["b"])["strict"] is True


# ---------------------------------------------------------------- 两条口径
def test_column_order_is_the_difference_between_the_two_verdicts():
    """★ 这两条口径的差别**只**在这里 —— 报告要分开报的原因。"""
    r = eg.compare([["John", "Doe"]], ["a", "b"], [["Doe", "John"]], ["x", "y"])
    assert r["strict"] is False, "列序不同，严格口径应当判否"
    assert r["tolerant"] is True, "列序不同但内容一致，宽松口径应当判是"
    assert "列序" in r["why"]


def test_row_order_does_not_matter_in_either_verdict():
    """★ 行序不参与评分。

    参考 SQL 里的 `ORDER BY` 是为了让「前 5 名」在并列处有确定答案，
    不是要求模型也按同样顺序输出。模型返回同样内容、顺序不同，是对的。
    """
    r = eg.compare([["b"], ["a"]], ["n"], [["a"], ["b"]], ["n"])
    assert r["strict"] is True and r["tolerant"] is True


def test_column_names_do_not_matter():
    """`COUNT(*)` 和 `COUNT(*) AS n` 是同一个答案；评分不考别名起得好不好。"""
    assert eg.compare([[347]], ["COUNT(*)"], [[347]], ["n"])["strict"] is True


def test_a_too_wide_projection_fails_both_verdicts():
    """★ 这是 v1→v2 提示词要治的那个毛病：多选列。

    `SELECT *` 和「只选需要的两列」列数不同，两条口径都不算对 ——
    投影过度不该被宽松口径放过，否则那个提示词改进就测不出来了。
    """
    ref = [["AC/DC", 2]]
    model = [["AC/DC", 2, "extra", "more", "even_more"]]
    r = eg.compare(model, ["a", "b", "c", "d", "e"], ref, ["x", "y"])
    assert r["strict"] is False and r["tolerant"] is False
    assert "列数不同" in r["why"]


def test_duplicate_rows_are_a_multiset_not_a_set():
    """★ 多重集，不是集合。

    集合的话，模型返回 `[A, A, B]` 而参考是 `[A, B, B]` 会被判成一致 ——
    group by 写错、join 产生笛卡尔积这类错误正好长这样。
    """
    assert eg.compare([["A"], ["A"], ["B"]], ["n"],
                      [["A"], ["B"], ["B"]], ["n"])["strict"] is False
    assert eg.compare([["A"], ["A"], ["B"]], ["n"],
                      [["A"], ["A"], ["B"]], ["n"])["strict"] is True


def test_wider_than_the_permutation_cap_degrades_loudly():
    """列数超过上限时不枚举重排，**并且要说明**自己退化了。

    悄悄退化会让报告里的「宽松口径」名不副实 —— 读的人以为列序被容忍了，
    实际上没有。
    """
    n = eg.MAX_PERM_COLS + 1
    model = [list(range(n))]
    ref = [list(reversed(range(n)))]
    r = eg.compare(model, list("abcdefg")[:n], ref, list("uvwxyz1")[:n])
    assert r["tolerant"] == r["strict"], "超上限时宽松口径应当退化成严格口径"


def test_why_distinguishes_row_count_from_row_content():
    """★ `why` 要能区分「行数不对」和「行数对但内容不对」—— 排查方向完全不同。

    行数不对通常是 `WHERE` / `GROUP BY` / `JOIN` 的粒度问题；
    行数对而内容不对通常是取值取错了列或者过滤条件写错。这两种线索不该混成一句。

    （这条测试第一版把两个例子写反了：行数相同、内容不同时，`why` 说的是
    「行内容不一致」而不是「行数不同」。断言写错的是我，不是代码。）
    """
    same_count = eg.compare([["A"], ["B"]], ["n"], [["A"], ["C"]], ["n"])["why"]
    diff_count = eg.compare([["A"]], ["n"], [["A"], ["C"]], ["n"])["why"]
    assert "行内容" in same_count and "行数不同" not in same_count, same_count
    assert "行数不同" in diff_count, diff_count


# ---------------------------------------------------------------- McNemar
def test_mcnemar_matches_a_hand_computed_value():
    """b=1, c=9 是教科书上的例子，精确双尾检验的经典值。

    n=10、k=1：2 * (C(10,0)+C(10,1)) / 2^10 = 2 * 11 / 1024 = 0.021484375
    """
    assert eg.mcnemar_exact_p(1, 9) == pytest.approx(0.021484375)
    assert eg.mcnemar_exact_p(9, 1) == pytest.approx(0.021484375), "应当对称"


def test_mcnemar_is_never_significant_for_small_disagreement():
    """★ 36 题规模下的现实：不一致对数个位数时，p 值不可能小。

    这条是给报告措辞兜底的 —— 它证明了「不许写显著」不是谦虚，是算术。
    """
    for b, c in ((2, 4), (3, 3), (1, 5), (4, 6), (5, 7)):
        assert eg.mcnemar_exact_p(b, c) > 0.05, (b, c)
    # 连 10 比 0 这种「一边倒」都到不了 0.05
    assert eg.mcnemar_exact_p(10, 0) > 0.001
    assert eg.mcnemar_exact_p(10, 0) < 0.01


def test_mcnemar_with_no_disagreement_is_p_one():
    """两臂完全一致时 p = 1.0 —— 「没有差异」不是「有差异」的证据。"""
    assert eg.mcnemar_exact_p(0, 0) == 1.0


def test_beyond_noise_uses_strict_greater_than():
    """差值**等于**噪声带时不算超出 —— 那种波动完全可以用噪声解释。"""
    assert eg.beyond_noise(3, 3) is False
    assert eg.beyond_noise(4, 3) is True
    assert eg.beyond_noise(-4, 3) is True, "方向不影响是否超出"


# ---------------------------------------------------------------- 措辞纪律
@pytest.mark.parametrize("only_a,only_b,p", [
    (2, 1, 0.9), (3, 3, 1.0), (5, 2, 0.45), (0, 0, 1.0), (9, 1, 0.02),
])
def test_describe_difference_never_uses_a_banned_word(only_a, only_b, p):
    """★ 唯一允许描述版本差异的函数，词表是封闭的。

    「显著 / 大幅 / 提升 / 极大」一个都不许出现 —— 包括在 p 值很小的那几档。
    p 小只说明这次观测到的差异不太像噪声，不说明差异大。
    """
    text = eg.describe_difference("v1", "v3", only_a, only_b, p, noise_band=2)
    assert eg.check_wording(text) == [], text


def test_the_wording_check_can_actually_catch_a_banned_word():
    """负控：`check_wording` 不是恒真的。"""
    assert eg.check_wording("v2 显著优于 v1") == ["显著"]
    assert eg.check_wording("准确率大幅提升") == ["大幅", "提升"]


def test_describe_difference_says_which_side_of_the_noise_band_it_is_on():
    """超出噪声带和没超出，措辞必须不同 —— 否则读了等于没读。"""
    inside = eg.describe_difference("v1", "v2", 3, 2, 0.8, noise_band=2)
    outside = eg.describe_difference("v1", "v2", 8, 1, 0.02, noise_band=2)
    assert "未超出噪声带" in inside and "未达可判定水平" in inside
    assert "超出噪声带" in outside


# ---------------------------------------------------------------- 汇总
def test_summarize_keeps_unsupported_out_of_the_denominator():
    """★ 模型答「答不了」是一个有效结论，不该被算成失败。

    混进分母的话，v3 提示词里那段「答不了就明说」的引导会**降低**分数，
    于是评测在惩罚一个正确的行为。
    """
    recs = [
        {"status": "ok", "strict": True, "tolerant": True, "expect": "rows", "calls": 1},
        {"status": "failed", "strict": False, "tolerant": False, "expect": "rows", "calls": 3},
        {"status": "unsupported", "expect": "unsupported", "calls": 1},
        {"status": "unsupported", "expect": "unsupported", "calls": 1},
    ]
    s = eg.summarize(recs)
    assert s["n_answerable"] == 2 and s["n_unsupported_q"] == 2
    assert s["n_strict"] == 1 and s["ex_strict"] == pytest.approx(0.5)
    assert s["unsupported_correct"] == 2 and s["unsupported_total"] == 2
    assert s["calls"] == 6, "调用次数要把说『答不了』的那次也算上 —— 它真的花了钱"


def test_summarize_of_nothing_is_zero_not_a_crash():
    s = eg.summarize([])
    assert s["n_answerable"] == 0 and s["ex_strict"] == 0.0


# ---------------------------------------------------------------- 逐题表的格子
def test_an_unanswerable_question_shows_a_dash_not_a_no():
    """★ 逐题表里，「不可答」两列写 `—` 而不是「否」。

    实测踩过：模型在一道本来答不了的题上**老实说了答不了**（判对了），
    而报告那两列写着「否 / 否」—— 一道答对的题看起来像答错的。
    同一种坑在这个仓库出现过一次（`DECISIONS.md` D-39）。

    注意这条用例查的是**显示出来的字**：判分本身没错（`_mark` 与
    `summarize` 走的是另一条判据），错的是给人看的那一格。
    """
    correct_refusal = {"expect": "unsupported", "status": "unsupported",
                       "strict": False, "tolerant": False}
    assert eg.strict_cells(correct_refusal) == ("—", "—")

    # 反方向：可答题必须照旧显示是/否，否则这次修改就把信息抹掉了
    assert eg.strict_cells({"expect": "rows", "strict": True,
                            "tolerant": True}) == ("是", "是")
    assert eg.strict_cells({"expect": "rows", "strict": False,
                            "tolerant": True}) == ("否", "是")


def test_the_dash_rule_covers_every_unanswerable_question():
    """不是只对「答对了」的那种写 `—`：一道本来答不了、模型却硬答了的题，
    它错在哪里由「说明」那一列讲（`why`），而不是由这两格讲。"""
    hard_answered = {"expect": "unsupported", "status": "ok",
                     "strict": False, "tolerant": False}
    assert eg.strict_cells(hard_answered) == ("—", "—")
