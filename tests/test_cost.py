"""用量账本的测试。**不联网、不调模型。**

这个文件守的是两件事：

1. **记账是旁路，不能反过来影响主流程。** 账本写不进去（磁盘满、权限不对、
   路径被占）时，`record()` 必须安静地放过 —— 服务端正在回答用户问题，
   记账失败不该让回答失败。这条要**故意把路径弄坏**来测，不能只读代码。
2. **金额不许凭空冒出来。** 缺单价、缺出处时 `money()` 返回 `None`，
   而不是 0，也不用别家单价兜底。「没算出来」和「不要钱」是两回事。

★ 每个用例都把账本路径指到 `tmp_path`：`cost.ledger_path()` 落在
`reports/` 下，真往那儿写会把仓库的成本报告污染成测试数据。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mcp_server import cost


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """把账本指到临时目录，返回那个路径。"""
    p = tmp_path / "usage_ledger.jsonl"
    monkeypatch.setattr(cost, "ledger_path", lambda: p)
    return p


# ---------------------------------------------------------------- 记账
def test_record_then_read_round_trip(ledger):
    cost.record("text2sql", "some-model", {"prompt_tokens": 120, "completion_tokens": 30})
    rows = cost.read_ledger()
    assert len(rows) == 1
    assert rows[0]["source"] == "text2sql"
    assert rows[0]["model"] == "some-model"
    assert rows[0]["prompt_tokens"] == 120 and rows[0]["completion_tokens"] == 30
    assert rows[0]["ts"], "没有时间戳的账本行没法对账"


def test_record_accepts_an_object_with_attributes_not_only_a_dict(ledger):
    """模型 SDK 回的是一个对象，不是一个 dict。两种都要吃得下。"""
    cost.record("kb", "m", SimpleNamespace(prompt_tokens=7, completion_tokens=3))
    r = cost.read_ledger()[0]
    assert (r["prompt_tokens"], r["completion_tokens"]) == (7, 3)


def test_record_skips_a_usage_without_any_token_count(ledger):
    """★ 没有 token 信息的调用**不记**。

    记一条 0/0 的行会让成本报告里出现一次「调用但没花钱」的假象；
    宁可少一行，不要一行假的。
    """
    cost.record("text2sql", "m", None)
    cost.record("text2sql", "m", {})
    cost.record("text2sql", "m", object())
    assert cost.read_ledger() == []


def test_record_never_raises_when_the_ledger_cannot_be_written(tmp_path, monkeypatch):
    """★ 负控：把账本路径弄成一个**目录**，open(..., "a") 必然失败。

    这是「旁路记账绝不抛异常」这条规则的机械验证。只读代码看不出这条，
    因为代码里那个 `except Exception: pass` 完全可以被后来的人顺手删掉。
    """
    d = tmp_path / "not_a_file"
    d.mkdir()
    monkeypatch.setattr(cost, "ledger_path", lambda: d)

    cost.record("text2sql", "m", {"prompt_tokens": 1, "completion_tokens": 1})  # 不该抛


def test_record_keeps_going_after_a_failed_write(tmp_path, monkeypatch):
    """第一次写失败之后，第二次（路径修好了）还要能写进去 —— 不许留下坏状态。"""
    good = tmp_path / "ok.jsonl"
    bad = tmp_path / "bad"
    bad.mkdir()
    monkeypatch.setattr(cost, "ledger_path", lambda: bad)
    cost.record("s", "m", {"prompt_tokens": 1, "completion_tokens": 1})
    monkeypatch.setattr(cost, "ledger_path", lambda: good)
    cost.record("s", "m", {"prompt_tokens": 2, "completion_tokens": 2})
    assert len(cost.read_ledger()) == 1


# ---------------------------------------------------------------- 读账本
def test_missing_ledger_is_an_empty_list_not_an_error(ledger):
    """账本不存在是**正常状态**（还没跑过任何评测），不是错误。"""
    assert cost.read_ledger() == []


def test_a_corrupt_line_is_skipped_and_the_rest_survives(ledger):
    """★ 一行坏数据不该让整个报告生成不出来。

    被 kill 掉的进程会留下半行 JSON。那时报告的价值恰恰最大 ——
    已经花掉的钱必须算得出来。
    """
    ledger.write_text(
        json.dumps({"source": "a", "model": "m", "prompt_tokens": 1,
                    "completion_tokens": 1}) + "\n"
        + '{"source": "b", "model": "m", "prompt_tok' + "\n"
        + "\n"
        + json.dumps({"source": "c", "model": "m", "prompt_tokens": 3,
                      "completion_tokens": 3}) + "\n",
        encoding="utf-8")
    rows = cost.read_ledger()
    assert [r["source"] for r in rows] == ["a", "c"]


def test_summary_aggregates_by_source_and_model(ledger):
    for src, model, i, o in (("text2sql", "m1", 10, 1), ("text2sql", "m1", 20, 2),
                             ("colloquial", "m1", 5, 5), ("text2sql", "m2", 1, 1)):
        cost.record(src, model, {"prompt_tokens": i, "completion_tokens": o})
    rows = cost.ledger_summary()
    assert rows[0] == {"src": "text2sql", "model": "m1", "calls": 2,
                       "tok_in": 30, "tok_out": 3}, "调用次数最多的排最前"
    by = {(r["src"], r["model"]): r for r in rows}
    assert by[("colloquial", "m1")]["calls"] == 1
    assert by[("text2sql", "m2")]["tok_in"] == 1


def test_retries_are_counted_as_separate_calls(ledger):
    """★ 重写重试各记一行。

    把重试合并成一次调用会低报成本 —— 而重试正是这套状态机最花钱的地方，
    低报的恰好是它最该被看见的那部分。
    """
    for _ in range(3):
        cost.record("text2sql", "m", {"prompt_tokens": 100, "completion_tokens": 10})
    s = cost.ledger_summary()[0]
    assert s["calls"] == 3 and s["tok_in"] == 300


# ---------------------------------------------------------------- 金额
def test_money_is_none_without_a_price_entry():
    amount, why = cost.money("unknown-model", 1_000_000, 1_000_000, {})
    assert amount is None, "★ 缺单价时**绝不能**返回 0 —— 那看起来像免费"
    assert "无单价条目" in why


def test_money_requires_both_directions_of_the_price():
    prices = {"m": {"in": 1.0, "out": None, "source": "u", "as_of": "2026-01-01"}}
    assert cost.money("m", 1, 1, prices)[0] is None


def test_money_requires_a_citable_source_and_date():
    """★ 有数字但没出处，等于一个没法核对的断言。"""
    for row in ({"in": 1.0, "out": 2.0, "source": "", "as_of": "2026-01-01"},
                {"in": 1.0, "out": 2.0, "source": "https://example.com", "as_of": ""},
                {"in": 1.0, "out": 2.0}):
        amount, why = cost.money("m", 1_000, 1_000, {"m": row})
        assert amount is None, row
        assert "出处" in why, why


def test_money_computes_the_hand_checked_amount():
    """每百万输入 2 元、输出 3 元：100 万输入 + 50 万输出 = 2 + 1.5 = 3.5 元。"""
    prices = {"m": {"in": 2.0, "out": 3.0, "currency": "CNY",
                    "source": "https://example.com/pricing", "as_of": "2026-01-01"}}
    amount, why = cost.money("m", 1_000_000, 500_000, prices)
    assert amount == pytest.approx(3.5)
    assert "2026-01-01" in why, "口径栏要能看出这个金额是按哪天的价目算的"


def test_money_of_zero_tokens_is_zero_not_none():
    """零 token 是**算出来的 0**，和「算不出来」要区分开。"""
    prices = {"m": {"in": 2.0, "out": 3.0, "source": "u", "as_of": "2026-01-01"}}
    amount, _ = cost.money("m", 0, 0, prices)
    assert amount == 0.0


# ---------------------------------------------------------------- 单价表
def test_price_override_without_a_source_is_rejected(tmp_path, monkeypatch):
    """★ 没出处的单价直接报错，不许「先用了再说」。

    这是 D-9 的机械保障：一旦允许缺出处的单价溜进来，报告里就会出现
    一个没人能核对的金额，而且看不出来它是怎么来的。
    """
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"m": {"in": 1.0, "out": 2.0}}), encoding="utf-8")
    monkeypatch.setenv(cost.ENV_PRICES, str(p))
    with pytest.raises(cost.PriceError):
        cost.load_prices()


def test_price_override_with_a_source_is_loaded(tmp_path, monkeypatch):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"m": {"in": 1.0, "out": 2.0,
                                   "source": "https://example.com", "as_of": "2026-01-01"}}),
                 encoding="utf-8")
    monkeypatch.setenv(cost.ENV_PRICES, str(p))
    assert cost.load_prices()["m"]["in"] == 1.0


def test_no_builtin_prices_ship_with_the_repo():
    """★ 内置单价表**故意是空的**。

    价目随时会变，把某天的数字固化进源码比留空更容易误导人 ——
    留空会让人去查，固化会让人直接引用。
    这条测试是为了防止后来的人「顺手补上」一组方便的数字。
    """
    assert cost.BUILTIN_PRICES == {}
