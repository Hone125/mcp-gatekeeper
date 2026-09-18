"""`mcp_server/verify_kit.py` 的单元测试 —— 也就是「判定别人」的那段代码。

## 为什么这块必须单独测

`verify_kit` 判的是「仓库作为交付物合不合格」。它自己判错的话，错法不是崩溃，
而是**一张全绿的表**：一件不合格的事被判成 PASS，读报告的人无从发现。

所以这里的重点全在**边界和负例**上：期望码差一个、痕迹缺一件、
命令根本没跑起来（退出码 -1 与「跑了但结果不对」是两种问题，修的人不一样）。

## 为什么还要起一次子进程

下面大部分用例是纯函数调用，但纯函数测不到「`19_verify.py` 这个入口还在不在、
import 还通不通、`--selftest` 还跑不跑得起来」。测试文件以非数字开头、能 import，
脚本名以数字开头、**不能 import** —— 所以它只能被当成子进程测。
"""
from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server import selfcheck as sc
from mcp_server import verify_kit as vk

ROOT = sc.ROOT
PY = sys.executable

# 下面有几条用例**必须拿词表里的真词当样本**（掩码、自毒、豁免那一族）。
# 词表按设计住在仓库外，clone 下来就没有 —— 那几条**没有可比对的东西**，
# 判 SKIP 而不是红（`SKIP ≠ PASS`，见 `DECISIONS.md` D-44）。
#
# 守卫写在**用例上**、不写在整个文件上：这个文件里多数用例与词表无关，
# 整文件跳过会把它们一起丢掉 —— 那才是真的把覆盖降下来。
needs_wordlist = pytest.mark.skipif(
    not sc.WORDLIST_AVAILABLE,
    reason="词表不在本机（仓外文件）：样本取不到，本项没有实测依据")


def _llm_entry() -> dict:
    return {"cmd": ["experiments/09_text2sql_eval.py"], "expect": 0,
            "needs_llm": True, "not_run": "reports/text2sql_NOT_RUN.md",
            "why": "测试用"}


# ---------------------------------------------------------------- 退出码判定
def test_the_expected_exit_code_is_a_pass():
    assert vk.judge_exit(_llm_entry(), 0, ROOT).status == vk.PASS


def test_reporting_five_without_leaving_anything_behind_is_a_fail(tmp_path):
    """光喊「未完成」不算数 —— 没有落盘、没有解法的「未完成」，
    在报告里和「忘了跑」长得一模一样。"""
    (tmp_path / "reports").mkdir()
    r = vk.judge_exit(_llm_entry(), 5, tmp_path)
    assert r.status == vk.FAIL
    assert "text2sql_NOT_RUN.md" in r.detail


def test_leaving_a_marker_without_a_blockers_entry_is_still_a_fail(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "text2sql_NOT_RUN.md").write_text("x", encoding="utf-8")
    r = vk.judge_exit(_llm_entry(), 5, tmp_path)
    assert r.status == vk.FAIL
    assert vk.BLOCKERS in r.detail


def test_all_three_conditions_together_make_it_a_legitimate_skip(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "text2sql_NOT_RUN.md").write_text("x", encoding="utf-8")
    (tmp_path / vk.BLOCKERS).write_text("见 reports/text2sql_NOT_RUN.md\n",
                                        encoding="utf-8")
    assert vk.judge_exit(_llm_entry(), 5, tmp_path).status == vk.SKIP


def test_a_command_that_never_started_is_an_error_not_a_fail():
    """-1 是 `_run()` 用来表示「没跑起来」的码。判成 FAIL 的话，
    读报告的人会去查仓库内容，而真正该查的是环境。"""
    assert vk.judge_exit(_llm_entry(), -1, ROOT).status == vk.ERROR


def test_an_undeclared_exit_code_is_a_fail():
    entry = {"cmd": ["x.py"], "expect": 0, "why": "测试用"}
    assert vk.judge_exit(entry, 1, ROOT).status == vk.FAIL


def test_a_declared_alternative_code_is_a_skip_not_a_pass():
    """`12_cost_report.py` 在账本为空时以 5 结束 —— 这是**声明过**的替代口径。
    但它仍然是 SKIP：报告没生成出来，就不该得到 PASS 的待遇。"""
    entry = {"cmd": ["experiments/12_cost_report.py"], "expect": 0,
             "alt": {5: "账本为空"}, "why": "测试用"}
    r = vk.judge_exit(entry, 5, ROOT)
    assert r.status == vk.SKIP
    assert "账本为空" in r.detail


