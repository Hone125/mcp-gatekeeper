"""偏移量往返：`get_passage` 与 `search_passages` 必须用同一套坐标。

## 为什么单独拿一个文件测这个

因为这类 bug **不会报错**。假设构建索引时按「NFC 归一化之后的文本」记偏移，
而读取时从「原始文本」切片，两者差一个字符，那么返回的就是一段
**通顺、合理、但来自别处**的文字。没有异常、没有警告、退出码 0，
只有内容悄悄错了 —— 而这种错误在一个"检索演示"里几乎不可能被肉眼发现。

所以这里不测「大概对不对」，测的是**逐字符相等**，而且用固定随机种子抽 50 组，
让它可复现。
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

import pytest

from mcp_server import kb_tools, paths

SEED = 20260918
SAMPLES_PER_DOC = 50


def _documents(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT doc_id, text FROM documents")}
    finally:
        conn.close()


def test_random_ranges_round_trip(tiny_kb):
    """对每篇文档随机抽 50 组 `(offset, length)`，断言切片逐字符相等。"""
    texts = _documents(tiny_kb)
    rng = random.Random(SEED)
    checked = 0
    for doc_id, text in texts.items():
        for _ in range(SAMPLES_PER_DOC):
            offset = rng.randrange(0, len(text) + 5)          # 故意越过末尾
            length = rng.randrange(1, 300)
            r = kb_tools.get_passage(doc_id, offset, length, db_path=tiny_kb)
            assert r["ok"], r
            assert r["text"] == text[offset:offset + length], (
                f"doc={doc_id} offset={offset} length={length} 的切片不一致")
            assert r["length"] == len(r["text"]), r
            checked += 1
    assert checked == SAMPLES_PER_DOC * len(texts)


def test_search_hits_and_get_passage_agree(tiny_kb):
    """★ 交叉验证：检索说「这段在 [a:b]」，`get_passage` 取 [a:b] 必须给出同一段文字。

    这是两条独立代码路径在同一份坐标上的一致性检查。任何一侧的坐标算错，
    这里都会以「两段文字不相等」的形式暴露出来。
    """
    texts = _documents(tiny_kb)
    r = kb_tools.search_passages("用兵 地形 茶 神農氏 山水", top_k=20, db_path=tiny_kb)
    assert r["ok"] and r["n_hits"] > 0, r

    for h in r["hits"]:
        span = h["char_end"] - h["char_start"]
        g = kb_tools.get_passage(h["doc_id"], h["char_start"], span, db_path=tiny_kb)
        assert g["ok"], g
        assert g["text"] == h["text"], (
            f"检索返回的段落与按同一区间取出的段落不一致：doc={h['doc_id']} "
            f"[{h['char_start']}:{h['char_end']}]")
        assert g["text"] == texts[h["doc_id"]][h["char_start"]:h["char_end"]]


def test_offset_past_the_end_returns_empty_not_an_error(tiny_kb):
    """越过末尾是**合法输入**（调用方可能拿的是旧偏移量），返回空串而不是报错。

    和 `offset='甲'` 那种真·非法输入区别开：前者是范围问题，后者是类型问题。
    """
    r = kb_tools.get_passage("d1", offset=10 ** 6, length=100, db_path=tiny_kb)
    assert r["ok"] is True
    assert r["text"] == ""
    assert r["length"] == 0
    assert r["n_chars"] > 0, "应当仍然报告文档的真实长度，便于调用方自我纠正"


def test_negative_offset_is_clamped_to_zero(tiny_kb):
    texts = _documents(tiny_kb)
    r = kb_tools.get_passage("d1", offset=-999, length=20, db_path=tiny_kb)
    assert r["ok"] and r["offset"] == 0
    assert r["text"] == texts["d1"][:20]


def test_length_is_capped_at_the_maximum(tiny_kb):
    r = kb_tools.get_passage("d1", offset=0, length=10 ** 9, db_path=tiny_kb)
    assert r["ok"]
    assert r["length"] <= kb_tools.MAX_PASSAGE_LEN


def test_chunk_spans_are_within_bounds_and_ordered():
    """分块函数的纯函数检查：偏移量递增、不重叠、在范围内。"""
    text = "\n".join(f"第{i}行" + "字" * 40 for i in range(200))
    spans = kb_tools.chunk_spans(text, target=300)
    assert spans, "应当切出块来"
    assert [s[0] for s in spans] == list(range(len(spans))), "ord 必须是 0..n-1"
    prev_end = 0
    for _ord, cs, ce in spans:
        assert 0 <= cs < ce <= len(text), (cs, ce, len(text))
        assert cs >= prev_end, f"块之间重叠了：{cs} < {prev_end}"
        prev_end = ce
    # 每块都必须是原文的精确切片（这是「偏移量可信」的最直接表述）
    for _ord, cs, ce in spans:
        assert text[cs:ce] in text


def test_chunk_spans_covers_text_once_when_target_is_tiny():
    text = "甲" * 10 + "\n" + "乙" * 10 + "\n" + "丙" * 10
    spans = kb_tools.chunk_spans(text, target=1)
    assert len(spans) == 3
    assert [text[cs:ce] for _o, cs, ce in spans] == ["甲" * 10, "乙" * 10, "丙" * 10]


# ---------------------------------------------------------------- 真实语料（可选）
_REAL = paths.kb_db()


@pytest.mark.skipif(not _REAL.is_file(),
                    reason="仓库的真实索引还没构建（跑 experiments/02_build_kb.py）")
def test_real_corpus_round_trips_too():
    """对真实语料也抽一遍。索引没构建时跳过 —— 测试不该依赖构建产物。"""
    texts = _documents(_REAL)
    assert len(texts) >= 30, f"真实语料只有 {len(texts)} 篇，样本太少"
    rng = random.Random(SEED)
    for doc_id in sorted(texts):
        text = texts[doc_id]
        for _ in range(3):
            offset = rng.randrange(0, len(text))
            length = rng.randrange(1, 900)
            r = kb_tools.get_passage(doc_id, offset, length, db_path=_REAL)
            assert r["ok"], r
            assert r["text"] == text[offset:offset + length], f"doc={doc_id} offset={offset}"
