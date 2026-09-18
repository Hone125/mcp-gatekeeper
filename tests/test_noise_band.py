"""噪声带与配对检验的测试。**纯函数，不联网、不读文件、不需要 LLM。**

## 为什么这两件事必须能被单元测试钉住

噪声带是**所有版本差异的判据**。它一旦量错，报告里每一句「v2 比 v1 好」都跟着错：

- 量大了 → 真实差异被噪声盖住，什么都测不出来（表现为「两版一样」，看起来还很稳健）；
- 量小了 → 抖动被写成差异（表现为「改进了」，看起来很有成果）。

两种错法都不会自己暴露出来。所以 `compare_runs()` 与 `pair_compare()` 是
纯函数，放在 `mcp_server/eval_grading.py` 里，由这个文件逐条钉住。

它们原本写在 `experiments/20_*.py` / `21_*.py` 里 —— 那两个文件名以数字开头，
**没法被 import**，只能靠跑脚本测。挪进库里就是为了这一条。
"""
from __future__ import annotations

import pytest

from mcp_server import eval_grading as eg


def rec(qid, *, strict=False, expect="rows", status="ok", tag="", sql=""):
    return {"id": qid, "expect": expect, "strict": strict, "status": status,
            "tag": tag, "sql": sql}


# ---------------------------------------------------------------- 判定口径
def test_flip_verdict_uses_strict_ex_for_answerable_questions():
    assert eg.flip_verdict(rec("q", strict=True)) is True
    assert eg.flip_verdict(rec("q", strict=False)) is False


def test_flip_verdict_uses_the_unsupported_answer_for_unanswerable_questions():
    """★ 不可答题的判定是「有没有老实说答不了」，不是 `strict`。

    那 4 题的 `strict` 永远是 False（没有参考解可对），拿它当判定的话，
    噪声带里永远看不到这几道题的波动。
    """
    assert eg.flip_verdict(rec("u", expect="unsupported", status="unsupported")) is True
    assert eg.flip_verdict(rec("u", expect="unsupported", status="ok")) is False


# ---------------------------------------------------------------- 噪声带
def test_identical_runs_have_a_zero_band():
    r = eg.compare_runs([rec("q1", strict=True), rec("q2")],
                        [rec("q1", strict=True), rec("q2")])
    assert r["noise_band"] == 0 and r["n_questions"] == 2 and r["flips"] == []


def test_the_band_counts_only_what_actually_flipped():
    a = [rec("q1", strict=True), rec("q2", strict=True), rec("q3")]
    b = [rec("q1", strict=True), rec("q2"), rec("q3")]
    r = eg.compare_runs(a, b)
    assert r["noise_band"] == 1
    assert [f["id"] for f in r["flips"]] == ["q2"]
    assert r["flips"][0]["run_a"] is True and r["flips"][0]["run_b"] is False


def test_the_band_separates_answerable_from_unsupported():
    """总噪声带与「只数可答题」的翻转数分开报，方便与 EX 的分母口径对上。"""
    a = [rec("q1", strict=True), rec("u1", expect="unsupported", status="unsupported")]
    b = [rec("q1"), rec("u1", expect="unsupported", status="ok")]
    r = eg.compare_runs(a, b)
    assert r["noise_band"] == 2, "两道题的判定都翻了"
    assert r["n_answerable_flips"] == 1 and r["n_answerable"] == 1


def test_a_dropped_question_raises_instead_of_inflating_the_band():
    """★ 掉题不是波动。

    某题在一遍里缺失（调用层挂了）时如果按「答错」处理，噪声带会凭空变大，
    之后所有版本差异都会被这个假噪声带掩盖 —— 这正是最坏的那种错：
    它让报告显得更保守、更可信。
    """
    with pytest.raises(ValueError, match="掉题"):
        eg.compare_runs([rec("q1"), rec("q2")], [rec("q1")])


def test_misaligned_question_order_raises():
    with pytest.raises(ValueError, match="对不齐"):
        eg.compare_runs([rec("q1"), rec("q2")], [rec("q2"), rec("q1")])


def test_band_of_an_empty_run_is_zero_not_a_crash():
    r = eg.compare_runs([], [])
    assert r["noise_band"] == 0 and r["n_questions"] == 0


# ---------------------------------------------------------------- 配对
def test_pair_compare_counts_only_the_disagreements():
    """两臂都对的、都错的**不进**不一致对数 —— 它们说明不了谁更好。"""
    a = [rec("q1", strict=True), rec("q2"), rec("q3", strict=True), rec("q4")]
    b = [rec("q1", strict=True), rec("q2", strict=True), rec("q3"), rec("q4")]
    r = eg.pair_compare(a, b, band=0, name_a="v1", name_b="v2")
    assert r["n_only_a"] == 1 and r["n_only_b"] == 1
    assert r["n_both"] == 1 and r["n_neither"] == 1
    assert r["delta"] == 0 and r["n_paired"] == 4


def test_pair_compare_keeps_unsupported_out_of_the_denominator():
    a = [rec("q1", strict=True), rec("u1", expect="unsupported", status="unsupported")]
    b = [rec("q1", strict=True), rec("u1", expect="unsupported", status="ok")]
    r = eg.pair_compare(a, b, band=0, name_a="v1", name_b="v2")
    assert r["n_paired"] == 1, "不可答题不该进 EX 分母"
    assert r["unsupported_flips"] == ["u1"], "但它翻转了要单独报出来"


def test_pair_compare_honours_the_strict_greater_than_rule():
    """★ 净差**等于**噪声带时不算超出。

    这是 `beyond_noise()` 与报告措辞之间的接缝，单独测一次。
    """
    a = [rec(f"q{i}", strict=True) for i in range(5)]
    b = [rec(f"q{i}", strict=(i < 2)) for i in range(5)]
    r_equal = eg.pair_compare(a, b, band=3, name_a="v1", name_b="v2")
    assert r_equal["delta"] == 3 and r_equal["beyond_noise"] is False
    r_beyond = eg.pair_compare(a, b, band=2, name_a="v1", name_b="v2")
    assert r_beyond["delta"] == 3 and r_beyond["beyond_noise"] is True


def test_pair_compare_never_emits_a_banned_word():
    """措辞纪律的机械检查 —— 包括净差很大、p 很小的情况。"""
    a = [rec(f"q{i}", strict=True) for i in range(20)]
    b = [rec(f"q{i}", strict=(i >= 19)) for i in range(20)]
    r = eg.pair_compare(a, b, band=0, name_a="v1", name_b="v2")
    assert r["banned_words"] == [], r["sentence"]


def test_pair_compare_raises_when_the_two_arms_do_not_line_up():
    with pytest.raises(ValueError, match="对不齐"):
        eg.pair_compare([rec("q1")], [rec("q2")], band=0, name_a="v1", name_b="v2")