def test_a_pass_is_a_pass_even_when_the_run_had_output():
    """有输出尾部也要照判 —— 别把「输出里有 error 字样」当成失败信号：
    脚本本来就可能把 `errors: 0` 这种字样打出来。"""
    assert vk.judge_exit(_llm_entry(), 0, ROOT, "errors: 0").status == vk.PASS


def test_every_entry_that_needs_the_sample_db_declares_the_no_db_alternative():
    """★ 查示例库的那些入口，必须声明「库没建」这条合法口径。

    示例库是构建产物、按设计不入库（源 SQL 随仓库分发）。新 clone 一台机器上
    它们会以 3 结束并写明「[未完成] 示例库还没构建」—— 脚本自己说的是「未完成」，
    台账要是不声明这条口径，就会把同一件事写成 `FAIL 退出码 3，期望 0`：
    把一个**没做**说成**做坏了**。这一条钉住那份声明不被悄悄摘掉。

    ★ 名单**不写死**：谁声明了 `E_NO_DB` 就归谁管，直接从脚本源码里读。
      写死名单的话，将来新增一个查库的脚本不会被这条测试发现。
    """
    needs_db = {p for p in (ROOT / "experiments").glob("*.py")
                if "E_NO_DB" in p.read_text(encoding="utf-8")}
    assert needs_db, "一个声明 E_NO_DB 的脚本都没找到 —— 这条测试失去了对象"
    checked = 0
    for entry in vk.EXIT_LEDGER:
        script = ROOT / entry["cmd"][0]
        if script not in needs_db:
            continue
        checked += 1
        assert entry.get("alt", {}).get(vk.NO_DB_EXIT), \
            f"{entry['cmd']} 查示例库却没声明「库没建」的口径"
        r = vk.judge_exit(entry, vk.NO_DB_EXIT, ROOT)
        assert r.status == vk.SKIP, (entry["cmd"], r.status)
        assert "未完成" in r.detail, (entry["cmd"], r.detail)
    assert checked >= 5, f"只查到 {checked} 条查库的入口 —— 少了就是名单算错了"


def test_an_author_machine_path_in_the_quoted_output_never_reaches_the_report():
    """★ 这段尾巴是**引用**，会被原样抄进 `reports/verify.md`。

    脚本在自己 stdout 里打一行绝对路径（`print(f"报告：{REPORTS / 'x.md'}")`），
    报告就变成那个路径的载体，下一轮 S7 在自己的报告里命中它 —— 实测踩过。
    引用前掩码，并且**明说剪过**。
    """
    entry = {"cmd": ["x.py"], "expect": 0, "why": "测试用"}
    tail = "报告：C:\\Users\\someone\\Desktop\\repo\\reports\\x.md"
    detail = vk.judge_exit(entry, 1, ROOT, tail).detail
    assert "C:\\Users" not in detail
    assert "已隐去" in detail, "剪过却不说，读报告的人会以为这段引用是完整的"


def test_a_clean_tail_is_quoted_verbatim():
    """反方向：没问题的尾巴要原样保留，否则「已隐去」会变成一句到处都在的废话。"""
    entry = {"cmd": ["x.py"], "expect": 0, "why": "测试用"}
    detail = vk.judge_exit(entry, 1, ROOT, "退出码 1：题目 3 判错").detail
    assert "题目 3 判错" in detail
    assert "已隐去" not in detail


def test_a_wall_clock_reading_in_the_quoted_output_never_reaches_the_report():
    """★ 耗时读数每次都不同，抄进报告就等于报告每次都不一样。

    实测它是这么红起来的：`pytest` 的尾巴带着 `513 passed in 22.28s`，
    被抄进 `reports/verify.md` 之后，**每跑一次闸门这个文件就变成「已修改」**——
    而「跑完 `git status` 还是干净的」正是这个仓库最能自证的一句话。
    """
    entry = {"cmd": ["x.py"], "expect": 0, "why": "测试用"}
    for tail in (".... 513 passed in 22.28s",
                 "calls=1 strict=True tolerant=True 0.03s",
                 "7.0 秒后失败：WouldBlock"):
        detail = vk.judge_exit(entry, 1, ROOT, tail).detail
        assert "22.28" not in detail and "0.03" not in detail and "7.0" not in detail, tail
        assert "耗时已隐去" in detail, tail
        assert "已隐去" in detail, "剪过却不说，读报告的人会以为这段引用是完整的"


