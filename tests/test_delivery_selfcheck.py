"""交付级自检那 15 项里，**判据反直觉的那几条**得有人钉住。

这个文件不重复测「扫出来是空的」——那类用例在本仓别处已经有了。
它只测四件容易在改动中被悄悄改坏的事：

1. **第 6 项的判据是「所有 `N 个工具` 都必须等于 6」**，不是「出现过 `6 个工具`」。
   后者在「6 个……8 个」同时出现时照样通过 —— 那正是这一项要防的情况。
2. **第 15 项要求命中数 > 0**。它守的是「没在没人同意的情况下重写历史」，
   所以「历史很干净」在这一项上**是失败**。判据写反了就完全失效，
   而这种判据写反了不会报错，只会一直绿。
3. **交付物不在本机时判 SKIP，不判 PASS**。这是全仓最容易被改成 PASS 的地方：
   把交付物那几个函数 `monkeypatch` 成「读不到 → 返回空命中」，
   如果实现里没有那条 `pending` 分支，就会静默变成「0 命中 = 干净」。
4. **选择器挑的是「带当前标签的那一份」**。上面第 3 条的那些用例把选择器整个换掉了，
   所以选择器自己长期没有覆盖：标签散在多处时，换一版就会出现
   「检查照跑、查的却是上一份」，而且照样报绿。见文件末尾那三条用例。

文件都在 `tmp_path` 里造，**不碰真的交付物**（它不在仓库里，也不该在）。
"""
from __future__ import annotations

import pytest

from mcp_server import delivery_selfcheck as dsc
from mcp_server import paths
from mcp_server import selfcheck as sc

# 第 8 项要拿「本仓地址」跟交付物里的链接比。地址现在**不写死在代码里**了，
# 所以用例把它钉成一个假地址 —— 不钉的话，这些用例会跟着本机的 `origin` 走：
# 换一次远端地址，它们的结果就跟着变，而那是**被测量的东西在动**，不是判据在动。
FAKE_SLUG = "someone/some-repo"


def _pin_repo(monkeypatch) -> str:
    """把本仓地址钉成假的，返回它对应的网页地址。"""
    monkeypatch.setenv(paths.ENV_REPO_URL, FAKE_SLUG)
    url, why = paths.repo_web_url()
    assert url and not why, why
    return url


def _fake_resume(monkeypatch, tmp_path, text: str):
    p = tmp_path / f"某简历-{dsc.RESUME_TAG}.html"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setattr(dsc, "resume_html", lambda: (p, ""))


def _fake_pdf(monkeypatch, tmp_path, pages: int):
    p = tmp_path / f"某简历-{dsc.RESUME_TAG}.pdf"
    p.write_bytes(b"".join(b"/Type /Page\n" for _ in range(pages))
                  + b"/Type /Pages\n/Count %d\n" % pages)
    monkeypatch.setattr(dsc, "resume_pdf", lambda: (p, ""))


# --------------------------------------------------------------- 第 6 项

@pytest.mark.parametrize("body,status", [
    ("<p>共 6 个工具，3 个 scope</p>", dsc.PASS),
    ("<p>共 8 个工具</p>", dsc.FAIL),
    # ★ 两个都出现 —— 「出现过 6 个工具」那种写法会放它过去，这里必须 FAIL
    ("<p>共 6 个工具</p><p>另有 8 个工具</p>", dsc.FAIL),
    ("<p>一个都没写</p>", dsc.FAIL),
])
def test_tool_count_must_be_exactly_six_everywhere(monkeypatch, tmp_path,
                                                   body, status):
    _fake_resume(monkeypatch, tmp_path, body)
    item = dsc.check_6_tool_count()
    assert item.status == status, (item.status, item.note, item.hits)


# -------------------------------------------------------------- 第 15 项

