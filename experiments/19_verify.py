"""台账式自检：把「这个仓库现在是什么状态」变成一张可核对的表。

## 它和 `pytest` 的分工

`pytest` 管「某个函数对不对」——细、快、每次提交都跑。
本脚本管「整个仓库作为一个交付物对不对」——粗、慢、需要**真实地跑一遍**：

- 每一条红线**实际命中几处**（不是「通过」两个字）；
- 每个入口脚本**实际退出几**，和它声明的期望码比；
- `pytest` 自己**实际退出几**；
- 产物之间有没有互相矛盾（比如「未完成」的牌子挂着、结果文件却也躺着）。

**只报「通过」的检查是没法核对的。** 所以这张表里每一行都带三类信息：
口径（这条检查凭什么这么判）、实测（看到了什么）、判定。

## 判定逻辑在 `mcp_server/verify_kit.py`

本文件以数字开头、**没法被 import**，所以「怎么判」全在 `verify_kit` 里，
那里能被单元测试直接钉住。本文件只负责有副作用的那一半：
起子进程、收退出码、拼表、落盘。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | FAIL 0 / ERROR 0 |
| 1 | 有 FAIL（仓库状态不对） |
| 2 | 有 ERROR（自检自己出问题了，比如某个脚本跑不起来） |
| 4 | 用法错误 |
| 5 | 自检自身的负控不通过（`--selftest`） |
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402
from mcp_server import selfcheck as sc, verify_kit as vk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

E_OK = 0
E_FAIL = 1
E_ERROR = 2
E_USAGE = 4
E_SELFTEST = 5

PASS, FAIL, SKIP, ERROR = vk.PASS, vk.FAIL, vk.SKIP, vk.ERROR


def _run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    """跑一条命令，返回 `(退出码, 输出尾部)`。跑不起来返回码 -1。

    退出码 -1 会被判成 ERROR 而不是 FAIL —— 「命令没跑起来」和
    「命令跑了但结果不对」是两种问题，修的人不一样。

    ★ 尾部在这里就掩码（`vk.tail_for_report`），因为**这个函数的返回值只有
    一个用途：抄进报告**。原来只在 `judge_exit` 里掩码，于是不走台账的那几条
    （比如 P1 直接拼 `退出码 {got} | {tail}`）就把原始输出抄了进去 ——
    `pytest` 尾巴里的耗时因此进了 `reports/verify.md`，
    每跑一次闸门那个文件就显示「已修改」。收在一处，就没有下一个漏的。

    ★ `limit=300` 交给它去截，**不要在外面写 `[:300]`**：先截断会把一条绝对路径
    切成半截，半截路径掩码认不出、检查也认不出（见 `tail_for_report` 的说明）。
    """
    try:
        p = subprocess.run([sys.executable, *cmd], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）"
    except OSError as e:
        return -1, f"起不来：{type(e).__name__}: {e}"
    lines = (p.stdout or "").strip().splitlines()[-2:]
    errs = (p.stderr or "").strip().splitlines()
    return p.returncode, vk.tail_for_report(" / ".join(lines + errs[-2:]), limit=300)


# ---------------------------------------------------------------- 静态检查
def static_checks(root: Path | None = None) -> list[Result]:
    """四类红线 + 三个卫生项。全部委托 `mcp_server/selfcheck.py`。"""
    r = sc.ROOT if root is None else root
    out: list[vk.Result] = []

    def one(cid, title, caliber, hits, verified=(), pending=""):
        """把一批命中收成一行结果。**命中内容一律掩码**，见 `sc.mask_all()`：

        报告里逐字写出命中的字符串，报告自己就成了新的载体 ——
        下一轮扫描会在 `reports/verify.md` 里**再次**命中它（实测发生过）。
        所以这里只写位置 `文件:行号` 和掩码后的片段，想看原文按位置打开文件。

        `verified` 是**已核验的巧合**（`sc.VERIFIED_COINCIDENCES`）：
        它们照旧列出来，但不算违规，而且要在 detail 里单独报一个数 ——
        直接滤掉的话，命中数写 0，读的人会以为从来没撞过车。

        `pending` 非空表示这一项**没资格下结论**（比如词表不在本机）——
        判 `SKIP`，而且 detail 里写清楚为什么。**不是 PASS**：把「没查」
        写成「0 命中」，是这一整套检查最想防的那种谎。
        """
        def _row(h, kind):
            d = h.as_dict() if hasattr(h, "as_dict") else {"file": str(h)}
            m = {k: (sc.mask_all(v) if k in ("needle", "text") else v)
                 for k, v in d.items()}
            m["判定"] = kind
            return m

        if pending:
            out.append(vk.Result(cid, title, caliber, SKIP,
                                 f"{pending} —— 本项**没有实测依据**，不是通过"))
            return

        rows = ([_row(h, "真命中") for h in hits]
                + [_row(h, "已核验巧合") for h in verified])
        detail = f"命中 {len(hits)} 处" if hits else "0 命中"
        if verified:
            detail += (f"（另有 {len(verified)} 处已核验巧合，不算违规；"
                       f"见 selfcheck.VERIFIED_COINCIDENCES）")
        out.append(vk.Result(cid, title, caliber,
                             PASS if not hits else FAIL, detail, rows))

    # ★ 词表**不在仓库里**（见 `mcp_server/selfcheck.py` 文件头「词表住在仓库外面」）。
    #   读不到词表时 S1/S2/S3 判 SKIP 而不是 PASS：这三项的全部内容就是
    #   「拿词表去比对」，没有词表就没有任何实测依据。理由文本只有一份，来自
    #   `selfcheck.WORDLIST_REASON`，改口径不必来这边同步。
    _wl = sc.WORDLIST_REASON

    one("S1", "识别性名称（公司名 / 课程名 / 原项目名）",
        "全仓扫描（含公版正文）：出现在哪里都是污染",
        sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL, root=r), pending=_wl)
    # ★ 检查项的名字本身也是内容。
    #
    # S2 原来那个标签里用的词**正好在词表里**，于是这一行检查自己就是一处命中，
    # 它还会被写进报告、让报告也跟着命中（实测就是这么转起来的）。所以标签一律
    # 改成中性说法 + 指向词表的常量名。
    #
    # 这段注释本身也不许把那些词写出来 —— 写「解释为什么不能写」的时候又把词写了
    # 一遍，是同一件事的第三层。★ 第二次修改之后，**仓库里任何文件都不许出现它们**
    # （词表搬到了仓外），所以这里连「哪个文件是唯一允许出现的地方」这句话都不能再说。
    one("S2", "领域敏感语义（词表见 selfcheck.MEDICAL_TERMS）",
        "只扫自己写的文件：公版古籍里的那些字是文本本身，不算本仓语义",
        sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED, root=r), pending=_wl)
    # ★ 两个数分开报：真命中必须为 0；已核验的巧合允许非 0，但要列出来。
    #   判据与 `tests/test_repo_hygiene.py` 里那条测试**同一个函数**
    #   （`sc.split_coincidences`），否则台账和 pytest 会给出不同结论。
    _s3_all = sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, root=r,
                      word_boundary=True)
    _s3_real, _s3_coin = sc.split_coincidences(_s3_all)
    one("S3", "原项目的具体数字",
        "除第三方原样数据外全扫；数字必须**整体独立**才算命中 —— "
        "一个禁用数字若只是某个更长数字里的一段（比如年份中的几位），不算。"
        "自己算出来的统计量恰好等于某个被禁数字时，按 "
        "`selfcheck.VERIFIED_COINCIDENCES` 登记后不计违规，但**照旧列出来**",
        _s3_real, verified=_s3_coin, pending=_wl)
    one("S4", "夸大措辞",
        "词表见 `mcp_server/selfcheck.py` 的 `OVERSTATED`"
        "（本报告自身也受该表约束，所以这里不逐字列出那几个词 —— "
        "机械检查分不清「用这个词夸结果」和「把这个词当规矩写下来」，"
        "所以规矩是根本别写）。只扫给人读的成品；"
        "生成报告的脚本另有运行时自查（见 eval_runner.write_report）",
        sc.grep_prose(sc.OVERSTATED, root=r))
    one("S5", "密钥文件（.env / *.key / *.pem …）",
        "全仓查找文件；即使被 .gitignore 排除，也不该躺在工作区里",
        sc.iter_secret_files(r))
    one("S6", "密钥形态（长得像密钥的文本）",
        "除第三方原样数据外全扫，含报告与文档：密钥不挑地方出现，"
        "示例里的一串同样会被复制走；命中的串只显示前 24 个字符",
        sc.grep_secrets(r))

    pat = re.compile(sc.ABS_PATH_PATTERN, re.I)
    abs_hits = []
    for p, rel in sc.iter_files(sc.SCOPE_AUTHORED, r):
        if p.suffix.lower() not in {".py", ".md", ".json", ".toml", ".yml", ".yaml"}:
            continue
        for i, line in enumerate(sc.read_text(p).splitlines(), 1):
            m = pat.search(line)
            if m:
                abs_hits.append(sc.Hit(str(rel), i, m.group(0), line.strip()[:160]))
    one("S7", "作者机器的绝对路径",
        "只扫自己写的文件；别人 clone 下来看到的应当全是仓库相对路径", abs_hits)

    rows = sc.audit_report_numbers(r)
    bad = [row for row in rows if not row["ok"]]
    out.append(vk.Result(
        "S8", "每个报了数字的报告都有「口径」小节",
        "逐份报告实测数字个数；有数字（≥1 个）却没有 `## 口径` 标题行的判不合格。"
        "没有数字的报告天然豁免，豁免是按内容算出来的、不是按文件名开的白名单",
        PASS if not bad else FAIL,
        (f"{len(rows)} 份报告，其中 "
         f"{sum(1 for x in rows if x['n_numbers'] == 0)} 份无数字（天然豁免）"
         if not bad else
         "缺口径：" + "、".join(f"{x['file']}（{x['n_numbers']} 个数字）" for x in bad)),
        bad))
    return out


def pytest_check() -> vk.Result:
    got, tail = _run(["-m", "pytest", "-q"], timeout=900)
    return vk.Result("P1", "python -m pytest",
                     "整套单元测试的退出码；pytest 自己会报 passed/failed 计数",
                     PASS if got == 0 else FAIL, f"退出码 {got} | {tail}")


def registry_check() -> vk.Result:
    """服务端注册表与 `TOOL_SCOPES` 必须一致。

    与 `pytest` 里那条测试是同一件事，但这里再报一次是为了让它出现在**这张表**上 ——
    表是给读报告的人看的，而这条正是「改一处漏一处会静默降级」的那类风险。
    """
    from mcp_server import server

    try:
        # 注册表来自 MCP 框架自己（`mcp.list_tools()`），不需要先建服务端；
        # 同步入口在事件循环里会抛，本脚本是同步的，所以这条路径是安全的。
        problems = server.selfcheck()  # 返回问题清单，空列表 = 通过
    except Exception as e:  # noqa: BLE001
        return vk.Result("P2", "服务端工具注册表自检",
                         "注册的工具名集合 == TOOL_SCOPES 的键集合",
                         ERROR, f"{type(e).__name__}: {e}")
    return vk.Result("P2", "服务端工具注册表自检",
                     "注册的工具名集合 == TOOL_SCOPES 的键集合",
                     PASS if not problems else FAIL,
                     "一致" if not problems else "；".join(problems)[:200])


def exit_checks(only: list[str] | None = None, *,
                rerun_llm: bool = False) -> list[vk.Result]:
    """把每条命令跑一遍，再交给 `verify_kit.judge_exit()` 判。

    ## 默认**不重跑**需要凭据的那四条

    理由是实测出来的两个副作用：它们真花钱（09 一次约 108 次调用、21 一次 72 次），
    而且会**覆盖 `reports/` 里的实测产物** —— 而 `RESULTS.md` 逐条引用了那些数字，
    闸门一跑文档就对不上产物了。完整说明见 `vk.judge_not_rerun()`。

    所以默认按盘上的产物判（`judge_not_rerun`），要端到端重跑加 `--rerun-llm`。
    """
    out: list[vk.Result] = []
    for entry in vk.EXIT_LEDGER:
        if only and not any(Path(entry["cmd"][0]).name == o for o in only):
            continue
        if entry.get("needs_llm") and not rerun_llm:
            out.append(vk.judge_not_rerun(entry, ROOT))
            continue
        cmd = [p if not p.endswith(".py") else str(Path(p)) for p in entry["cmd"]]
        got, tail = _run(cmd)
        out.append(vk.judge_exit(entry, got, ROOT, tail))
    return out


# ---------------------------------------------------------------- 自检（负控）
def run_selftest() -> int:
    """★ 证明每一条检查**都会红**。

    「检查是绿的」和「检查能红」是两件事。只做前者的话，一个 `return PASS`
    的假实现也能通过 —— 而且它在报告里长得和「一切正常」一模一样。

    做法：在临时目录里造一个**故意写坏的仓库**，然后调**同一批检查函数**。
    真实仓库的一个字节都不动（那种「临时改一下再改回去」的做法，
    恢复之后什么都没留下，见 `BLOCKERS.md` 末尾那段）。
    """
    problems: list[str] = []

    def expect(cond: bool, msg: str):
        if not cond:
            problems.append(msg)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "reports").mkdir()
        (root / "ok.py").write_text("print('ok')\n", encoding="utf-8")

        # 一次只放一种污染，看**对应那一项**是否红。
        #
        # 样本本身含那些词（否则测不出扫描器会不会红），所以它们**必须**
        # 放在排除在扫描之外的文件里 —— 也就是 `mcp_server/selfcheck.py`，
        # 见那里 `NEGATIVE_FIXTURES` 上方的说明。
        #
        # ★ 其中三条样本的内容**从词表里取**（`sc.WORDLIST_FIXTURES`）。
        #   词表不在仓库里，读不到时那三条样本是占位串，S1/S2/S3 也判 SKIP ——
        #   于是「它会红吗」这个问题**没有实测依据**，整条跳过。
        #   跳过而不是当作通过：负控失去意义时，它守护的那条断言也一起失去意义。
        wl_ok = sc.WORDLIST_AVAILABLE
        for cid, rel, body, what in sc.NEGATIVE_FIXTURES:
            if cid in sc.WORDLIST_FIXTURES and not wl_ok:
                continue
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            got = {x.cid: x for x in static_checks(root)}
            expect(got[cid].status == FAIL, f"{what} 的检查没红（{cid}）")
            p.unlink()

        # S5 查的是「文件存在」，单独造
        (root / ".env").write_text("LLM_API_KEY=\n", encoding="utf-8")
        expect({x.cid: x for x in static_checks(root)}["S5"].status == FAIL,
               "密钥文件的检查没红（S5）")
        (root / ".env").unlink()

        # ★ 反方向：污染清干净之后每一项都必须回到 PASS。
        # 只测「会红」的话，一个永远返回 FAIL 的实现也能通过。
        clean = {x.cid: x for x in static_checks(root)}

        for cid in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"):
            if cid in sc.WORDLIST_FIXTURES and not wl_ok:
                continue        # 词表不在本机 → 这一项是 SKIP，「回到 PASS」无从谈起
            expect(clean[cid].status == PASS,
                   f"干净目录下 {cid} 不是 PASS：{clean[cid].status} {clean[cid].detail}")

        # 掩码的负控（S9 的底）。两头都要测：
        #   掩码后的文本必须自己不含污染 —— 否则报告仍是载体；
        #   未掩码的同一段必须被判出来 —— 否则 S9 是个永远绿的摆设。
        for cid, rel, body, what in sc.NEGATIVE_FIXTURES:
            if cid in ("S8",):          # S8 的「污染」是缺小节，不是敏感串
                continue
            if cid in sc.WORDLIST_FIXTURES and not wl_ok:
                continue        # 占位串既掩不掉也扫不出来，测了只会得到假红
            expect(not sc.scan_report_carrier(sc.mask_all(body)),
                   f"掩码之后仍带着污染（{what}）")
            expect(bool(sc.scan_report_carrier(body)),
                   f"未掩码的样本没被判出来（{what}）—— 掩码检查是摆设")

        # 产物矛盾
        (root / "reports" / "noise_band.json").write_text("{}", encoding="utf-8")
        (root / "reports" / "noise_band_NOT_RUN.md").write_text("未完成\n",
                                                               encoding="utf-8")
        expect(any(x.status == FAIL for x in vk.artifact_checks(root)),
               "结果与「未完成」声明同时存在时没有报矛盾")
        (root / "reports" / "noise_band_NOT_RUN.md").unlink()
        expect(all(x.status == PASS for x in vk.artifact_checks(root)),
               "只有结果、没有未完成声明时应全 PASS")

        # 文档检查：缺一份必需文档必须红
        expect({x.cid: x for x in vk.doc_checks(root)}["D1"].status == FAIL,
               "缺少必需文档时 D1 没红")
        for n in vk.REQUIRED_DOCS:
            (root / n).write_text("x", encoding="utf-8")
        expect({x.cid: x for x in vk.doc_checks(root)}["D1"].status == PASS,
               "文档齐了 D1 却不是 PASS")
        expect({x.cid: x for x in vk.doc_checks(root)}["D2"].status == SKIP,
               "README 还没写时 D2 应当是 SKIP 而不是 PASS")

    # 退出码判定的四条路径
    llm_entry = {"cmd": ["experiments/09_text2sql_eval.py"], "expect": 0,
                 "needs_llm": True, "not_run": "reports/text2sql_NOT_RUN.md",
                 "why": "x"}
    with tempfile.TemporaryDirectory() as td2:
        r2 = Path(td2)
        (r2 / "reports").mkdir()
        expect(vk.judge_exit(llm_entry, 0, r2).status == PASS, "期望码命中时没判 PASS")
        expect(vk.judge_exit(llm_entry, 5, r2).status == FAIL,
               "光报 5、不留任何痕迹时应当 FAIL")
        (r2 / "reports" / "text2sql_NOT_RUN.md").write_text("x", encoding="utf-8")
        expect(vk.judge_exit(llm_entry, 5, r2).status == FAIL,
               "缺 BLOCKERS 条目时应当 FAIL")
        (r2 / "BLOCKERS.md").write_text("见 reports/text2sql_NOT_RUN.md\n",
                                        encoding="utf-8")
        expect(vk.judge_exit(llm_entry, 5, r2).status == SKIP,
               "三件齐了应当判 SKIP（合法未完成）")
        expect(vk.judge_exit(llm_entry, 1, r2).status == FAIL, "退出码 1 应当 FAIL")
        expect(vk.judge_exit(llm_entry, -1, r2).status == ERROR,
               "命令没跑起来应当 ERROR 而不是 FAIL")

    if problems:
        print(f"[FAIL] 自检的负控不通过，{len(problems)} 处：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return E_SELFTEST
    print("[OK] 自检的负控全部按预期报红/报绿：")
    print("     S1~S8 各放一种污染 → 逐项报红；污染清除后 → 逐项报绿")
    print("     掩码：样本掩码后 → 不再含污染；未掩码的同一段 → 必须被判出来")
    print("     S5 密钥文件、S8 缺口径小节、D1 缺文档 → 都报红；文档补齐 → 绿，"
          "README 未写 → SKIP 而不是 PASS")
    print("     A* 产物矛盾（结果与未完成声明并存）→ 报红")
    print("     X* 退出码：期望码→PASS；光报 5 不留痕迹→FAIL；只有 NOT_RUN 文件→FAIL；"
          "三件齐→SKIP；其它码→FAIL；没跑起来→ERROR")
    return E_OK


# ---------------------------------------------------------------- 报告
def render_report(results: list[vk.Result]) -> str:
    n = {s: sum(1 for r in results if r.status == s)
         for s in (PASS, FAIL, SKIP, ERROR)}
    lines: list[str] = []
    A = lines.append
    A("# 自检台账")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/19_verify.py`，一条命令跑完下面每一行")
    A("- **判定**：`PASS` / `FAIL` / `SKIP` / `ERROR` 四选一。"
      "`SKIP` 不等于 `PASS` —— 它表示「这项现在没资格下结论」")
    A("- **S1~S4**：四类红线的实际命中数，词表与扫描范围定义在 "
      "`mcp_server/selfcheck.py` 的文件头")
    A("- **S5/S6**：密钥文件与密钥形态。后者扫全文（含报告）；"
      "命中的串在报告里只显示前 24 个字符")
    A("- **S7**：作者机器绝对路径，只扫自己写的文件")
    A("- **S8**：逐份报告实测数字个数，有数字却没有 `## 口径` 标题行的判不合格。"
      "「数字」定义为不贴着字母/下划线/点/斜杠/连字符的数字串，"
      "比值（`32/32`）整体算一个")
    A("- **S9**：本报告**自己**再扫一遍全部红线词表与形态。"
      "理由是个会自我放大的回路：报告要列出命中的位置，"
      "若逐字抄下来，报告自己就成了新的载体，下一轮会在报告里再次命中它。"
      "所以命中内容一律掩码（见 `mcp_server/selfcheck.py` 的 `mask_all`），"
      "并由这一项机械地确认掩码没漏 —— 不靠「写的时候小心一点」")
    A("- **A\\***：产物互斥检查 —— 「实测结果」与「未完成声明」不许同时存在")
    A("- **D\\***：根目录必需文档；阶段 6 的两份未创建时判 `SKIP`")
    A("- **X\\***：每个入口脚本的**实际退出码**与它声明的期望码比对。"
      f"需要模型凭据的脚本以 {vk.NO_LLM_EXIT} 结束是**合法**的，"
      f"但必须同时满足：留下 `*_NOT_RUN.md` + `{vk.BLOCKERS}` 里有对应动作")
    A("- **P1**：`python -m pytest` 的退出码")
    A("- **P2**：服务端注册表自检（注册的工具名集合 == `TOOL_SCOPES` 的键集合）")
    A("- **本表数字的性质**：判定计数、命中数、退出码**全部是实测的**；"
      "没有任何一个数字是估计、写死或从别处抄来的")
    A("")
    A(f"**合计**：{len(results)} 条 —— PASS {n[PASS]} / FAIL {n[FAIL]} / "
      f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    A("")
    A("## 明细")
    A("")
    A("| # | 检查项 | 判定 | 说明 |")
    A("|---|---|---|---|")
    for r in results:
        A(r.line())
    A("")
    A("## 命中的行（若有）")
    A("")
    A("命中内容已掩码，按 `文件:行号` 打开原文件看原文。")
    A("")
    any_hits = False
    for r in results:
        if not r.hits:
            continue
        any_hits = True
        A(f"### {r.cid} {r.title} —— {len(r.hits)} 处")
        A("")
        for h in r.hits[:40]:
            if isinstance(h, dict):
                A(f"- `{h['file']}:{h['line']}` 命中「{h['needle']}」")
                A(f"  `{h['text']}`")
            else:
                A(f"- `{h}`")
        A("")
    if not any_hits:
        A("无。")
        A("")
    A("## 这张表**不能**说明什么")
    A("")
    A("- 它不检查业务正确性 —— 「SQL 护栏挡住了该挡的」由 `11_guardrail_test.py` "
      "自己的用例说话，本表只记录那条命令的退出码。")
    A("- `SKIP` 不等于 `PASS`。需要模型凭据的那几项在凭据配好之前一直是 `SKIP`，"
      f"它们的实测数字**不存在**（见 `{vk.BLOCKERS}`）。")
    A("- 静态扫描证明的是「没找到」，不是「不存在」—— 词表之外的说法它看不见。"
      "所以词表本身要有人维护，见 `DECISIONS.md` D-24。")
    A("")
    return "\n".join(lines)


def write_report(results: list[vk.Result]) -> tuple[list[str], vk.Result]:
    """落盘，并判 S9「报告自身不带污染」。

    ## 为什么要迭代而不是渲染两遍

    S9 这一行的结论要写进报告里，而写进去之后报告就变了 ——
    于是「S9 判的那段文本」和「真正落盘的文本」不是同一段。
    那种自我指涉的检查最容易写成假的：查的是 A，写的是 B，看起来却一样。

    所以这里**迭代到不动点**：渲染 → 扫 → 把结论写回 S9 那一行 → 再渲染 → 再扫。
    S9 行的内容本身是掩码过的、不含污染，所以两轮之内必然稳定；
    真出现来回震荡就停下来并把结论判红（说明掩码没兜住）。
    """
    from mcp_server import eval_runner as runner

    s9 = vk.Result("S9", "报告自身不带污染", "", SKIP, "尚未判定")
    results.append(s9)
    text = ""
    for _ in range(3):
        text = render_report(results)
        carrier = sc.scan_report_carrier(text)
        status = PASS if not carrier else FAIL
        detail = ("0 命中" if not carrier else
                  "报告自己带着污染：" + "、".join(sorted({sc.mask_all(c) for c in carrier})))
        hits = [{"file": "reports/verify.md", "line": 0, "needle": sc.mask_all(c),
                 "text": ""} for c in carrier]
        if s9.status == status and s9.detail == detail:
            break                      # 不动点：S9 的结论不影响它自己的判定
        s9.status, s9.detail, s9.hits = status, detail, hits
    s9.caliber = ("把**即将落盘的这段文本**再扫一遍全部红线词表与形态，"
                  "直到「S9 的结论」与「S9 判的那段文本」是同一段为止；"
                  "掩码就是为了让它为空，这一项机械地确认掩码没漏")
    banned = runner.write_report(REPORTS / "verify.md", text)
    return banned, s9


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="台账式自检（不需要 LLM）")
    ap.add_argument("--selftest", action="store_true",
                    help="负控：证明每一条检查都会红（不动真实仓库）")
    ap.add_argument("--quick", action="store_true",
                    help="跳过需要真实起子进程的项（只做静态检查 + 注册表）")
    ap.add_argument("--only", default="",
                    help="只跑这些入口脚本的退出码检查，逗号分隔文件名")
    ap.add_argument("--rerun-llm", action="store_true",
                    help="端到端重跑需要凭据的四条（会花钱并覆盖 reports/ 里的产物）")
    ap.add_argument("--json", action="store_true", help="同时落一份 verify.json")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()

    results: list[vk.Result] = []
    results += static_checks()
    results += vk.artifact_checks()
    results += vk.doc_checks()
    if args.quick:
        results.append(vk.Result("X*", "入口脚本退出码台账",
                                 "本次用 --quick 跑，未执行", SKIP,
                                 "跳过：--quick"))
        results.append(vk.Result("P1", "python -m pytest", "本次用 --quick 跑，未执行",
                                 SKIP, "跳过：--quick"))
    else:
        only = [s.strip() for s in args.only.split(",") if s.strip()]
        results += exit_checks(only or None, rerun_llm=args.rerun_llm)
        results += [pytest_check()]
    results += [registry_check()]

    REPORTS.mkdir(parents=True, exist_ok=True)
    banned, s9 = write_report(results)
    if args.json:
        (REPORTS / "verify.json").write_text(json.dumps({
            "results": [r.as_dict() for r in results],
            "summary": {s: sum(1 for r in results if r.status == s)
                        for s in (PASS, FAIL, SKIP, ERROR)},
            "note": "口径见 reports/verify.md 的「口径」小节。"
                    "SKIP 不等于 PASS：需要模型凭据的项在配好之前一直是 SKIP。",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    n = {s: sum(1 for r in results if r.status == s)
         for s in (PASS, FAIL, SKIP, ERROR)}
    for r in results:
        mark = {PASS: " ", FAIL: "F", SKIP: "-", ERROR: "E"}[r.status]
        print(f"  [{mark}] {r.cid:<7} {r.title:<42} {r.detail[:70]}")
    print(f"\n合计 {len(results)} 条：PASS {n[PASS]} / FAIL {n[FAIL]} / "
          f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    print(f"报告：{paths.rel(REPORTS / 'verify.md')}")

    if banned:
        # 这里**不掩码**：终端不是产物，被扫描的是磁盘上的文件。看不出是哪个词
        # 就没法改；而改的地方是 `render_report()` 里的模板文字，不是报告本身 ——
        # 模板修好之后下一次运行会把 `reports/verify.md` 整个覆盖掉。
        print(f"[FAIL] 自检报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_FAIL
    if s9.status == FAIL:
        print("[FAIL] 自检报告自己带着污染（S9），见 reports/verify.md", file=sys.stderr)
        return E_FAIL
    if n[ERROR]:
        return E_ERROR
    if n[FAIL]:
        return E_FAIL
    print("[OK] FAIL 0 / ERROR 0")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