def test_a_path_split_by_truncation_never_leaks_a_fragment():
    """★ 截断必须发生在掩码**之后**。

    反过来的写法（`tail_for_report(x)[:300]`）看着等价，实测不是：
    截断把一条绝对路径从中间切开，剩下半截 `C:\\Users` —— 掩码认不出来
    （正则要更多分隔符），检查也认不出来（同一条正则）。于是它就安安静静
    躺在报告里，谁都不会报。

    所以判据不是「检查通过」，而是「**文本里没有那个片段**」——
    这条用例直接查片段本身，不查正则。
    """
    back = chr(92)
    path = back.join(["C:", "Users", "19070", "Desktop", "repo", "reports", "x.md"])
    tail = "x" * 270 + " 报告：" + path
    assert len(tail) > 300, "这条用例要的是「尾巴比 limit 长」，样本得够长"

    got = vk.tail_for_report(tail, limit=300)
    assert "C:" + back not in got, f"半截路径漏了出去：{got[-80:]!r}"
    assert "已隐去" in got, "剪过却不说，读报告的人会以为这段引用是完整的"
    assert len(got) <= 300, "limit 没生效"


def test_integer_seconds_are_a_declaration_and_must_survive():
    """反方向：整数秒是**声明过的阈值**，不是时钟读数，不能一起抹掉。

    `read_timeout_seconds=5s` 与 `HARD_TIMEOUT=20s` 是配置；`7.03s` 是测量。
    如果正则写成 `\\d+s`，前者会被当成后者误伤 —— 那份报告就丢掉了它本来要说的信息。
    """
    entry = {"cmd": ["x.py"], "expect": 0, "why": "测试用"}
    detail = vk.judge_exit(
        entry, 1, ROOT,
        "超时：read_timeout_seconds=5s，外层硬超时 20s").detail
    assert "read_timeout_seconds=5s" in detail
    assert "20s" in detail
    assert "已隐去" not in detail


# ---------------------------------------------------------------- 台账自身
def test_every_ledger_entry_declares_what_it_needs_to_be_judgeable():
    """台账里每条都要有：跑什么、期望几、为什么跑。少一样就没法判。"""
    for e in vk.EXIT_LEDGER:
        assert e["cmd"] and isinstance(e["cmd"], list)
        assert isinstance(e.get("expect"), int)
        assert e.get("why", "").strip(), f"{e['cmd']} 没写口径"
        if e.get("needs_llm"):
            assert e.get("not_run", "").startswith("reports/"), \
                f"{e['cmd']} 说要留「未完成」痕迹，却没给路径"


def test_every_ledger_script_actually_exists():
    """路径打错一个字符，表现是 ERROR（命令跑不起来），而不是「这条检查没了」——
    所以在这里先把路径钉死。"""
    missing = [e["cmd"][0] for e in vk.EXIT_LEDGER if not (ROOT / e["cmd"][0]).is_file()]
    assert not missing, f"台账里这些脚本不存在：{missing}"


def test_the_ledger_ids_are_unique():
    ids = [vk.cid_for(e) for e in vk.EXIT_LEDGER]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"这些 id 撞了，报告表里没法区分：{dupes}"


# ---------------------------------------------------------------- 产物互斥
def test_the_artifact_checks_flag_a_result_and_a_not_run_marker_coexisting(tmp_path):
    """「跑过了」和「没跑成」两块牌子同时挂着 —— 读报告的人只会看到其中一块。"""
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "noise_band.json").write_text("{}", encoding="utf-8")
    (tmp_path / "reports" / "noise_band_NOT_RUN.md").write_text("x", encoding="utf-8")
    got = {r.cid: r for r in vk.artifact_checks(tmp_path)}
    assert got["A1"].status == vk.FAIL

    (tmp_path / "reports" / "noise_band.json").unlink()
    got = {r.cid: r for r in vk.artifact_checks(tmp_path)}
    assert got["A1"].status == vk.PASS, "只有「未完成」声明时不该判矛盾"
    assert "未完成" in got["A1"].detail


def test_the_artifact_checks_are_honest_about_neither_being_present(tmp_path):
    """两者都没有 —— 判 PASS（不矛盾）但说明里要写清「两者都没有」，
    不能含糊成一句「通过」。"""
    (tmp_path / "reports").mkdir()
    got = {r.cid: r for r in vk.artifact_checks(tmp_path)}
    assert got["A1"].status == vk.PASS
    assert "都没有" in got["A1"].detail


