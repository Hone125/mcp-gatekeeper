"""仓库卫生的机械检查：把「红线」变成可执行断言，而不是靠自觉。

## 为什么要有这个文件

「不要写进公司名 / 课程名 / 原项目名 / 领域术语 / 某些数字」这类约束，
靠人肉 review 是守不住的 —— 一上午写 800 行代码，谁也不会逐字回读自己写的注释。
它们必须是**机器每次跑测试时都验一遍**的东西，而且失败信息要直接给出**文件名 + 行号**，
这样改起来是十分钟的事，而不是一次考古。

## 口径在 `mcp_server/selfcheck.py`，词表在**仓库外面**

这个文件里**没有**任何词表，也没有扫描循环。判定口径全在 `mcp_server/selfcheck.py`，
理由是有两个消费者：

1. 本文件 —— 每次 `pytest` 断言「命中数为 0」；
2. `experiments/19_verify.py` —— 自检要**报出命中数与命中行**，
   写进 `reports/final_selfcheck.md`。

两边各存一份词表就会漂移：往一处加了「某公司简称」，另一处不知道，
于是「测试全绿」和「自检全绿」可以同时成立而结论不同。

★ **而词表本身既不在这里、也不在 `selfcheck.py` 里** —— 它由环境变量
`MCP_TOOLKIT_WORDLIST` 指向一个仓外的文件。这一层是后加的，理由见
`selfcheck.py` 文件头的「词表住在仓库外面」：词表逐字写着那些词，
它住在仓库里的时候，仓库里唯一有这些字的地方就是它自己。
**读不到词表时，依赖词表的测试判 SKIP，不是 PASS**（`DECISIONS.md` D-44）。

**扫描范围与三处排除的完整理由，写在 `selfcheck.py` 的文件头**，不在这里重复 ——
两处写同一件事，迟早会有一处忘了改。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from mcp_server import selfcheck as sc  # noqa: E402
from mcp_server import verify_kit as vk  # noqa: E402

# 词表、范围、扫描原语全部来自 `mcp_server/selfcheck.py` —— **口径只有一份**。
# 本文件只负责把「命中数必须为 0」写成断言，以及证明这些断言真的会红。
ROOT = sc.ROOT
SKIP_DIRS = sc.SKIP_DIRS
SELF_FILES = sc.SELF_FILES
FORBIDDEN_NAMES = sc.FORBIDDEN_NAMES
MEDICAL_TERMS = sc.MEDICAL_TERMS
FORBIDDEN_NUMBERS = sc.FORBIDDEN_NUMBERS
OVERSTATED = sc.OVERSTATED
SCOPE_ALL = sc.SCOPE_ALL
SCOPE_AUTHORED = sc.SCOPE_AUTHORED
SCOPE_NO_VERBATIM = sc.SCOPE_NO_VERBATIM
SCOPE_READER_PROSE = sc.SCOPE_READER_PROSE
THIRD_PARTY_VERBATIM = sc.THIRD_PARTY_VERBATIM

_grep = sc.grep
_iter_reader_prose = sc.iter_reader_prose
_read = sc.read_text

# 词表不在仓库里（见文件头）。依赖它的断言在词表不可用时**判 SKIP，不是 PASS** ——
# 「没有实测依据」和「实测通过」是两回事（`DECISIONS.md` D-44）。
# 理由文本由 `selfcheck` 提供，只有一份，改口径不必来这边同步。
needs_wordlist = pytest.mark.skipif(not sc.WORDLIST_AVAILABLE,
                                    reason=sc.WORDLIST_PENDING)


def _fmt(hits) -> str:
    return sc.format_hits(hits)


@needs_wordlist
def test_no_identifying_names_anywhere():
    """公司名 / 课程名 / 原项目名 —— **全仓**不得出现，公版正文也算。"""
    hits = _grep(FORBIDDEN_NAMES, SCOPE_ALL)
    assert not hits, f"识别性名称 {len(hits)} 处命中：\n{_fmt(hits)}"


@needs_wordlist
def test_no_medical_terms_in_authored_files():
    """领域术语 —— 只在**自己写的文件**里扫。公版古籍正文不在此列（见文件头说明）。"""
    hits = _grep(MEDICAL_TERMS, SCOPE_AUTHORED)
    assert not hits, f"自己写的文件里出现领域术语 {len(hits)} 处：\n{_fmt(hits)}"


@needs_wordlist
def test_no_forbidden_numbers_outside_verbatim_data():
    """原项目的具体数字。第三方原样数据除外，其余全扫。

    ★ 两类分开看，**两个数都要报出来**：

    - 真命中 → 必须为 0；
    - 已核验的巧合（`sc.VERIFIED_COINCIDENCES`）→ 允许非 0，但断言消息里要列出来。

    分开的理由写在 `sc.split_coincidences()` 的文档字符串里：直接在 `grep()` 里
    滤掉的话，命中数报 0，读的人会以为从来没撞过车 —— 那正是静默。
    """
    hits = _grep(FORBIDDEN_NUMBERS, SCOPE_NO_VERBATIM, word_boundary=True)
    real, coin = sc.split_coincidences(hits)
    assert not real, (
        f"原项目数字 {len(real)} 处真命中（另有 {len(coin)} 处已核验巧合）：\n"
        f"{_fmt(real)}\n"
        "  如果确认是巧合（比如行号恰好是这些数），**不要改数字**："
        "把它按 `mcp_server/selfcheck.py` 的 `VERIFIED_COINCIDENCES` 格式登记，"
        "写明这个值是怎么算出来的（登记要连测试一起改，于是不可能悄悄放宽）。"
    )


@needs_wordlist
def test_every_registered_coincidence_is_declared_and_justified():
    """★ 白名单只许有登记过的那一条，而且每条都要有出处。

    这是「不许悄悄放宽」的机械保证：想加一条，就必须同时改这个数字，
    于是改动会在 diff 里露出来，而不是藏进一次「顺手加个例外」。
    """
    table = sc.VERIFIED_COINCIDENCES
    assert len(table) == 1, (
        f"巧合白名单有 {len(table)} 条，本仓只登记过 1 条。"
        f"新增一条必须是有意识的决定：把这里改成新条目数，并在 `DECISIONS.md` 里写明。")

    for e in table:
        assert e["literal"] in FORBIDDEN_NUMBERS, (
            f"「{e['literal']}」根本不在被禁表里，没有需要豁免的东西 —— "
            f"这类条目只会被用来豁免任意文本。")
        assert len(e["why"].strip()) >= 30, f"「{e['literal']}」的 why 太短，等于没写"
        assert e["paths"], f"「{e['literal']}」没写允许出现在哪些文件里（那等于全仓放行）"
        for p in e["paths"]:
            assert (ROOT / p).exists(), f"白名单指向的文件不存在：{p}"


def test_a_coincidence_is_only_forgiven_in_the_file_that_declared_it():
    """★ 负控：同一个字面量，在**别的**文件里必须照旧算命中。

    没有这一条的话，白名单会退化成「这个数字全仓放行」——
    那正好把要防的事（原项目指标出现在别的产物里）放过去了。
    """
    lit = sc.VERIFIED_COINCIDENCES[0]["literal"]
    allowed = sc.coincidence_paths(lit)
    assert allowed, f"{lit} 没有登记任何允许的文件"

    inside = sc.Hit(sorted(allowed)[0], 1, lit, f"p = {lit}")
    outside = sc.Hit("reports/别的报告.md", 1, lit, f"p = {lit}")
    real, coin = sc.split_coincidences([inside, outside])
    assert coin == [inside], f"登记过的文件里应当放行，实际：{coin}"
    assert real == [outside], f"没登记的文件里必须照旧命中，实际：{real}"


def test_no_overstated_wording_in_the_reports_people_read():
    """夸大措辞。数字没超出噪声带时，只许写「方向为 X，未达可判定水平」。

    只扫**给人读的成品**（`reports/**` + 根目录结果文档），不扫源码 =
    范围理由见 `_iter_reader_prose()` 的文档字符串。
    """
    hits = sc.grep_prose(OVERSTATED)
    assert not hits, f"报告里出现夸大措辞 {len(hits)} 处：\n{_fmt(hits)}"


def test_the_reader_prose_scope_is_not_empty():
    """★ 反空断言：范围里**确实有文件**。

    如果 `_iter_reader_prose()` 因为路径写错而什么都不产出，
    `test_no_overstated_wording_in_the_reports_people_read` 会永远通过 ——
    而且看起来正像是「报告很干净」。这一条把那种失败模式变成红的。
    """
    files = list(_iter_reader_prose())
    assert files, "报告范围是空的 —— 上一条测试等于没在检查任何东西"
    assert any(rel.name == "cost_report.md" for _, rel in files), \
        f"没扫到已知存在的报告：{[str(r) for _, r in files]}"


def test_the_overstated_scan_would_actually_catch_a_report(tmp_path, monkeypatch):
    """★ 负控：往范围里放一份含夸大措辞的报告，扫描必须命中。

    构造是真的 —— 把扫描器的 `ROOT` 指到临时目录，再调**同一个**
    `grep_prose()`。自己重写一遍扫描循环来断言自己，那种负控是自证。
    """
    fake = tmp_path / "reports"
    fake.mkdir()
    (fake / "fake_result.md").write_text(
        "| v2 | 精度显著提升 |\n", encoding="utf-8")
    monkeypatch.setattr(sc, "ROOT", tmp_path)

    files = list(_iter_reader_prose())
    assert [rel.name for _, rel in files] == ["fake_result.md"], files
    hits = sc.grep_prose(OVERSTATED)
    assert hits, "扫描没有命中 —— 这正是这条负控要防的那件事"
    # 同一行会被多个词命中（`显著` 与 `显著提升` 都在词表里），所以断言的是
    # 「命中的文件与该行原文」，而不是命中条数 —— 条数取决于词表怎么写，
    # 拿它当断言会让「往词表里加一个同义词」变成一次无关的测试失败。
    assert {h.rel for h in hits} == {str(Path("reports") / "fake_result.md")}, hits
    assert {h.text for h in hits} == {"| v2 | 精度显著提升 |"}, hits


def test_third_party_verbatim_exclusion_is_still_honest():
    """排除清单本身要经得起检查：被排除的文件必须真的存在，且真的撞上了被禁数字。

    这一条是为了防止「排除」变成一个随手可加的后门：如果哪天有人往
    `THIRD_PARTY_VERBATIM` 里塞一个自己写的文件来消掉一个命中，这里会红。
    """
    for rel in THIRD_PARTY_VERBATIM:
        p = ROOT / rel
        assert p.is_file(), f"排除清单里的 {rel} 并不存在 —— 排除理由不成立"
        text = _read(p)
        # 被排除的文件必须是「别人给的」：要么有明确的上游出处，要么是逐字节下载的
        assert "INSERT INTO" in text or "CREATE TABLE" in text, (
            f"{rel} 看起来不是数据文件，不该出现在第三方原样数据清单里")


def test_the_scanner_excludes_only_the_two_sample_files():
    """排除清单只许含那两个文件，别的一律不许进。

    想扩大排除范围，必须改这条测试并说明理由。
    """
    assert set(SELF_FILES) == {Path("mcp_server") / "selfcheck.py",
                              Path("tests") / "test_repo_hygiene.py"}, SELF_FILES
    assert len(THIRD_PARTY_VERBATIM) == 1, (
        "第三方原样数据清单被扩大了。每加一个都要在 selfcheck.py 的文件头写清楚"
        "为什么它不能被扫描。")


@needs_wordlist
def test_the_exempt_files_do_not_hide_a_word_list():
    """★ 绕过 `SELF_FILES`、**直接扫豁免区**：那两个文件里不许有任何词表条目。

    这一条补的是原来推不出来的那个结论。原来只能说到
    「`S1`（豁免区之外）0 命中」；加上这一条，才能说「全仓没有这些词」。

    ★ 第二次修改时，这条测试的**方向反了过来**：原来它断言的是
    「词表确实在这两个文件里」（那时排除的理由是「词表扫自己必然全中」）。
    词表搬出仓库之后那个理由就不成立了；那条断言若留着，等于逼着谁把词表搬回来。
    现在断言的是反面。

    它要拿词表逐条比对才能跑，所以词表不可用时判 SKIP ——
    而 SKIP 的时候，上面那句「全仓没有这些词」**同样不成立**。
    这就是「SKIP 不等于 PASS」在这里的具体意思。
    """
    for rel in SELF_FILES:
        text = _read(ROOT / rel)
        assert text, f"{rel} 读不出来，这条检查没法做"
        found = sc.wordlist_entries_in(text)
        assert not found, (
            f"{rel} 里出现了 {len(found)} 个词表条目：{found}\n"
            "  这个文件在 SELF_FILES 里、被四类扫描跳过，所以这些串**不会**"
            "被别的检查报出来 —— 只有这一条会。词表应当住在仓库外面，"
            "样本应当从词表里取（见 selfcheck.py 的 `_wordlist_sample`）。")

    # 负控：这条检查必须真的会红，否则「豁免区干净」是一句永远成立的话。
    probe = "前缀" + FORBIDDEN_NAMES[0] + "后缀"
    assert FORBIDDEN_NAMES[0] in sc.wordlist_entries_in(probe), (
        "把一个词表条目喂进去却没被报出来 —— 这条检查是摆设")


def test_no_secrets_or_key_material_present():
    """`.env` / `*.key` / `*.pem` 不得存在于仓库（哪怕被 gitignore，也不该躺在这里）。"""
    bad = sc.iter_secret_files()
    assert not bad, f"仓库里出现了密钥类文件：{bad}"


def test_no_text_that_looks_like_a_key():
    """密钥**形态**：不是密钥文件、但长得像密钥的文本。

    这一类扫得最宽（含报告、含文档），因为密钥不挑地方出现 ——
    写在 README 的示例里同样会被人复制走。`.env.example` 只有键名、值为空，
    所以不该命中；它一旦命中，说明有人把真值填进了模板。
    """
    hits = sc.grep_secrets()
    assert not hits, f"文件里出现疑似密钥 {len(hits)} 处：\n{_fmt(hits)}"


def test_the_secret_shape_scan_would_actually_catch_a_key(tmp_path, monkeypatch):
    """★ 负控：往假仓库里放一段像密钥的文本，形态扫描必须命中。

    没有这一条的话，一个正则写错（比如少了个 `{16,}`）的扫描器会永远返回空，
    而「永远返回空」在报告里长得和「很干净」一模一样。
    """
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    (tmp_path / "README.md").write_text(
        "示例：LLM_API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("这里没有密钥。\n", encoding="utf-8")

    hits = sc.grep_secrets()
    assert hits, "扫描没有命中 —— 这正是这条负控要防的那件事"
    # 同一行会被两条规则命中（密钥前缀 + `KEY=值` 形态），所以断言的是「哪个文件」，
    # 顺带钉住「另一份没有密钥的文档没被误报」。
    assert {h.rel for h in hits} == {"README.md"}, hits
    # 命中里显示的密钥是**截断过的**（只留前 24 个字符）—— 报错信息不该把
    # 一整串疑似密钥原样抄进日志和 CI 输出里。
    assert any(h.needle.startswith("sk-abcdefghijklmnopqrstu") for h in hits), hits
    assert all(len(h.needle) <= 25 for h in hits), hits


def test_the_shipped_env_example_does_not_contain_a_real_value():
    """`.env.example` 是模板：键名可以有，**值必须是空的**。"""
    text = _read(ROOT / ".env.example")
    assert "LLM_API_KEY=" in text
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        value = value.split("#", 1)[0].strip()
        assert not value.startswith("sk-"), f".env.example 里填了真值：{key}=…"
        if key.strip() in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                           "MCP_JWT_SECRET"):
            assert value == "", f".env.example 的 {key.strip()} 不是空的：{value!r}"


def test_gitignore_covers_the_dangerous_things():
    """`.gitignore` 必须在**第一个提交之前**就把这些挡住 —— 事后加是来不及的。"""
    gi = _read(ROOT / ".gitignore")
    for must in (".env", "*.key", "*.pem", "__pycache__", ".venv", "*.db",
                 "reports/usage_ledger.jsonl"):
        assert must in gi, f".gitignore 里缺 `{must}`"


def test_no_absolute_paths_from_the_authors_machine():
    """不许把作者机器上的绝对路径写进代码 —— 那正是「别人 clone 下来跑不通」的根因。

    这条是有来历的：早先的写法把数据目录绝对路径硬编码在模块里，别人拿到之后
    报的错指向一个和他的机器毫无关系的路径。`paths.py` 就是为了根治它。
    """
    pat = re.compile(sc.ABS_PATH_PATTERN, re.I)   # 单一口径：与 19_verify.py 用同一条
    hits = []
    for p, rel in sc.iter_files(SCOPE_AUTHORED):
        if p.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".yaml"}:
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            if pat.search(line):
                hits.append(sc.Hit(str(rel), i, pat.search(line).group(0),
                                   line.strip()[:160]))
    assert not hits, f"代码/文档里出现作者机器的绝对路径：\n{_fmt(hits)}"


def _volatile_hits(base: Path) -> list:
    """扫一个目录下的产物里有没有时钟读数。抽出来是为了能对 `tmp_path` 做负控。

    **一行只报一条**：几条正则是互相覆盖的（`in 22.51s` 同时命中前两条），
    逐条报的话一个读数会变成两条命中。报告要的是「哪一行有问题」，
    不是「有几个正则匹配上了」。
    """
    hits = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".md", ".json", ".jsonl"}:
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            for pat in vk.VOLATILE_RES:
                m = pat.search(line)
                if m:
                    hits.append(sc.Hit(str(p.relative_to(base)), i, m.group(0),
                                       line.strip()[:160]))
                    break
    return hits


def test_the_committed_artifacts_carry_no_wall_clock_reading():
    """★ 产物要**逐字节可复现**，所以 `reports/` 里不许有时钟读数。

    这条是有来历的：`pytest` 的尾巴（`515 passed in 22.51s`）和评测脚本每道题
    后面的 `calls=1 0.02s` 被抄进了 `reports/`，于是**每跑一次闸门就有十来个
    产物显示「已修改」**。`git status` 因此不再是「有没有变化」的信号，
    而「跑完还是干净的」本来是这个仓库最能自证的一句话。

    判据（可重跑）：跑两遍 `tools/check.py`，`reports/` 的聚合哈希相同。
    实测两遍得到同一个哈希；这条测试守的就是那个前提。
    """
    hits = _volatile_hits(ROOT / "reports")
    assert not hits, (
        "这些产物里有每次都不同的时钟读数，它们会让 `reports/` 失去可复现性：\n"
        + _fmt(hits)
        + "\n修法：**别把耗时抄进落盘产物**（打印出来给人看可以）。"
          "引用子进程输出时走 `verify_kit.tail_for_report()`，它会把耗时掩码。")


def test_the_wall_clock_scan_would_actually_catch_a_reading(tmp_path):
    """★ 负控：真放一条时钟读数进去，必须被抓到 —— 否则上面那条只是没在跑。"""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "bad.md").write_text(
        "合计 515 passed in 22.51s\n", encoding="utf-8")
    assert len(_volatile_hits(tmp_path / "reports")) == 1

    # 反方向：整数秒是**声明过的阈值**，不是时钟读数，不许误伤。
    (tmp_path / "reports" / "good.md").write_text(
        "超时：read_timeout_seconds=20s，外层硬超时 30s\n", encoding="utf-8")
    hits = _volatile_hits(tmp_path / "reports")
    assert len(hits) == 1, "整数秒被误判成了时钟读数"
    assert "passed in" in hits[0].text

    # ★ 补的一条：pytest 的**第二种**耗时形态，只有套件跑满 60 秒才会出现。
    # `format_session_duration` 在 `seconds >= 60` 时返回 `f"{seconds:.2f}s ({dt})"`，
    # 前三条正则只吃掉 `in 22.51s`，括号里的 `(H:MM:SS)` 原样留在产物里 ——
    # 「同一份代码，跑得快就干净、跑得慢就脏」。这一台实测踩到了，
    # 详见 `verify_kit.VOLATILE_RES` 第 4 条上方的说明。
    (tmp_path / "reports" / "slow_run.md").write_text(
        "604 passed 〔耗时已隐去〕 (0:01:19)\n", encoding="utf-8")
    hits = _volatile_hits(tmp_path / "reports")
    assert len(hits) == 2, "pytest 的 `(H:MM:SS)` 形态没有被抓到"
    assert any(h.needle == "(0:01:19)" for h in hits), \
        f"抓到的不是括号里的读数：{[h.needle for h in hits]}"
    # 反方向再来一次：括号里不是时钟的圆括号不许误伤。
    (tmp_path / "reports" / "slow_run.md").write_text(
        "工具数（6 个）与 scope（db:read）都一致\n", encoding="utf-8")
    assert len(_volatile_hits(tmp_path / "reports")) == 1, "普通圆括号被误判了"


def test_every_report_that_reports_numbers_has_a_caliber_section():
    """有数字的报告必须有「口径」小节。这是 §8 自检第 6 项，在这里也钉一遍。

    在这里钉的理由：`19_verify.py` 是**人手动跑的**，而这条测试每次 `pytest`
    都跑 —— 一份新写的报告少了口径小节，当天就会红，而不是等下次自检。
    """
    rows = sc.audit_report_numbers()
    assert rows, "一份报告都没扫到 —— 路径写错了，这条测试等于没跑"
    bad = [r for r in rows if not r["ok"]]
    assert not bad, (
        "这些报告里有数字却没有「口径」小节：\n"
        + "\n".join(f"  {r['file']}：{r['n_numbers']} 个数字，例如 {r['sample']}"
                    for r in bad)
        + "\n修法：在这份报告里加一节 `## 口径`，逐项写明定义 / 样本量 / 局限性。")


def test_the_caliber_audit_would_actually_catch_a_report_without_one(tmp_path,
                                                                    monkeypatch):
    """★ 负控：一份只有数字、没有口径小节的报告必须被判为不合格。

    并且反方向也要有：同一份报告加上 `## 口径` 小节之后就合格 ——
    只测前者的话，一个「永远返回不合格」的实现也能通过。
    """
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    r = tmp_path / "reports"
    r.mkdir()
    (r / "no_caliber.md").write_text("# 结果\n\n严格 EX 32/32，调用 36 次。\n",
                                     encoding="utf-8")
    rows = {x["file"]: x for x in sc.audit_report_numbers()}
    assert rows["reports/no_caliber.md"]["ok"] is False
    assert "32/32" in rows["reports/no_caliber.md"]["sample"]

    (r / "no_caliber.md").write_text(
        "# 结果\n\n## 口径\n\n严格 EX = 结果元组逐字相等。\n\n严格 EX 32/32。\n",
        encoding="utf-8")
    rows = {x["file"]: x for x in sc.audit_report_numbers()}
    assert rows["reports/no_caliber.md"]["ok"] is True


def test_a_report_with_no_numbers_is_exempt_but_that_is_recorded(tmp_path,
                                                                monkeypatch):
    """没有数字的报告天然豁免 —— 但豁免这件事本身要**被记下来**。

    `reports/final_selfcheck.md` 会写出每份报告的数字个数，
    所以「豁免」不是靠一张按文件名开的白名单，而是内容自己挣来的。
    """
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    r = tmp_path / "reports"
    r.mkdir()
    (r / "empty.md").write_text("# 未完成\n\n这一步没有跑，原因见卡点清单。\n",
                                encoding="utf-8")
    rows = {x["file"]: x for x in sc.audit_report_numbers()}
    assert rows["reports/empty.md"]["n_numbers"] == 0
    assert rows["reports/empty.md"]["ok"] is True


# ---------------------------------------------------------------- 掩码（报告不许自带污染）
# 这一节防的是一个**会自我放大的回路**：
# 自检报告要列出命中的位置，若把命中的字符串逐字抄下来，报告自己就成了一份
# 带着这些字符串的产物 —— 下一轮扫描会在报告里**再次**命中它，而且这次命中的
# 是「我自己的报告」。实测发生过：第一版 `19_verify.py --quick` 报出的命中，
# 大半来自它上一轮写下的 `reports/verify.md`。
#
# 治法不是「写的时候小心一点」，而是：报告里只写位置 + 掩码片段，
# 并让 `scan_report_carrier()` 机械地确认掩码没漏。
def _fixture_body_params():
    """负控样本 → parametrize 参数，并**逐条**标注哪些依赖词表。

    逐条而不是整组：S4/S6/S7 用的是夸大措辞、密钥形态、绝对路径，
    这三张表本来就在仓库里，和词表在不在没关系 —— 整组 SKIP 会把它们一起放过去，
    而它们正好是**不需要**外部文件就永远能跑的那几条。
    """
    out = []
    for cid, _fname, body, _cat in sc.NEGATIVE_FIXTURES:
        if cid == "S8":  # S8 走的是「报告数字口径」那条路，另有测试盯着
            continue
        needs_wl = cid in sc.WORDLIST_FIXTURES and not sc.WORDLIST_AVAILABLE
        out.append(pytest.param(body, id=cid,
                                marks=pytest.mark.skipif(needs_wl,
                                                         reason=sc.WORDLIST_PENDING)))
    return out


FIXTURE_BODY_PARAMS = _fixture_body_params()


@pytest.mark.parametrize("body", FIXTURE_BODY_PARAMS)
def test_masking_removes_the_very_thing_it_reports(body):
    """掩码后的文本必须自己不含污染。**逐个样本**测，不是挑一个好测的。"""
    masked = sc.mask_all(body)
    assert not sc.scan_report_carrier(masked), (
        f"掩码之后仍带着污染：{sc.scan_report_carrier(masked)}")
    assert sc.MASK in masked, "掩码没生效 —— 样本里连一个需要盖的串都没有？"


@pytest.mark.parametrize("body", FIXTURE_BODY_PARAMS)
def test_the_carrier_scan_would_actually_catch_an_unmasked_quotation(body):
    """★ 负控：未掩码的同一段**必须**被判出来。否则掩码检查是个永远绿的摆设。"""
    assert sc.scan_report_carrier(body), "未掩码的样本没被判出来 —— 掩码检查是摆设"


def test_the_sha_fingerprint_keeps_the_verdict_and_drops_the_key():
    """提交号进产物之前要换成指纹：**判定能力不能丢，钥匙不能留**。

    两件事都要钉住，缺一条这个替换就是错的：

    - 丢了判定能力（不同提交号撞成同一个指纹）→ 第 13 项就成了摆设；
    - 留了钥匙（指纹里带着原提交号的一段）→ 白换，等于没做。
    """
    a = "a" * 40
    b = "a" * 39 + "b"          # 只差最后一位 —— 指纹也必须不同
    assert sc.sha_fingerprint(a) == sc.sha_fingerprint(a), "同一个提交号要得同一个指纹"
    assert sc.sha_fingerprint(a) != sc.sha_fingerprint(b), "差一位就撞了，指纹失去判定能力"
    assert sc.sha_fingerprint("") == ""
    got = sc.sha_fingerprint(a)
    assert len(got) == 12 and all(c in "0123456789abcdef" for c in got)
    # ★ 负控那一半：指纹里不许出现原提交号的任何一段（7 位以上在托管站点上就能解析）
    assert got not in a and a[:7] not in got and a[:8] not in got


@needs_wordlist
def test_the_masking_wordlists_are_the_same_ones_the_scan_uses():
    """掩码用的词表必须**就是**扫描用的那一批。

    两边各写一份的话，早晚会出现「扫描器认识、掩码不认识」的词 ——
    那个词就会一路写进报告，然后再被扫描器从报告里扫出来。

    整条判 SKIP 而不是只跳过词表那三类：三张词表都在仓外（见文件头），
    词表不可用时它们为空、`assert words` 必然红 —— 那是**假红**。
    唯一的仓内表 `OVERSTATED` 的掩码由上面 S4 那条参数化用例双向验着，
    不依赖词表，所以跳掉这里不会在它身上留缺口。
    """
    for name in ("FORBIDDEN_NAMES", "MEDICAL_TERMS", "FORBIDDEN_NUMBERS", "OVERSTATED"):
        words = getattr(sc, name)
        assert words, f"{name} 是空的"
        for w in words[:3]:
            assert sc.MASK in sc.mask_all(f"前缀{w}后缀"), f"{name} 里的「{w}」掩不掉"
            assert sc.scan_report_carrier(f"前缀{w}后缀"), f"{name} 里的「{w}」扫不出来"


def test_the_selfcheck_report_does_not_carry_what_it_reports():
    """`reports/verify.md` 自己不能带着它报出来的那些串。

    这是 S9 的 pytest 版本。两边都留：S9 跟着自检一起跑、结果写在报告里；
    这条跟着每次 `pytest` 跑，报告一写完就会红，不必等下一次自检。
    """
    import subprocess

    p = ROOT / "reports" / "verify.md"
    if not p.is_file():
        pytest.skip("还没跑过 19_verify.py，没有报告可查")
    carrier = sc.scan_report_carrier(_read(p))
    assert not carrier, (
        f"自检报告自己带着污染：{[sc.mask_all(c) for c in carrier]}\n"
        "修法：改 `experiments/19_verify.py` 里 `render_report()` 的模板文字 —— "
        "报告是生成物，改它没用，下一次运行会整个覆盖。")


def test_data_root_defaults_inside_the_repository():
    """`paths.data_root()` 的默认值必须在仓库内，不能跟着机器走。"""
    import os

    from mcp_server import paths

    saved = os.environ.pop(paths.ENV_DATA_ROOT, None)
    try:
        assert paths.data_root() == paths.ROOT / "data"
    finally:
        if saved is not None:
            os.environ[paths.ENV_DATA_ROOT] = saved


@pytest.mark.parametrize("rel", ["README.md", "NOTICE.md", "LICENSE"])
def test_required_root_documents_exist_later(rel):
    """这几份文件属于收尾阶段。**现在允许缺失**，但阶段 6 结束前必须都在。

    写成测试而不是待办事项，是为了让「忘了写」这件事在每次 pytest 里都冒一次头 ——
    待办清单会被忽略，红色测试不会。
    """
    if not (ROOT / rel).exists():
        pytest.skip(f"{rel} 属于阶段 6 产物，尚未创建")


def _entries_missing_setup(dirs) -> list[str]:
    """扫描若干目录，返回**没有**调用 `console.setup_stdio()` 的入口脚本。

    抽成独立函数是为了能被负控测试拿一份**故意写坏的样本**来调 ——
    「这条检查会红」和「这条检查现在是绿的」是两件事，见下。
    """
    missing = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.py")):
            text = _read(p)
            if "__main__" not in text:      # 不是入口（被 import 的工具模块）
                continue
            if "console.setup_stdio()" not in text:
                missing.append(str(p))
    return missing


def test_every_entry_script_sets_up_its_own_stdio():
    """★ 每个可执行入口都必须自己调 `console.setup_stdio()`。

    ## 这条测试的来历

    在简体中文 Windows 上直接跑 `python experiments/11_guardrail_test.py`（不带
    `-X utf8`）时，stdout 按 GBK 编码。脚本把 41 条用例全跑完、打印出「41 / 41」，
    然后**在最后一行打印 ✅ 时抛 `UnicodeEncodeError`，进程以 1 结束**。
    调用方看到的结论是「护栏实验失败」。

    这比崩溃更糟：它教人「这个脚本的退出码不用信」，而退出码正是本仓报告里
    最硬的一类证据（`11_guardrail_test.py` 每条负例的「实测」栏依据就是它）。

    ## 为什么用静态扫描而不是跑一遍

    跑一遍要几十秒（那些实验会拉起几十个子进程），而且只覆盖「这次跑到的」脚本。
    静态扫描一秒内覆盖**所有**入口，包括以后新加的 —— 新脚本忘了写这行，
    测试当天就红，而不是等到某台机器上以 GBK 输出时才红。

    断言写成「文件里出现 `console.setup_stdio()`」而不是「在 main 的第一行」：
    后者要靠正则去猜 `main` 的形状，脆且不值。
    """
    dirs = [ROOT / "experiments", ROOT / "tools"]
    entries = [p for d in dirs if d.is_dir() for p in sorted(d.glob("*.py"))]
    assert entries, "一个入口脚本都没扫到 —— 扫描路径写错了，这条测试等于没跑"

    missing = _entries_missing_setup(dirs)
    assert not missing, (
        "这些入口脚本没有在 main() 里调用 console.setup_stdio()，"
        "在 GBK 控制台上会因打印中文/符号而崩、并且退出码被带歪：\n  "
        + "\n  ".join(missing)
        + "\n修法：`from mcp_server import console`，然后在 main() 第一行加 "
          "`console.setup_stdio()`")


def test_the_stdio_scan_would_actually_catch_a_script_that_forgot(tmp_path):
    """★ 负控：让上面那条检查**红一次**，证明它不是恒真的。

    做法是喂一份故意写坏的样本进去，而不是「临时把仓库里的某一行删掉、跑一遍、
    再改回来」—— 后者有一个众所周知的坏处：恢复之后，什么都没留下，
    谁也看不出这条检查到底验过没有。样本落在 `tmp_path` 里，被测的仓库文件
    一个字节都没动。
    """
    good = tmp_path / "good" / "ok.py"
    bad = tmp_path / "good" / "forgot.py"
    lib = tmp_path / "good" / "not_an_entry.py"
    good.parent.mkdir(parents=True)
    good.write_text(
        "import sys\nfrom mcp_server import console\n\n"
        "def main():\n    console.setup_stdio()\n    return 0\n\n"
        "if __name__ == '__main__':\n    sys.exit(main())\n", encoding="utf-8")
    bad.write_text(
        "import sys\n\ndef main():\n    print('✅ 全过')\n    return 0\n\n"
        "if __name__ == '__main__':\n    sys.exit(main())\n", encoding="utf-8")
    lib.write_text("def helper():\n    return 1\n", encoding="utf-8")

    found = _entries_missing_setup([tmp_path / "good"])
    assert found == [str(bad)], found
    assert str(good) not in found and str(lib) not in found


def test_the_stdio_helper_is_not_wired_into_the_server():
    """`mcp_server/server.py` 的 stdout 是 JSON-RPC 通道，**不许**在那里重配 stdio。

    这条是反向断言。`console.setup_stdio()` 是给「给人看的脚本」用的；
    接进服务端会把协议通道的编码也改掉，而且它本来就不该往 stdout 写人话
    （这个坑已经踩过一次，见 `server.py` 顶部那段）。
    """
    text = _read(ROOT / "mcp_server" / "server.py")
    assert "setup_stdio" not in text, (
        "server.py 里出现了 setup_stdio —— 服务端的 stdout 是协议通道，不能重配")


def test_the_stdio_helper_actually_works_and_never_raises():
    """正反两面：能编出 emoji，且流被换成怪对象时不抛异常。"""
    from mcp_server import console

    console.setup_stdio()          # 幂等：重复调用也不该出问题
    console.setup_stdio()

    class NotAResStream:
        def write(self, _s):       # 没有 reconfigure 的对象
            return 0

    import sys as _sys

    saved_out, saved_err = _sys.stdout, _sys.stderr
    try:
        _sys.stdout = NotAResStream()   # type: ignore[assignment]
        _sys.stderr = NotAResStream()   # type: ignore[assignment]
        console.setup_stdio()           # 不许抛
    finally:
        _sys.stdout, _sys.stderr = saved_out, saved_err