def test_history_residue_passes_when_residue_is_still_there(monkeypatch):
    """真实仓库：修复前的提交还在、那份文件里那些字面量还查得到 → PASS。

    ★ 这一条**必须有词表**才有意义：扫描用的是词表那三张表，词表读不到时
    扫什么都是 0 命中，于是「命中 > 0 才算过」这条判据会把一个**空扫描**
    读成「历史已经被重写过」。clone 下来（词表按设计不在仓库里）就是这个情形 ——
    所以这里判 SKIP，不是红：**没有实测依据**，既不是通过也不是失败（D-44）。

    ★ 第二个 SKIP 口子是**迁仓之后**才有的（D-54）：新仓的历史自一条初始提交
    起算，那段旧历史根本不在新 clone 的机器上，于是「那一版里还读得出那些字」这件事
    **没有对象可测** —— 被测函数这时判 SKIP（照实说明理由），本条用例跟着判 SKIP。
    条件写得**很窄**：只有「本机取不回那一版」**且**「本机没有一条 `legacy/…`
    分支」（那段旧历史不在本机）才跳。维护者本机留着那条分支，它照样必须 PASS，
    所以将来若有人把第 15 项改成「永远 SKIP」，这一条仍然会红。

    ★ 那个条件改过一次：原来问的是「本机提交笔数 ≤ 1」。**笔数会随新仓自己长**，
      于是新仓攒到第二笔之后，每一台新 clone 的机器都会落进 FAIL 那一支，
      挨一句「历史被重写过」—— 不实且严重，而且不需要谁改代码它就会自己开始误报。
      现在是问证据（本机还留没留着那段旧历史）。两个分支各自另有
      `test_history_residue_is_skip_when_this_clone_has_only_the_new_history` 与
      `test_history_residue_fails_when_the_old_history_is_here_but_the_object_is_gone`
      钉住；`not pre` 那一支由
      `test_history_residue_is_skip_when_the_old_history_is_not_here` 钉住。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：扫出来必然是 0 命中，本项没有实测依据")
    v27 = _patched_27(monkeypatch)
    pre, _ = paths.pre_fix_commit()
    if pre and not v27.pre_fix_text(pre) and not v27.legacy_history_refs():
        pytest.skip("本机没有一条 `legacy/…` 分支（新 clone 的正常状态）："
                    "那段旧历史不在本机，这一版取不回来 —— "
                    "本项没有实测依据，既不是通过也不是失败")
    item = dsc.check_15_history_residue()
    assert item.status == dsc.PASS, (item.status, item.note, item.hits)
    assert "命中" in item.note and "只读" in item.note


def test_history_residue_fails_when_the_residue_is_gone(monkeypatch):
    """★ 反向：把扫描结果按成空（等价于「历史已经被重写过」）→ 必须 FAIL。

    这一条是第 15 项存在的全部意义。没有它的话，
    「判据写反」和「历史真的被重写了」在报告上都表现为一个绿字。

    ★ 但这条**必须有词表**才构造得出来：词表读不到时，「0 命中」有两种解释
    （历史被重写 / 根本没词表），而第 15 项这时判的是 SKIP ——
    本条要模拟的是前者，构造不出来就判 SKIP，不是红。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：0 命中这时无法区分「重写过」与「没词表」，"
                    "本项没有实测依据")
    v27 = dsc._load_27()
    monkeypatch.setattr(v27, "scan", lambda _t: [])
    # ★ 还要把加载器也换掉：`_load_27()` 每次都会**从磁盘重新执行**那个脚本，
    #   只补丁上一个实例的话，被测函数拿到的是另一个没被补丁的实例 ——
    #   于是这条用例会「通过」，而它其实什么都没验证。
    monkeypatch.setattr(dsc, "_load_27", lambda: v27)
    item = dsc.check_15_history_residue()
    assert item.status == dsc.FAIL, (item.status, item.note)
    assert item.hits


# ------------------------------------------------------- 词表读不到时

@pytest.mark.parametrize("fn", [dsc.check_1_names_in_repo,
                                dsc.check_2_numbers_in_repo,
                                dsc.check_3_terms_in_repo,
                                dsc.check_5_resume_stale,
                                dsc.check_15_history_residue])
def test_wordlist_missing_means_skip_never_pass(monkeypatch, fn):
    """词表读不到 → `sc.grep()` 返回空 → 命中 0。**这时候必须判 SKIP。**

    这是把词表搬出仓库之后新长出来的坑（`DECISIONS.md` D-46）：
    「没查」会被一个空的命中列表伪装成「查过了，干净」。

    ★ 第 15 项在这个坑里**摔得更狠**：它的判据是反的（命中 > 0 才算过），
    所以同一个空的命中列表会把它推成 **FAIL**，而且注释指向
    「历史被重写过」—— 一个不实且严重的结论。加进这张表就是为了钉住它。
    """
    monkeypatch.setattr(sc, "WORDLIST_AVAILABLE", False)
    monkeypatch.setattr(sc, "WORDLIST_REASON", "词表未随仓分发（见维护者本地）")
    item = fn()
    assert item.status == dsc.SKIP, (item.status, item.n_hits, item.hits)
    assert "没有实测依据" in item.note


