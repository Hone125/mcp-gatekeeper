"""词表装载器：**它读不出来时会怎样**，比它读得出来时怎样更重要。

## 为什么单独一个文件

词表从仓库里搬出去之后（见 `mcp_server/selfcheck.py` 文件头），
「读不到词表」从一个不可能状态变成了**常态** —— 任何人 clone 下来都是这个状态。
于是装载器的每一条失败路径都得有人验：失败时它必须**说不出话**（判 SKIP），
而不是**说没事**（判 PASS）。

这个文件里的用例都**不需要**词表在本机 —— 它们造自己的输入。所以它们
在任何机器上都跑，不受 `MCP_TOOLKIT_WORDLIST` 影响。这一点是刻意的：
一个只在「维护者那台机器上」才跑的检查，等于没有。
"""
from __future__ import annotations

import re

import pytest

from mcp_server import selfcheck as sc


def _write(tmp_path, body: str, name: str = "wl.py"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_a_good_wordlist_loads_all_three_tables(tmp_path):
    """正控：三个键齐、都是字符串列表 → 读得出来，理由为空串。"""
    p = _write(tmp_path, "FORBIDDEN_NAMES = ['a']\n"
                         "MEDICAL_TERMS = ['b']\n"
                         "FORBIDDEN_NUMBERS = ['c']\n")
    table, why = sc.load_wordlist(p)
    assert why == "", why
    assert table == {"FORBIDDEN_NAMES": ["a"], "MEDICAL_TERMS": ["b"],
                     "FORBIDDEN_NUMBERS": ["c"]}


@pytest.mark.parametrize("body,expect", [
    ("MEDICAL_TERMS = ['b']\nFORBIDDEN_NUMBERS = ['c']\n", "缺这些键"),
    ("FORBIDDEN_NAMES = []\nMEDICAL_TERMS = ['b']\nFORBIDDEN_NUMBERS = ['c']\n",
     "缺这些键"),
    ("FORBIDDEN_NAMES = ['a']\nMEDICAL_TERMS = ['b']\nFORBIDDEN_NUMBERS = 3\n",
     "不是字符串列表"),
    ("FORBIDDEN_NAMES = ['a', 1]\nMEDICAL_TERMS = ['b']\n"
     "FORBIDDEN_NUMBERS = ['c']\n", "不是字符串列表"),
    ("FORBIDDEN_NAMES = [x for x in 'ab']\nMEDICAL_TERMS = ['b']\n"
     "FORBIDDEN_NUMBERS = ['c']\n", "不是一个字面量"),
    ("def f(:\n", "不是一个能解析的 Python 文件"),
])
def test_a_broken_wordlist_never_loads_partially(tmp_path, body, expect):
    """★ 六条坏输入，一条都不许「读进来一半」。

    半读进来的后果最坏：三张表里有一张是空的，于是那一类的扫描**永远 0 命中** ——
    报告上写着「通过」，而实际上那一类根本没在查。判「读不出来」比判「读进来两张」
    安全得多，因为前者会让调用方去判 SKIP。
    """
    table, why = sc.load_wordlist(_write(tmp_path, body))
    assert table == {}, f"坏输入居然读出了东西：{table}"
    assert expect in why, f"理由不清楚：{why!r}"


def test_a_missing_file_says_so_without_echoing_the_path(tmp_path):
    """文件不存在 → 理由里**不许出现路径**。

    这条不是洁癖：理由文本会一路进报告，而本仓有一条检查不许产物出现
    作者机器的目录（`ABS_PATH_PATTERN`）。所以理由只说「不存在」。
    """
    p = tmp_path / "nope.py"
    table, why = sc.load_wordlist(p)
    assert table == {}
    assert why and str(tmp_path) not in why, f"理由里带了路径：{why!r}"


def test_the_default_lookup_is_the_sibling_of_the_repository(monkeypatch, tmp_path):
    """没设环境变量时，找的是**仓库同级目录**下的约定文件名。

    ★ 两头都要验，否则这条用例是空的：只验「找不到时返回 PENDING」的话，
    把查找位置改成任何地方（甚至干脆不找）它都照样通过。
    所以这里在**同级目录**放一个文件，看它找不找得到 —— 这就是那个「同级」的定义。
    """
    monkeypatch.delenv(sc.ENV_WORDLIST, raising=False)
    repo = tmp_path / "some-repo"
    repo.mkdir()

    p, why = sc.resolve_wordlist(repo)
    assert p is None and why == sc.WORDLIST_PENDING, (p, why)

    # 同级（父目录）放一个 → 必须找到
    sibling = tmp_path / sc.WORDLIST_FILENAME
    sibling.write_text("FORBIDDEN_NAMES = ['a']\nMEDICAL_TERMS = ['b']\n"
                       "FORBIDDEN_NUMBERS = ['c']\n", encoding="utf-8")
    assert sc.resolve_wordlist(repo) == (sibling, "")

    # 放进**仓库里面**不算 —— 那就是「词表又回仓库了」，正是这次要修掉的事
    inside = repo / sc.WORDLIST_FILENAME
    inside.write_text(sibling.read_text(encoding="utf-8"), encoding="utf-8")
    sibling.unlink()
    assert sc.resolve_wordlist(repo)[0] is None, "词表放进仓库里居然也被认了"


def test_the_env_var_wins_and_a_missing_target_is_loud(monkeypatch, tmp_path):
    """环境变量优先；指向的文件不在 → 理由点名环境变量，但仍不带路径。"""
    monkeypatch.setenv(sc.ENV_WORDLIST, str(tmp_path / "not-here.py"))
    p, why = sc.resolve_wordlist(tmp_path)
    assert p is None
    assert sc.ENV_WORDLIST in why, f"理由没说是环境变量的问题：{why!r}"
    assert str(tmp_path) not in why, f"理由里带了路径：{why!r}"


def test_the_pending_wording_never_leaks_a_path():
    """那句写进报告的话，本身也得过得了「不许出现绝对路径」那条检查。"""
    assert not re.search(sc.ABS_PATH_PATTERN, sc.WORDLIST_PENDING)
    assert sc.WORDLIST_PENDING.strip()


def test_wordlist_entries_in_catches_every_table(monkeypatch):
    """`wordlist_entries_in()` 三类都要认 —— 它是「豁免区干不干净」那条断言的底。"""
    monkeypatch.setattr(sc, "FORBIDDEN_NAMES", ["甲"])
    monkeypatch.setattr(sc, "MEDICAL_TERMS", ["乙"])
    monkeypatch.setattr(sc, "FORBIDDEN_NUMBERS", ["1.5"])
    assert sc.wordlist_entries_in("前缀甲后缀") == ["甲"]
    assert sc.wordlist_entries_in("前缀乙后缀") == ["乙"]
    assert sc.wordlist_entries_in("前缀 1.5 后缀") == ["1.5"]
    assert sc.wordlist_entries_in("干干净净") == []
