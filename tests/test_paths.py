"""路径收敛的测试。

重点不是「函数返回了某个字符串」，而是两条**可移植性**承诺：

1. 默认值**跟着仓库走**，不跟着机器走。早先的写法把数据目录的绝对路径硬编码进源码，
   别人 clone 下来必然报「文件不存在」，而且错误信息指向一个和他毫无关系的路径。
2. 换数据目录**只改环境变量，不改代码**。这条如果只在文档里写着而没有测试，
   下一个人加新路径时就会又去拼一个绝对路径。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server import paths


def test_default_root_is_inside_repo():
    """默认数据根必须落在仓库内 —— 这是「clone 下来就能跑」的前提。"""
    p = paths.data_root()
    assert p == paths.ROOT / "data"
    assert paths.ROOT in p.parents or p.parent == paths.ROOT


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.data_root() == tmp_path
    assert paths.chinook_db() == tmp_path / "chinook" / "chinook.db"
    assert paths.kb_db() == tmp_path / "kb" / "kb.db"


def test_env_is_read_per_call_not_at_import(monkeypatch, tmp_path):
    """环境变量在**每次调用**时读取。

    如果写成模块级常量（`DATA = Path(os.environ.get(...))`），import 之后就再也改不动了，
    测试想指向临时目录会很别扭，最后往往演变成「测试里 monkeypatch 一个常量」这种更脆的写法。
    """
    before = paths.data_root()
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.data_root() == tmp_path
    monkeypatch.delenv(paths.ENV_DATA_ROOT)
    assert paths.data_root() == before


def test_describe_shape(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    d = paths.describe()
    assert set(d) >= {"data_root", "source", "exists", "sources", "build_products", "ROOT"}
    assert d["exists"] is True
    assert all(v is False for v in d["sources"].values())     # 空目录里什么都没有
    assert paths.ENV_DATA_ROOT in d["source"]                  # 要能看出值是从哪来的


def test_main_exit_codes(monkeypatch, tmp_path, capsys):
    # 2 = 数据根不存在
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path / "does-not-exist"))
    assert paths.main() == 2
    out = capsys.readouterr().out
    assert paths.ENV_DATA_ROOT in out          # 引导里要给出设置环境变量的写法

    # 1 = 数据根在，但缺源文件
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.main() == 1
    assert "缺" in capsys.readouterr().out

    # 0 = 源文件齐全
    (tmp_path / "chinook").mkdir()
    (tmp_path / "chinook" / "Chinook_Sqlite.sql").write_text("-- x", encoding="utf-8")
    (tmp_path / "kb" / "raw").mkdir(parents=True)
    (tmp_path / "kb" / "manifest.json").write_text("[]", encoding="utf-8")
    (tmp_path / "kb" / "raw" / "a.txt").write_text("x", encoding="utf-8")
    assert paths.main() == 0


def test_build_products_absence_is_not_an_error(monkeypatch, tmp_path, capsys):
    """构建产物不在 == 「还没构建」，不是错误。

    把 `.db` 的缺失当成环境错误，会让 `clone && pytest` 直接红 —— 而它们本来
    就是该由构建脚本生成、不该入库的东西。
    """
    (tmp_path / "chinook").mkdir()
    (tmp_path / "chinook" / "Chinook_Sqlite.sql").write_text("-- x", encoding="utf-8")
    (tmp_path / "kb" / "raw").mkdir(parents=True)
    (tmp_path / "kb" / "manifest.json").write_text("[]", encoding="utf-8")
    (tmp_path / "kb" / "raw" / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.main() == 0
    assert not (tmp_path / "chinook" / "chinook.db").exists()


def test_relative_display_hides_the_authors_machine():
    """★ 打印用的相对路径 —— 这不是美化，是**防污染**。

    stdout 会被 `19_verify.py` 原样引用进 `reports/verify.md`。脚本打一行
    盘符开头的绝对路径（这里连示例都不写：写了它自己就会被同一条检查扫到），
    报告就成了那个路径的载体，下一轮 S7 在自己的报告里命中它（实测踩过）。
    所以落盘用绝对路径、**打印用相对路径**。
    """
    got = paths.rel(paths.reports_dir() / "x.md")
    assert got == "reports/x.md"
    assert ":" not in got and "\\" not in got


def test_a_path_outside_the_repo_is_left_alone(monkeypatch, tmp_path):
    """数据根被环境变量指到仓库外时无法相对化 —— 原样返回，不抛异常。

    那种路径是**配置出来的**，不是作者机器的目录结构；打印它没有污染问题，
    而拿它去 `relative_to()` 会抛 `ValueError`（脚本会在打印那一行崩掉）。
    """
    monkeypatch.setenv(paths.ENV_DATA_ROOT, str(tmp_path))
    assert paths.rel(paths.chinook_db()) == str(tmp_path / "chinook" / "chinook.db")


# ---------------------------------------------------------------- 仓库地址
#
# 地址原来**写死在两个脚本里**，所以「解析不出来」「两处说法不一致」这些状态
# 根本不存在，也就没人测。搬进这个文件之后它们全成了真实状态，而每一种
# 误判的代价都不一样：把「解析不出来」判成「干净」会放走真问题，
# 判成「不一致」会让人去查一个不存在的事故。所以逐条钉住。

def _only_env_left(monkeypatch):
    """把两处**本机状态**清空（`origin` 与仓外留档），只留环境变量那条路。"""
    monkeypatch.delenv(paths.ENV_REPO_URL, raising=False)
    monkeypatch.setattr(paths, "_slug_from_git", lambda: "")
    monkeypatch.setattr(paths, "maintainer_constants", lambda: ({}, "（测试里钉的）"))


@pytest.mark.parametrize("written,expect", [
    ("https://github.com/o/r", "o/r"),
    ("https://github.com/o/r.git", "o/r"),
    ("git@github.com:o/r.git", "o/r"),
    ("ssh://git@github.com/o/r.git", "o/r"),
    ("github.com/o/r", "o/r"),
    ("o/r", "o/r"),
    ("https://github.com/o/r?tab=readme", "o/r"),
])
def test_slug_forms_are_all_understood(monkeypatch, written, expect):
    """`git remote get-url` 吐出来的形态不止一种，人写进环境变量的也不止一种。

    只认 `https://…` 的话，一个用 ssh 克隆的维护者会把地址判成「解析不出来」，
    而那看起来和「地址真的没了」一模一样 —— 于是他开始查一个不存在的事故。
    """
    _only_env_left(monkeypatch)
    monkeypatch.setenv(paths.ENV_REPO_URL, written)
    assert paths.repo_slug() == (expect, "")


@pytest.mark.parametrize("written", [
    "https://gitlab.com/o/r",              # 不认得的托管站点
    "https://github.com/o",                # 只有一段
    "https://github.com/o/r/tree/main",    # 深链接，不是仓库地址
    "https://example.com/a/b",
    "",
    "   ",
])
def test_slug_rejects_what_it_cannot_recognise(monkeypatch, written):
    _only_env_left(monkeypatch)
    monkeypatch.setenv(paths.ENV_REPO_URL, written)
    slug, why = paths.repo_slug()
    assert slug is None and why


def test_the_raw_value_is_never_echoed_back(monkeypatch):
    """★ 认不出来时**不回显原值**。

    那个值可能来自环境变量，也就是可能带着作者机器的目录 —— 而原因文本会被
    写进报告，报告又要进仓库（本仓有一条检查专门盯这个，S7）。
    """
    _only_env_left(monkeypatch)
    # ★ 样本**攒出来，不写成字面量**：这个形状（盘符 + 用户目录）正是本仓那条
    #   「不许出现作者机器的绝对路径」的检查（`ABS_PATH_PATTERN`）要拦的东西，
    #   连测试里的样本也不例外 —— 写成字面量的话，这条检查会**指着我这一行**报红。
    #   攒出来之后源码里没有那个形状，而被测的字符串一个字节没变。
    sample = "/".join(["C:", "Users", "someone", "on", "their", "machine", "repo"])
    monkeypatch.setenv(paths.ENV_REPO_URL, sample)
    slug, why = paths.repo_slug()
    assert slug is None
    assert "someone" not in why and "Users" not in why


def test_pin_and_origin_must_agree(monkeypatch):
    """★ 两处本机状态不一致 → 判「切换做了一半」，**不随便挑一个**。

    迁移那一步要同时改 `origin` 与仓外留档。漏改一个的表现是「检查照跑、
    只是查的是另一个仓库」—— 那种绿比红危险得多，所以这里宁可判「没查」。
    """
    monkeypatch.delenv(paths.ENV_REPO_URL, raising=False)
    monkeypatch.setattr(paths, "_slug_from_git", lambda: "owner/old-name")
    monkeypatch.setattr(paths, "maintainer_constants",
                        lambda: ({"REPO_URL": "https://github.com/owner/new-name"}, ""))
    slug, why = paths.repo_slug()
    assert slug is None and "不一致" in why
    # 两处一致就照常返回（不能因为加了守卫就永远解析不出来）
    monkeypatch.setattr(paths, "_slug_from_git", lambda: "owner/new-name")
    assert paths.repo_slug() == ("owner/new-name", "")


def test_env_wins_over_local_state(monkeypatch):
    """环境变量是**人为指定的覆盖**，说了就以它为准。

    测试靠这条把地址钉成确定值 —— 不然用例会跟着本机的 `origin` 走：
    换一次远端地址，结果就跟着变，而那是**被测量的东西在动**，不是判据在动。
    """
    monkeypatch.setattr(paths, "_slug_from_git", lambda: "owner/origin-name")
    monkeypatch.setattr(paths, "maintainer_constants",
                        lambda: ({"REPO_URL": "https://github.com/owner/pin-name"}, ""))
    monkeypatch.setenv(paths.ENV_REPO_URL, "owner/env-name")
    assert paths.repo_slug() == ("owner/env-name", "")


def test_no_address_anywhere_is_pending_never_a_guess(monkeypatch):
    """★ 三处都没有 → `None` + 原因，**不猜**。

    本仓教义是不猜远端地址。写一个兜底常量的话，换地址时它会静静地指向一个
    早就不是本仓的地方，而所有检查照样绿 —— 这正是不许有兜底常量的理由。
    """
    _only_env_left(monkeypatch)
    slug, why = paths.repo_slug()
    assert slug is None and why
    assert paths.repo_web_url() == (None, why)
    assert paths.repo_api_commits_url() == (None, why)
    assert paths.repo_raw_url("a/b.py") == (None, why)
    # 地址一有，三个派生地址就都对得上
    monkeypatch.setenv(paths.ENV_REPO_URL, "o/r")
    assert paths.repo_web_url() == ("https://github.com/o/r", "")
    assert paths.repo_raw_url("a/b.py") == (
        "https://raw.githubusercontent.com/o/r/main/a/b.py", "")
    assert paths.repo_api_commits_url("main") == (
        "https://api.github.com/repos/o/r/commits/main", "")


def test_maintainer_file_is_read_as_data_and_never_executed(monkeypatch, tmp_path):
    """★ 仓外留档只做 `ast` 解析，**不 `exec`** —— 与仓外词表同一套约定。

    顺带钉住：非字符串、算不出来的赋值被**逐条忽略**，而不是让整份文件读不出来
    （留档里多写一行注释性表达式不该让维护者本机的检查全瞎）。
    """
    root = tmp_path / "a" / "repo"
    root.mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", root)
    (tmp_path / "a" / paths.MAINTAINER_FILENAME).write_text(
        "A = 'x'\nB = 1\nC = compute()\nD = 'y'\n", encoding="utf-8")
    consts, why = paths.maintainer_constants()
    assert why == "" and consts == {"A": "x", "D": "y"}


def test_pre_fix_commit_only_comes_from_outside_the_repo(monkeypatch, tmp_path):
    """★ 「修复前那一版」的编号**只能**从仓外读，读不到就是「没有依据」。

    写进仓库的话它就是一把钥匙：七位缩写在托管站点上照样能解析回旧对象，
    所以公开文档里只留标签，编号住在仓外。
    """
    root = tmp_path / "a" / "repo"
    root.mkdir(parents=True)
    monkeypatch.setattr(paths, "ROOT", root)
    monkeypatch.delenv(paths.ENV_REPO_URL, raising=False)
    assert paths.pre_fix_commit() == (None, paths.MAINTAINER_PENDING)
    assert paths.pre_fix_label() == (None, paths.MAINTAINER_PENDING)

    (tmp_path / "a" / paths.MAINTAINER_FILENAME).write_text(
        "PRE_FIX_COMMIT = '那一版'\nPRE_FIX_LABEL = '旧编号·F'\n", encoding="utf-8")
    assert paths.pre_fix_commit() == ("那一版", "")
    assert paths.pre_fix_label() == ("旧编号·F", "")


def test_maintainer_pending_text_carries_no_path():
    """读不到仓外留档时那句话会被写进报告，所以**不许带路径**（同词表那条）。

    这条看着像吹毛求疵，实际踩过：作者机器的目录写进产物之后，
    下一轮 S7 就在自己的报告里命中它。
    """
    assert ":" not in paths.MAINTAINER_PENDING
    assert "\\" not in paths.MAINTAINER_PENDING
    assert "/" not in paths.MAINTAINER_PENDING