def test_history_residue_is_skip_when_the_old_history_is_not_here(monkeypatch):
    """★ 新仓的正常状态：本机没有那段旧历史 → 判 SKIP，**不判 FAIL**。

    以前这种情况判 FAIL，而 FAIL 的措辞是「那个提交也不在了 —— 历史被重写过」。
    对一台新 clone 的机器来说那是一句**不实且严重**的指控：它什么也没做，
    只是那段历史不在它本机。这一条就是钉住那个分支的。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：负控甲没有样本可用")
    monkeypatch.setattr(dsc.paths, "pre_fix_commit",
                        lambda: (None, "仓外留档不在本机（测试里钉的）"))
    monkeypatch.setattr(dsc.paths, "pre_fix_label", lambda: (None, "同上"))
    item = dsc.check_15_history_residue()
    assert item.status == dsc.SKIP, (item.status, item.note, item.hits)
    assert "没有实测依据" in item.note
    # ★ 负控甲那一条腿**仍然要真的跑过** —— 不能因为乙没有依据就整项躺平
    assert "合成负控通过" in item.note


def test_history_residue_is_skip_when_this_clone_has_only_the_new_history(monkeypatch):
    """★ 新 clone 的正常状态：本机**没有**任何引用落在 `main` 历史之外 → SKIP。

    这条用例钉的正是那个「会自己开始误报」的坑：判据原来问「本机提交笔数 > 1」，
    而新仓的历史自己会长 —— 攒到第二笔之后，每一台新 clone 的机器都会落进 FAIL
    那一支挨一句「历史被重写过」，尽管它只是 `git clone` 了一下。
    所以这里把笔数按成 **5 笔**（改判据之前这一条必红）而把 `legacy/…` 按成空：
    本项必须判 SKIP，而不是 FAIL。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：负控甲没有样本可用")
    v27 = _patched_27(monkeypatch)
    monkeypatch.setattr(dsc.paths, "pre_fix_commit",
                        lambda: ("测试桩-占位，不是真编号", "测试里钉的"))
    monkeypatch.setattr(dsc.paths, "pre_fix_label", lambda: ("旧编号·F", "同上"))
    monkeypatch.setattr(v27, "pre_fix_text", lambda _id: "")
    monkeypatch.setattr(v27, "legacy_history_refs", lambda: [])
    monkeypatch.setattr(v27, "_rev_count", lambda: 5)
    item = dsc.check_15_history_residue()
    assert item.status == dsc.SKIP, (item.status, item.note, item.hits)
    assert "没有实测依据" in item.note
    assert "历史被重写过" in item.note          # 只是「不等于」它，不是判了它
    # ★ 负控甲那一条腿**仍然要真的跑过** —— 乙没有依据不等于整项躺平
    assert "合成负控通过" in item.note