# ---------------------------------------------------------------- 文档
def test_missing_required_docs_is_a_fail(tmp_path):
    r = {x.cid: x for x in vk.doc_checks(tmp_path)}["D1"]
    assert r.status == vk.FAIL
    assert "NOTICE.md" in r.detail


def test_the_phase_six_docs_are_skipped_not_passed(tmp_path):
    """没创建的文档判 SKIP 而不是 PASS —— 一张写着 PASS 的表会让人以为查过了。"""
    r = {x.cid: x for x in vk.doc_checks(tmp_path)}["D2"]
    assert r.status == vk.SKIP

    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "RESULTS.md").write_text("x", encoding="utf-8")
    assert {x.cid: x for x in vk.doc_checks(tmp_path)}["D2"].status == vk.PASS


def test_the_reader_prose_scope_covers_the_docs_that_people_read():
    """S4（夸大措辞）只扫给人读的成品，而范围必须真的包含根目录那几份 ——
    范围写空的话，S4 就是一个永远绿的空壳。

    注意这里查的是**范围定义**（`READER_DOCS`），不是 `iter_files` 的实际产出：
    后者只 yield 存在的文件，`README.md` 还没写的时候查不出来 ——
    那会让这条测试在阶段 6 之前永远红着，而在阶段 6 之后又测不到范围定义本身。
    """
    for must in ("README.md", "RESULTS.md"):
        assert must in sc.READER_DOCS, f"{must} 不在 S4 的范围定义里"

    real = {Path(rel).as_posix() for _, rel in sc.iter_reader_prose()}
    assert any(f.startswith("reports/") for f in real), "reports/ 下的报告没进 S4 范围"
    assert "reports/verify.md" in real or not (ROOT / "reports" / "verify.md").is_file()


# ---------------------------------------------------------------- 入口脚本（子进程）
def test_the_selftest_entry_point_still_runs():
    """`19_verify.py` 以数字开头、没法 import，只能当子进程测。

    这条测的是「入口还在、import 还通、`--selftest` 跑得起来」——
    它不重复断言里面每条负控，那是脚本自己干的事；这里只保证它**被跑到了**。
    """
    p = subprocess.run([PY, str(ROOT / "experiments" / "19_verify.py"), "--selftest"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert p.returncode == 0, f"自检的负控没通过：\n{p.stdout}\n{p.stderr}"
    assert "负控" in p.stdout


def test_the_gate_chain_lists_its_steps_without_running_them():
    """`tools/check.py` 不在退出码台账里（它自己会跑 19_verify，放进去就转起来了），
    所以由这条用例守着：入口在、能起、链子列得出来。

    `--list` 是刻意设计的：一个「跑起来要几分钟」的闸门，必须有办法**只看它要干什么**。

    ★ `26_final_selfcheck.py` 必须出现在链条里。它同样不在台账里（也要跑 19_verify），
    于是它**唯一的**守门人就是这一条断言 —— 少写一个字符，它就静默地不再被跑，
    而这正是「收尾自检」最不该有的状态。
    """
    p = subprocess.run([PY, str(ROOT / "tools" / "check.py"), "--list"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert p.returncode == 0, f"--list 没跑通：\n{p.stdout}\n{p.stderr}"
    for must in ("pytest", "19_verify.py", "25_deliverable_check.py",
                 "26_final_selfcheck.py"):
        assert must in p.stdout, f"链子里没有 {must}"
    assert "退出码" in p.stdout, "没有说明「退出码 = 第几步」，读的人不知道怎么看结果"


def test_the_usage_exit_code_cannot_collide_with_a_step_number():
    """用法错误的退出码不能是 2 或 4 —— 链条现在有 4 步，那两个码另有所指。

    读到一个会和正常结果撞车的码，人会去查错的那一步。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gate", ROOT / "tools" / "check.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    assert mod.E_USAGE not in range(1, len(mod.STEPS) + 1)
    assert mod.E_USAGE != 0


# ---------------------------------------------------------------- 产物可复现
def _fake_tree(tmp_path, files: dict[str, str]) -> Path:
    (tmp_path / "reports").mkdir()
    for name, body in files.items():
        p = tmp_path / "reports" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return tmp_path


def test_the_artifact_hash_is_stable_when_nothing_changed(tmp_path):
    """同一棵树取两次哈希，必须相同 —— 否则这条尺子本身就在抖。"""
    root = _fake_tree(tmp_path, {"a.md": "合计 15/15\n", "sub/b.json": "{}\n"})
    assert vk.artifacts_hash(root) == vk.artifacts_hash(root)


def test_the_artifact_hash_changes_when_one_byte_changes(tmp_path):
    """★ 负控：改一个字节必须改变哈希。

    没有这条，「哈希相同」可能只是因为哈希函数压根没在看内容 ——
    那它就是一句没有分母的漂亮话。
    """
    root = _fake_tree(tmp_path, {"a.md": "合计 15/15\n"})
    before, n = vk.artifacts_hash(root)
    assert n == 1, "文件数没数对"

    (root / "reports" / "a.md").write_text("合计 14/15\n", encoding="utf-8")
    after, _ = vk.artifacts_hash(root)
    assert after != before


def test_renaming_an_artifact_changes_the_hash(tmp_path):
    """文件名也要进哈希：只哈希内容的话，「改名」会被当成「没变化」。"""
    root = _fake_tree(tmp_path, {"a.md": "同样的一份内容\n"})
    before, _ = vk.artifacts_hash(root)
    (root / "reports" / "a.md").rename(root / "reports" / "b.md")
    after, _ = vk.artifacts_hash(root)
    assert after != before


# ------------------------------------------------------ 产物不被顺手改写
def test_products_preserved_puts_the_bytes_back(tmp_path):
    """★ 正控：跑完把文件**按字节**放回去，而且留得下可核的痕迹。

    这一条为一个真事故补的：`experiments/27_leak_fix_verify.py` 为了证明
    「读不到词表时判 SKIP」，会带着一个假的词表路径去跑闸门第 4 步，
    而第 4 步里的 `19_verify.py` 与 `pytest` 会把 `reports/verify.md`
    与 `reports/final_selfcheck.md` **重写成「本机没有词表」的样子**。
    跑完那次核验，工作区里就躺着两份降级的报告，看上去和真的一样。
    """
    a = tmp_path / "verify.md"
    a.write_bytes(b"\xe5\x90\x88\xe8\xae\xa1\r\n")      # 含 CRLF：字节级还原才守得住
    before = a.read_bytes()

    with vk.ProductsPreserved([a]) as guard:
        a.write_text("降级之后的样子\n", encoding="utf-8")

    assert a.read_bytes() == before, "没还原成原样"
    ev = guard.evidence[0]
    assert ev["restored"] is True and ev["before"] == ev["after"] != "-"


def test_products_preserved_deletes_a_file_that_only_appeared(tmp_path):
    """本来**不存在**的文件，跑完被创建出来了 → 删掉。

    留着它的话，一个「实验的副作用」会冒充成一份真产物 ——
    而报告的读者没有任何办法分辨。
    """
    ghost = tmp_path / "final_selfcheck.md"
    with vk.ProductsPreserved([ghost]) as guard:
        ghost.write_text("凭空出现\n", encoding="utf-8")

    assert not ghost.exists()
    assert guard.evidence[0]["before"] == "-" == guard.evidence[0]["after"]


def test_products_preserved_reports_a_restore_it_could_not_do(tmp_path):
    """★ 负控：**还原不了要说出来**，而且不许把异常抛给调用方。

    还原发生在子进程跑完之后。它可能失败（文件被占住、只读、盘满），
    也可能压根没轮上（进程被强杀）—— 那时候「已还原」就是一句假话，
    而报告里那行 `sha256 跑之前 == 跑完` 会替这句假话作证。
    所以这里把文件设成只读，逼出一次真的还原失败：证据必须写着 `restored=False`
    并带上错误类型，且 `with` 照常退出（调用方原本的异常不能被盖掉）。
    """
    a = tmp_path / "verify.md"
    a.write_text("原样\n", encoding="utf-8")
    try:
        with vk.ProductsPreserved([a]) as guard:
            a.chmod(stat.S_IREAD)              # 只读 —— 之后就写不回去了
            a.chmod(stat.S_IREAD | stat.S_IWRITE)
            a.write_text("改过了\n", encoding="utf-8")
            a.chmod(stat.S_IREAD)
        ev = guard.evidence[0]
        assert ev["restored"] is False, f"写不回去却报成还原成功：{ev}"
        assert ev["error"], "还原失败却没留下错误类型"
    finally:
        a.chmod(stat.S_IREAD | stat.S_IWRITE)   # 别给 pytest 留一个删不掉的临时文件


# ---------------------------------------------------------------- 命中渲染
def test_formatting_a_nonempty_hit_list_works():
    """★ 正控：**非空**命中列表必须能渲染出来。

    这条是为一个真事故补的：渲染代码原先长在 `experiments/26_final_selfcheck.py`
    里，用的是 `h.file` / `h.line`，而 `Hit` 的属性叫 `rel` / `line_no`。
    列表推导在 `hits` 为空时**根本不会求值**，所以那个错一直没发作 ——
    直到「报告不该出现的数字」这一项真的命中了 3 处，
    于是这一项在它唯一该报红的那一刻崩成了 ERROR。

    教训：只喂空列表的测试，测不到「有东西要报」的那条路径。
    """
    # 样本刻意选一个**不在任何词表里**的串：这一条测的是「位置 + 片段」这个格式，
    # 掩码另有它自己的一条测试。
    hits = [sc.Hit("reports/a.md", 3, "qqq", "这一行有 qqq")]
    lines = vk.format_hit_lines(hits)
    assert lines == ["reports/a.md:3 这一行有 qqq"], lines


@needs_wordlist
def test_formatted_hits_are_masked():
    """渲染出来的行必须已经掩码 —— 报告不能逐字引用它报出来的东西。

    样本从词表里取，**不写死字面量**：一来本仓禁的正是把这些词写进产物，
    二来这样测的就是「词表里任意一个词都能被掩掉」，而不是碰巧那一个。
    """
    word = sc.FORBIDDEN_NUMBERS[0]
    line = vk.format_hit_lines([sc.Hit("reports/a.md", 1, word, f"{word} 出现了")])[0]
    assert word not in line, f"掩码没生效：{line}"
    assert sc.MASK in line


def test_format_hit_lines_respects_the_limit():
    hits = [sc.Hit(f"reports/{i}.md", i, "x", "x") for i in range(30)]
    assert len(vk.format_hit_lines(hits, limit=5)) == 5
    assert len(vk.format_hit_lines(hits)) == 20, "默认上限不是 20"


@needs_wordlist
def test_a_hit_row_can_poison_its_own_report_and_the_helper_stops_it():
    """自毒回路的第三条入口：**命中行里的行号**。

    报告渲染命中行时写的是 `文件:行号`，而行号本身可能是被禁数字
    （词表里有几个正是典型行号）。于是「命中恰好落在第 N 行」时，
    引用它的报告自己就成了新的载体 —— 而且行号会随文件改动挪位，
    所以这种红是**偶尔出现、重跑就好**的那种。

    样本从词表里**现取**：本仓禁的正是把那些字面量写进文件，
    连测试文件也在扫描范围内（`SCOPE_AUTHORED`）。
    """
    lit = [n for n in sc.FORBIDDEN_NUMBERS if n.isdigit() and len(n) <= 4][0]
    poisoned = f"reports/some.md:{lit} 命中「某词」"

    # 正控：这个样本必须真的能被扫出来，否则下面那条断言什么也没证明
    assert sc.scan_report_carrier(poisoned), "样本没被扫出来，这条测试是空的"

    fixed, carrier = vk.finalize_report_text(poisoned, ROOT / "reports" / "verify.md")
    assert carrier and lit not in fixed, f"没掩掉：{fixed!r}"
    assert not sc.scan_report_carrier(fixed), f"掩码之后还带着污染：{fixed!r}"


@needs_wordlist
def test_a_registered_coincidence_is_not_erased_from_its_own_report():
    """负控的另一半：**登记过的巧合不许被掩掉**。

    掩码是「宁可多掩」的反面 —— 一律掩码会擦掉一个**真结果**
    （`reports/pair_test.md` 里那个精确 p 值就是自己算出来的，不是抄来的）。
    所以豁免是**按路径**给的：同一段文本换个文件就不放行。
    """
    entry = sc.VERIFIED_COINCIDENCES[0]
    text = f"p = {entry['literal']}"

    kept, carrier = vk.finalize_report_text(text, ROOT / entry["paths"][0])
    assert entry["literal"] in kept, "登记过的字面量在自己的报告里被掩掉了"
    assert not carrier

    # 同一个字面量换到一个**没登记**的文件里 → 照掩不误
    moved, carrier2 = vk.finalize_report_text(text, ROOT / "reports" / "别处.md")
    assert entry["literal"] not in moved and carrier2, "豁免跑到别的文件里去了"