def test_history_residue_fails_when_the_old_history_is_here_but_the_object_is_gone(
        monkeypatch):
    """★ 本机**确实还留着那段旧历史**（`legacy/…` 分支还在），却取不回那一版 → FAIL。

    钉的是那条 FAIL 分支的**条件**：证据是「本机还留着那段旧历史的分支」，
    不是「本机提交笔数 > 1」。这条分支要是没人钉，把条件写松（或写反）都看不出来 ——
    而它正是「不许在没人同意的时候重写历史」这条判据的牙。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：本项这时判 SKIP，走不到这条分支")
    v27 = _patched_27(monkeypatch)
    monkeypatch.setattr(dsc.paths, "pre_fix_commit",
                        lambda: ("测试桩-占位，不是真编号", "测试里钉的"))
    monkeypatch.setattr(dsc.paths, "pre_fix_label", lambda: ("旧编号·F", "同上"))
    monkeypatch.setattr(v27, "pre_fix_text", lambda _id: "")
    monkeypatch.setattr(v27, "legacy_history_refs",
                        lambda: ["refs/heads/legacy/history-测试桩"])
    item = dsc.check_15_history_residue()
    assert item.status == dsc.FAIL, (item.status, item.note, item.hits)
    assert item.hits and "历史被重写过" in item.hits[0]
    assert "那段旧历史的分支" in item.note


def test_history_residue_fails_when_the_ruler_itself_is_broken(monkeypatch):
    """★ 两条腿里**甲**是底线：甲不报红，那几个 0 就什么都证明不了。

    这一条要是判 PASS，就等于「尺子坏了」被读成「仓库很干净」——
    整套检查最想防的就是这一种。
    """
    if not sc.WORDLIST_AVAILABLE:
        pytest.skip("词表不在本机（仓外文件）：负控甲没有样本可用")
    v27 = _patched_27(monkeypatch)
    monkeypatch.setattr(v27, "synthetic_negative_control",
                        lambda: {"status": "FAIL", "n_kinds": 0, "n_occur": 0,
                                 "rows": [], "note": "合成样本一条都没报出来"})
    item = dsc.check_15_history_residue()
    assert item.status == dsc.FAIL, (item.status, item.note)
    assert "尺子当场失灵" in item.hits[0]


# ------------------------------------------------------- 第 4 项（联网）

def _patched_27(monkeypatch):
    """拿到 27 那个模块，并把 `dsc._load_27` 钉在**同一个实例**上。

    ★ 两个都要补：`_load_27()` 每次从磁盘重新执行那个脚本，
      只补丁一个实例的话，被测函数拿到的是另一个没被补丁的实例。
    """
    v27 = dsc._load_27()
    if v27 is None:
        pytest.skip("取不到 experiments/27_leak_fix_verify.py")
    monkeypatch.setattr(dsc, "_load_27", lambda: v27)
    return v27


def _fake_27(monkeypatch, result):
    """把 27 的取回动作换成给定结果。返回那个模块（补丁打在它身上）。"""
    v27 = _patched_27(monkeypatch)
    monkeypatch.setattr(v27, "fetch_remote",
                        lambda url, timeout=30: result)
    return v27


def test_remote_fetch_reads_http_200_as_success(monkeypatch):
    """★ 这一族是「第一次真跑才发现判据写反」的那一处。

    `fetch_remote` 成功时给的是 **HTTP 200 + bytes**。按「0 + str 才算成功」
    判的话，**取回成功反被读成「没取到」** —— `--online` 第一次真跑就踩了这个。
    所以这里把成功的那一侧也钉住：只钉失败那一侧的话，写反了照样能全绿。
    """
    _fake_27(monkeypatch, (200, b"clean text", "curl.exe"))
    hits, note = dsc.fetch_remote_hits()
    assert hits == [], (hits, note)
    assert "200" in note


def test_remote_fetch_treats_a_non_200_as_not_fetched(monkeypatch):
    _fake_27(monkeypatch, (404, "HTTPError 404", "urllib"))
    hits, note = dsc.fetch_remote_hits()
    assert hits is None, note
    assert "没取到" in note


def test_not_fetched_is_error_never_pass(monkeypatch):
    """取不到 ≠ 干净。这条要是判 PASS，就是用「没查」冒充「查过了」。"""
    monkeypatch.setenv(paths.ENV_REPO_URL, FAKE_SLUG)      # 地址是有的，只是取不到
    assert dsc.check_4_public_raw(
        True, {"remote_hits": None, "remote_note": "x"}).status == dsc.ERROR
    assert dsc.check_4_public_raw(
        True, {"remote_hits": [], "remote_note": "curl.exe（HTTP 200）"},
    ).status == dsc.PASS


@pytest.mark.parametrize("fn", [dsc.check_4_public_raw, dsc.check_13_remote_sha])
def test_online_items_skip_when_the_address_cannot_be_resolved(monkeypatch, fn):
    """★ 地址解析不出来 → SKIP，**不是** ERROR、更不是 PASS。

    这两种误判的代价不一样，所以必须分开：判 ERROR 的措辞是「没读到」，
    会让人去查网络 —— 而这里根本没有地址可查；判 PASS 更糟，等于
    「一个都对不上，所以没问题」。
    """
    monkeypatch.setattr(dsc.paths, "repo_slug",
                        lambda: (None, "解析不到本仓地址（测试里钉的）"))
    item = fn(True, {"remote_hits": None, "remote_note": "x",
                     "local_head": "a", "remote_sha": "b"})
    assert item.status == dsc.SKIP, (item.status, item.note)
    assert "没有实测依据" in item.note


def test_remote_sha_not_read_is_still_error_when_the_address_does_resolve(monkeypatch):
    """反向：地址**有**、只是没读到 → 仍然是 ERROR（不是 SKIP）。

    只钉住「地址没有 → SKIP」那一侧的话，把两种情况合并成一种的实现
    照样能全绿 —— 而那正是这条判据分两档的意义所在。
    """
    monkeypatch.setenv(paths.ENV_REPO_URL, FAKE_SLUG)
    item = dsc.check_13_remote_sha(True, {"local_head": "a", "remote_sha": ""})
    assert item.status == dsc.ERROR, (item.status, item.note)
    assert "没读到" in item.note


def test_item_13_publishes_a_fingerprint_not_the_commit_id(monkeypatch):
    """★ 判据行不许写出提交号 —— 连前 7 位也不行，报告是**公开产物**。

    7 位缩写在托管站点上照样解析回对象，所以「只写缩写」不构成遮挡：
    写进去就是把钥匙挂在墙上，而本轮刚把 22 个这样的编号搬去仓库外面（D-54）。
    这一条钉的是**产物里出现了什么**，不是 `sha_fingerprint` 自己 ——
    改回 `local[:7]` 的实现在这里必须红。
    """
    monkeypatch.setenv(paths.ENV_REPO_URL, FAKE_SLUG)
    local, remote = "1" * 40, "2" * 40
    item = dsc.check_13_remote_sha(True, {"local_head": local, "remote_sha": remote})
    assert item.status == dsc.FAIL and item.hits, item
    for text in (item.note, *item.hits):
        assert local[:7] not in text and remote[:7] not in text, text
        assert local not in text and remote not in text, text
    # 换写法之后判定能力不能一起丢：一致时仍然要判 PASS
    same = dsc.check_13_remote_sha(True, {"local_head": local, "remote_sha": local})
    assert same.status == dsc.PASS, same


def test_section_renders_all_fifteen_and_explains_skip():
    items = dsc.run(deps={"pytest_rc": 0, "pytest_n": "543 条通过",
                          "gate_rc": 0, "steps_note": "x"}, online=False)
    assert [i.n for i in items] == [str(n) for n in range(1, 16)]
    text = dsc.section(items)
    assert "| 15 |" in text and "「没查」不许写成「0 命中」" in text
    # ★ 引用了另一份报告，措辞里必须带「将由 / 生成后」这类标记 ——
    #   否则 `25_deliverable_check.py` 的 G7（文档引用了不存在的产物）会红。
    assert "由**" in text or "生成后" in text


# ------------------------------------------------------- 第 8 / 14 项

def _skeleton(mcp_body: str = "", others: str = "", intern: str = "") -> str:
    """搭一个最小但**结构正确**的简历骨架：两个 `<h2>` 段，条目用 `<h3>`。"""
    return ("<h2>项目经历</h2>"
            f"<div class='job'><h3>MCP Agent 工具层</h3>{mcp_body}</div>"
            f"{others}"
            "<h2>实习经历</h2>" + intern)


def test_facts_link_must_not_point_elsewhere(monkeypatch, tmp_path):
    url = _pin_repo(monkeypatch)
    _fake_resume(monkeypatch, tmp_path, _skeleton(
        f"<a href='{url}'>a</a>"
        "<a href='https://github.com/someone/other-repo'>b</a>"))
    item = dsc.check_8_facts_link()
    assert item.status == dsc.FAIL and "other-repo" in item.hits[0]

    _fake_resume(monkeypatch, tmp_path, _skeleton(f"<a href='{url}'>a</a>"))
    assert dsc.check_8_facts_link().status == dsc.PASS


def test_facts_link_may_be_written_without_the_scheme(monkeypatch, tmp_path):
    """简历里写的是 `github.com/…` 这种省掉 `https://` 的形态（排版更短）。

    只认带协议头的写法 → 判「一处都没有」→ 人去改一份本来就对的交付物。
    反向也要钉住：不带协议头的**别的**仓库照样得抓出来。
    """
    url = _pin_repo(monkeypatch)
    bare = url.removeprefix("https://")
    _fake_resume(monkeypatch, tmp_path, _skeleton(f"<p>代码：{bare}（公开示例库）</p>"))
    item = dsc.check_8_facts_link()
    assert item.status == dsc.PASS, (item.status, item.hits)

    _fake_resume(monkeypatch, tmp_path,
                 _skeleton("<p>见 github.com/someone/other-repo</p>"))
    item = dsc.check_8_facts_link()
    assert item.status == dsc.FAIL and "other-repo" in item.hits[0]


def test_other_blocks_may_link_their_own_public_repo(monkeypatch, tmp_path):
    """★ 范围是 MCP 那一条，不是整份简历。

    交付物上本来就有另一个项目的公开仓库链接。整份扫会把它判红，
    而它判红之后人只能去删一个本来就对的链接。
    """
    url = _pin_repo(monkeypatch)
    _fake_resume(monkeypatch, tmp_path, _skeleton(
        f"<p>{url}</p>",
        others="<div class='job'><h3>另一个项目</h3>"
               "<p>github.com/someone/other-project</p></div>"))
    assert dsc.check_8_facts_link().status == dsc.PASS


def test_facts_link_skips_when_the_repo_address_is_unresolvable(monkeypatch,
                                                                tmp_path):
    """★ 地址解析不出来时判 SKIP。

    这一条是这一轮新长出来的：地址原来写死在代码里，永远不会「解析不出来」。
    改成「环境变量 → `origin` → 仓外留档」之后就多了这个状态，而它**必须**
    判 SKIP —— 判 PASS 等于说「一个链接都没对上，所以没问题」，
    判 FAIL 会让人去改一份本来就对的交付物。
    """
    _fake_resume(monkeypatch, tmp_path, _skeleton("<p>随便什么</p>"))
    monkeypatch.setattr(dsc.paths, "repo_web_url",
                        lambda: (None, "解析不到本仓地址（测试里钉的）"))
    item = dsc.check_8_facts_link()
    assert item.status == dsc.SKIP, (item.status, item.note)
    assert "没有实测依据" in item.note


# ---------------------------------------------------------------- 第 7 项

def test_intern_section_must_not_say_mcp_but_the_mcp_block_may(monkeypatch, tmp_path):
    """★ 判据的范围是**实习那一节**。

    MCP 条的标题与技术栈本来就写着 `MCP`；按 ` · MCP` 这种形状去躲也没用 ——
    真实交付物里 MCP 条的技术栈正是 `Python · MCP 官方 SDK`，形状一模一样。
    """
    _fake_resume(monkeypatch, tmp_path, _skeleton(
        mcp_body="<div class='stack'>技术栈：Python · MCP 官方 SDK</div>",
        intern="<div class='stack'>技术栈：Python · Milvus Lite</div>"))
    assert dsc.check_7_tech_stack().status == dsc.PASS

    _fake_resume(monkeypatch, tmp_path, _skeleton(
        mcp_body="<div class='stack'>技术栈：Python · MCP 官方 SDK</div>",
        intern="<div class='stack'>技术栈：Python · MCP · Milvus Lite</div>"))
    item = dsc.check_7_tech_stack()
    assert item.status == dsc.FAIL and item.hits


def test_intern_section_missing_is_a_failure_not_a_pass(monkeypatch, tmp_path):
    """结构变了（找不到那一节）→ 判红，**不是**放过。"""
    _fake_resume(monkeypatch, tmp_path, "<h2>项目经历</h2><h3>MCP</h3>")
    assert dsc.check_7_tech_stack().status == dsc.FAIL


def test_resume_may_name_the_employer_but_not_the_old_repo(monkeypatch, tmp_path):
    """★ 第 5 项认的是**仓库标识**，不是整张名字表。

    词表第 1 张表的头几条是雇主名与内部系统名，而简历上写雇主是必须的
    （不写就没法核实这段经历）。拿整张表去扫简历，这一项永远红。

    哪些算「仓库标识」由词表自己划（`RESUME_FORBIDDEN_NAMES`）——
    这张小名单**不能写进仓库**，否则仓库又得把那些名字抄一遍，
    那正是 D-46 修掉的明文桥。所以这里从词表取，不从仓库取。
    """
    if not sc.WORDLIST_AVAILABLE or not sc.RESUME_FORBIDDEN_NAMES:
        pytest.skip("词表不在本机：本项没有实测依据")
    allowed = [x for x in sc.FORBIDDEN_NAMES
               if x not in set(sc.RESUME_FORBIDDEN_NAMES)]
    if not allowed:
        pytest.skip("词表把整张名字表都划给了交付物，这条用例无从构造")

    _fake_resume(monkeypatch, tmp_path, f"<p>{allowed[0]} · 实习生</p>")
    assert dsc.check_5_resume_stale().status == dsc.PASS

    _fake_resume(monkeypatch, tmp_path, f"<p>{sc.RESUME_FORBIDDEN_NAMES[0]}</p>")
    assert dsc.check_5_resume_stale().status == dsc.FAIL


@pytest.mark.parametrize("pages,status", [(2, dsc.PASS), (3, dsc.FAIL),
                                          (1, dsc.FAIL)])
def test_pdf_page_count(monkeypatch, tmp_path, pages, status):
    _fake_pdf(monkeypatch, tmp_path, pages)
    assert dsc.check_14_pdf_pages().status == status


# ------------------------------------------------- 第 4 件：选择器本身挑哪一份

# 诱饵：故意**不是**当前标签的那一版。它就是下面第一条用例要排除的那一份。
OTHER_TAG = "4.0"


def _one_version(tmp_path, tag: str) -> None:
    """在这个目录里造出「带某个标签的一版交付物」（HTML 与 PDF 各一份）。"""
    (tmp_path / f"某简历-{tag}.html").write_text("<p>占位</p>", encoding="utf-8")
    (tmp_path / f"某简历-{tag}.pdf").write_bytes(b"/Type /Page\n")


def _point_at(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(dsc.ENV_RESUME_DIR, str(tmp_path))


def test_selector_picks_the_current_tag_when_two_versions_coexist(monkeypatch, tmp_path):
    """★ 两版并存时，选中的必须是**带当前标签**的那一份。

    这就是本轮修掉的那个坑：这一组检查按文件名认交付物，标签写死在多处，
    换了一版之后选择器仍然挑中上一份 —— 检查照跑、报告照样绿，从报告上看不出异常。

    本用例**不 monkeypatch 选择器**：上面那些用例把 `resume_html`/`resume_pdf`
    整个换掉了，所以选择器本身一直没有人测过，这正是缺口所在。
    """
    assert OTHER_TAG != dsc.RESUME_TAG, \
        "诱饵跟当前标签撞了 —— 这条用例会静默退化成「两个候选」，先改诱饵"
    _one_version(tmp_path, OTHER_TAG)
    _one_version(tmp_path, dsc.RESUME_TAG)
    _point_at(monkeypatch, tmp_path)

    p, why = dsc.resume_html()
    assert why == ""
    assert p is not None and dsc.RESUME_TAG in p.name and OTHER_TAG not in p.name

    q, why_pdf = dsc.resume_pdf()
    assert why_pdf == ""
    assert q is not None and dsc.RESUME_TAG in q.name and OTHER_TAG not in q.name


def test_selector_reports_when_no_version_matches(monkeypatch, tmp_path):
    """一份都不匹配 → 报「找不到」，**不许**静默取空。

    （口径同词表那条：读不到不等于 0 命中。）
    """
    _one_version(tmp_path, OTHER_TAG)
    _point_at(monkeypatch, tmp_path)
    p, why = dsc.resume_html()
    assert p is None
    assert dsc.RESUME_TAG in why


def test_selector_reports_when_two_versions_match(monkeypatch, tmp_path):
    """「恰好一个」这条语义不许放宽：多出一份同样要报错。"""
    _one_version(tmp_path, dsc.RESUME_TAG)
    (tmp_path / f"另一份-{dsc.RESUME_TAG}.html").write_text("<p>占位</p>",
                                                           encoding="utf-8")
    _point_at(monkeypatch, tmp_path)
    p, why = dsc.resume_html()
    assert p is None
    assert dsc.RESUME_TAG in why
