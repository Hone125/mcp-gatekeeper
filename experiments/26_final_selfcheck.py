"""收尾自检：把「发布前必须机械核一遍」的 9 项一次跑完，逐项留下**命中数**。

## 它和另外三套检查的分工

| 谁 | 管什么 | 什么时候跑 |
|---|---|---|
| `pytest` | 某个函数对不对 | 写代码时 |
| `experiments/19_verify.py` | 每个入口脚本的退出码对不对得上声明 | 改脚本后 |
| `experiments/25_deliverable_check.py` | 文档里写的和仓库里对不对得上 | 改文档后 |
| **本脚本** | **发布前那 9 条红线，逐条报命中数** | 决定发布的那一刻 |

前三个是过程，本脚本是**结论**：它的产物 `reports/final_selfcheck.md` 是一份
可以打印出来贴在发布说明里的东西 —— 每一行都写着「查了几处、命中几处、在哪一行」。

## 为什么它自己不去扫，而是调 `selfcheck`

扫描口径只能有一份。本脚本里的每一项都调 `mcp_server/selfcheck.py`
的同一个函数，`19_verify.py` 与 `tests/` 调的是同一批函数 ——
**口径分叉的坏处是两边都绿、结论相反**，而且没人会发现。

## 为什么它**不在** `19_verify.py` 的退出码台账里

本脚本要跑 `19_verify.py`（第 9 项）。把它放进 19 的台账，就成了
「19 跑 26、26 又跑 19」，转起来了。同 `tools/check.py` 的处境，处置也相同：
放在 `tools/check.py` 的链子末尾，并由 `tests/test_verify_kit.py` 里
一条 `--list` 的子进程用例守着（同一条用例同时守着这两个脚本）。

代价说清：整个闸门跑下来会起**两次** `19_verify.py`（闸门一次、本脚本一次），
多花一分钟左右。这是为了让这份报告**自证**，而不是引用别人算好的结论。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 9 项全部通过 |
| 1 | 有项不合格（报告照写，写明是哪一项） |
| 2 | 有项自己出错了（异常 / 命令跑不起来），这一项**不算通过** |
| 5 | 有项 `SKIP`（**没有实测依据**，目前只可能是词表不在本机）—— 见下 |

★ 第 4 条与 `tools/check.py` 的 `pending_exit` 对应：本脚本是**发布结论**，
所以「9 项里有 3 项没资格下结论」不能算通过。判 5 而不是 1，是因为处理方式不同 ——
它不是仓库里写错了什么，而是**本机缺一个仓外文件**（`BLOCKERS.md` 卡点 3 给了
最小动作：造一个词表文件、设一个环境变量）。闸门读到 5 会打印「未完成」而不是「失败」。

为什么第 1~3 项会 SKIP：三张词表**不在仓库里**（见 `mcp_server/selfcheck.py`
文件头「词表住在仓库外面」）。读不到词表时这三项的全部内容 —— 「拿词表去比对」——
就没有任何实测依据。**把「没查」写成「0 命中」是这一整套检查最想防的那种谎。**
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402
from mcp_server import delivery_selfcheck as dsc  # noqa: E402
from mcp_server import selfcheck as sc  # noqa: E402
from mcp_server import verify_kit as vk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PY = sys.executable

E_OK, E_FAIL, E_ERROR, E_PENDING = 0, 1, 2, 5
PASS, FAIL, SKIP, ERROR = "PASS", "FAIL", "SKIP", "ERROR"


class Item:
    """一项检查的结果。

    `hits` 存的是**掩码后**的命中行（正常应当为空）。为什么要掩码：
    这份报告本身会被 `pytest` 的仓库卫生用例扫到 —— 逐字引用一处违规，
    报告自己就成了新的载体（同一种回路在本仓库出现过三次，见 `DECISIONS.md` D-30）。
    """

    __slots__ = ("n", "title", "caliber", "cmd", "status", "n_hits", "hits", "note")

    def __init__(self, n: str, title: str, caliber: str, cmd: str, status: str,
                 n_hits: int = 0, hits: list[str] | None = None, note: str = ""):
        self.n, self.title, self.caliber, self.cmd = n, title, caliber, cmd
        self.status, self.n_hits, self.note = status, n_hits, note
        self.hits = hits or []

    def as_dict(self) -> dict:
        return {"n": self.n, "title": self.title, "caliber": self.caliber,
                "cmd": self.cmd, "status": self.status, "n_hits": self.n_hits,
                "note": self.note, "hits": self.hits}


MAX_LINES = 20


def _from_hits(n: str, title: str, caliber: str, cmd: str, hits: list,
               verified: list = (), pending: str = "") -> Item:
    """把 `selfcheck` 的命中列表变成一项结果。命中的行**掩码后**才留。

    渲染走 `vk.format_hit_lines()` —— 原先这里自己写了一遍，用错了属性名
    （`h.file` 而不是 `h.rel`），而那个错只在命中非空时才发作，
    于是这一项**只在它该报红的时候**崩成 ERROR。详见那个函数的文档字符串。

    `verified` 是已核验的巧合：照旧列出来，且单独报一个数，但不判红。

    `pending` 非空 → 这一项判 `SKIP`：**没有实测依据**，不是通过，也不是失败。
    目前只有一种来源：三张词表不在仓库里，本机又没配（见文件头退出码表）。
    """
    if pending:
        return Item(n, title, caliber, cmd, SKIP, 0, [],
                    f"{pending} —— 本项**没有实测依据**，不是通过")
    lines = vk.format_hit_lines(hits, MAX_LINES)
    lines += [f"[已核验巧合] {s}" for s in vk.format_hit_lines(verified, MAX_LINES)]
    note = f"命中 {len(hits)} 处，只列前 {MAX_LINES} 行" if len(hits) > MAX_LINES else ""
    if verified:
        note = (note + "；" if note else "") + f"另有 {len(verified)} 处已核验巧合，不计违规"
    return Item(n, title, caliber, cmd, PASS if not hits else FAIL,
                len(hits), lines, note)


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    """跑一条命令，返回 `(退出码, 输出尾部)`；跑不起来返回 -1。

    尾部是**引用**：掩码之后再进报告。绝对路径就是这么漏进去的 ——
    脚本在自己 stdout 里打一行作者机器的路径，被原样抄进报告，
    报告就成了那个路径的载体（见 `DECISIONS.md` D-30 的连带修复）。

    走 `vk.tail_for_report` 而不是 `sc.mask_all`：它多去一样东西 —— **耗时读数**
    （`515 passed in 22.51s`）。那种数字每次都不同，抄进报告就等于报告每次都不同，
    而「同一份代码跑两遍，产物逐字节相同」正是本仓要能自证的东西。
    """
    try:
        p = subprocess.run([PY, *cmd], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）"
    except OSError as e:
        return -1, f"起不来：{type(e).__name__}: {e}"
    tail = (p.stdout or "").strip().splitlines()[-2:]
    err = (p.stderr or "").strip().splitlines()[-2:]
    return p.returncode, vk.tail_for_report(" / ".join(tail + err), limit=300)


def check_1_domain_terms() -> Item:
    return _from_hits(
        "1", "领域敏感语义",
        "词表见 `mcp_server/selfcheck.py` 的 `MEDICAL_TERMS`（这里不逐字列出）；"
        "只扫自己写的文件 —— 公版正文里出现的那些字是文本本身，不算本仓语义。"
        "词表不在仓库里，本机读不到时本项判 `SKIP`",
        "selfcheck.grep(MEDICAL_TERMS, SCOPE_AUTHORED)",
        sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED),
        pending=sc.WORDLIST_REASON)


def check_2_forbidden_names() -> Item:
    return _from_hits(
        "2", "识别性名称",
        "词表见 `selfcheck.py` 的 `FORBIDDEN_NAMES`；全仓扫描（含公版正文）："
        "出现在哪里都是污染。词表不在仓库里，本机读不到时本项判 `SKIP`",
        "selfcheck.grep(FORBIDDEN_NAMES, SCOPE_ALL)",
        sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL),
        pending=sc.WORDLIST_REASON)


def check_3_forbidden_numbers() -> Item:
    real, coin = sc.split_coincidences(
        sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, word_boundary=True))
    return _from_hits(
        "3", "不该出现的具体数字",
        "词表见 `selfcheck.py` 的 `FORBIDDEN_NUMBERS`；数字必须**整体独立**才算命中 —— "
        "某个更长数字里的一段（比如年份中的几位）不算。自己算出来的统计量恰好等于"
        "某个被禁数字时，按 `selfcheck.VERIFIED_COINCIDENCES` 登记后不计违规，"
        "但**照旧列出来**，且单独报一个数。词表不在仓库里，本机读不到时本项判 `SKIP`",
        "selfcheck.grep(FORBIDDEN_NUMBERS, SCOPE_NO_VERBATIM, word_boundary=True)"
        " → split_coincidences()",
        real, verified=coin, pending=sc.WORDLIST_REASON)


def check_4_overstated() -> Item:
    return _from_hits(
        "4", "夸大措辞",
        "词表见 `selfcheck.py` 的 `OVERSTATED`（本报告自身也受该表约束，"
        "所以这里不逐字列出那几个词）；只扫**给人读的成品**，"
        "程序生成的报告另有运行时自查（`eval_runner.write_report`）",
        "selfcheck.grep_prose(OVERSTATED)",
        sc.grep_prose(sc.OVERSTATED))


def check_5_secret_shapes() -> Item:
    return _from_hits(
        "5", "密钥形态",
        "词表见 `selfcheck.py` 的 `SECRET_PATTERNS`；除第三方原样数据外全扫"
        "（含报告与文档），命中的串只显示前 24 个字符",
        "selfcheck.grep_secrets()", sc.grep_secrets())


def check_6_report_calibers() -> Item:
    rows = sc.audit_report_numbers()
    bad = [r for r in rows if not r["ok"]]
    hits = [f"{r['file']}：{r['n_numbers']} 个数字，没有 `## 口径` 标题行" for r in bad]
    n_plain = sum(1 for r in rows if r["n_numbers"] == 0)
    return Item(
        "6", "报数字的报告都有口径小节",
        "逐份报告实测数字个数；有数字（≥1 个）却没有 `## 口径` 标题行的判不合格。"
        "没有数字的报告天然豁免 —— 豁免是按内容算出来的，不是按文件名开的白名单",
        "selfcheck.audit_report_numbers()", PASS if not bad else FAIL,
        len(bad), hits, f"{len(rows)} 份报告，其中 {n_plain} 份无数字（天然豁免）")


def check_7_secret_files() -> Item:
    """★ `iter_secret_files()` 返回的是**相对路径字符串**，不是 `Hit` ——
    别的扫描函数返回 Hit，这一个不是（文件级检查没有行号）。"""
    hits = sc.iter_secret_files()
    return Item(
        "7", "工作区里没有密钥文件",
        "全仓查找 `.env` / `*.key` / `*.pem` 等；**即使被 `.gitignore` 排除**，"
        "也不该躺在工作区里（被排除只说明它不会进 git，不说明它不存在）",
        "selfcheck.iter_secret_files()", PASS if not hits else FAIL, len(hits),
        [sc.mask_all(str(h)) for h in hits[:MAX_LINES]])


def check_8_pytest() -> Item:
    code, tail = _run(["-m", "pytest", "-q"], timeout=900)
    _RC["pytest"], _RC["pytest_tail"] = code, tail
    ok = code == 0
    return Item("8", "单元测试",
                "`python -m pytest` 的退出码：0 = 全过，非 0 = 有失败。"
                "命令跑不起来（-1）判 ERROR 而不是 FAIL —— 该修的地方不同",
                "python -m pytest -q",
                PASS if ok else (ERROR if code < 0 else FAIL),
                0 if ok else 1, [] if ok else [tail], f"退出码 {code}")


def check_9_verify() -> Item:
    code, tail = _run(["experiments/19_verify.py"], timeout=1800)
    _RC["verify"] = code
    ok = code == 0
    return Item("9", "台账式自检",
                "`experiments/19_verify.py` 的退出码：0 = FAIL 0 / ERROR 0。"
                "它自己会把每条结果的判定与口径写进 `reports/verify.md`",
                "python experiments/19_verify.py",
                PASS if ok else (ERROR if code < 0 else FAIL),
                0 if ok else 1, [] if ok else [tail], f"退出码 {code}")


CHECKS = (check_1_domain_terms, check_2_forbidden_names, check_3_forbidden_numbers,
          check_4_overstated, check_5_secret_shapes, check_6_report_calibers,
          check_7_secret_files, check_8_pytest, check_9_verify)

# 第 8、9 项跑出来的退出码，留给下面的「交付级自检」第 11 项做推论用。
# ★ 存在这里而不是让第 11 项自己再跑一遍：那会白花两分钟，且**跑出两份可能不一致的结论**。
_RC: dict = {}


def render(items: list[Item]) -> str:
    n = {s: sum(1 for i in items if i.status == s)
         for s in (PASS, FAIL, SKIP, ERROR)}
    L: list[str] = []
    A = L.append
    A("# 收尾自检（发布前 9 项）")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/26_final_selfcheck.py`；扫描口径全部来自 "
      "`mcp_server/selfcheck.py`（**单一口径**：`19_verify.py` 与 `tests/` 用的是同一批函数）")
    A("- **每一行的「命中数」都是实测的**：第 1~7 项数的是扫描出来的命中**行**数；"
      "第 8、9 项数的是子进程退出码（0 = 通过）")
    A("- **PASS 的含义**：命中数为 0，或退出码为 0。`ERROR` 表示这一项**自己**出错了"
      "（比如命令没跑起来）—— **不算通过**")
    A("- **`SKIP` 的含义**：这一项**没有实测依据**，既不是通过也不是失败。"
      "目前只有一种来源 —— 第 1~3 项靠的三张词表不在仓库里"
      "（见 `mcp_server/selfcheck.py` 文件头），本机读不到词表时无从比对。"
      "**把「没查」写成「0 命中」是这一整套检查最想防的那种谎**，"
      "所以这里单列一个判定，并把退出码从 0 改成 5（见文件头）")
    A("- **命中行是掩码之后才写进来的**：这份报告会被仓库卫生用例扫到，"
      "逐字引用一处违规会让报告自己成为新的载体（见 `DECISIONS.md` D-30）")
    A("")
    A(f"**合计**：9 项 —— PASS {n[PASS]} / FAIL {n[FAIL]} / "
      f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    A("")
    A("## 明细")
    A("")
    A("| # | 检查项 | 判定 | 命中数 | 命令 |")
    A("|---|---|---|---|---|")
    for i in items:
        A(f"| {i.n} | {i.title} | {i.status} | {i.n_hits} | `{i.cmd}` |")
    A("")
    A("## 每项的判据与实测")
    A("")
    for i in items:
        A(f"### {i.n} {i.title} —— {i.status}（命中 {i.n_hits}）")
        A("")
        A(f"- **判据**：{i.caliber}")
        A(f"- **命令**：`{i.cmd}`")
        if i.note:
            A(f"- **备注**：{i.note}")
        if i.hits:
            A("- **命中行**：")
            for h in i.hits:
                A(f"  - `{h}`")
        else:
            A("- **命中行**：无")
        A("")
    A("## 这张表**不能**说明什么")
    A("")
    A("- 它不判断代码写得好不好、文档够不够清楚 —— 那件事没有机械判据，"
      "硬编一个只会得到一条永远绿的假检查。它只查「说好的红线有没有被越过」。")
    A("- **静态项是词表驱动的**：词表里没有的说法扫不出来。"
      "「0 命中」的确切含义是「词表里的东西一处都没有」，不是「绝对干净」。")
    A("- 第 8、9 项的结论来自子进程退出码。命令**跑不起来**时会是 `ERROR` 而不是 `FAIL` —— "
      "两者要修的地方不同：一个是环境，一个是仓库内容。")
    A("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="收尾自检：发布前 9 项逐条报命中数")
    ap.add_argument("--json", action="store_true", help="同时落一份 .json")
    args = ap.parse_args()

    items: list[Item] = []
    for fn in CHECKS:
        try:
            items.append(fn())
        except Exception as e:  # noqa: BLE001
            items.append(Item(fn.__name__[6], fn.__name__, "检查自身抛异常",
                              fn.__name__, ERROR, 0, [],
                              f"{type(e).__name__}: {e}"))

    REPORTS.mkdir(parents=True, exist_ok=True)
    from mcp_server import eval_runner as runner

    # ---- 交付级自检（15 项）--------------------------------------------
    # 那 15 项问的是「**这一批交付物整体**是否成立」，检查对象跨出了仓库
    # （交付物在仓库外面、远端在网络上、还有一项要问 git 历史）。
    # 逻辑在 `mcp_server/delivery_selfcheck.py`，这里只把**已经算好的**结果递进去。
    #
    # ★ 第 11 项问的是「闸门整体退出码」，而本脚本就是闸门第 4 步 ——
    #   让它自己跑闸门会无限套娃。所以改为**由四步的退出码推得**：
    #   前 3 步里 pytest 与 19_verify 上面已经跑过（见第 8、9 项），
    #   第 3 步（交付物核验，几秒钟）在这里补跑一次，
    #   第 4 步就是本脚本自己的结论。四步全 0 → 闸门必然返回 0。
    deliv_rc, _ = _run(["experiments/25_deliverable_check.py"], timeout=900)
    self_ok = all(i.status == PASS for i in items)
    steps_ok = (self_ok and deliv_rc == 0 and _RC.get("pytest") == 0
                and _RC.get("verify") == 0)
    _m = __import__("re").search(r"(\d+)\s+passed", _RC.get("pytest_tail", ""))
    ditems = dsc.run(deps={
        "pytest_rc": _RC.get("pytest"),
        "pytest_n": f"{_m.group(1)} 条通过" if _m else "（没读通过数）",
        "gate_rc": 0 if steps_ok else None,
        "steps_note": f"pytest={_RC.get('pytest')} / 19_verify={_RC.get('verify')} / "
                      f"25_deliverable_check={deliv_rc} / "
                      f"本步（26）={'0' if self_ok else '非 0'}",
    }, online=False)

    # ★ 迭代到不动点：报告里写的内容会被下一次渲染改变（命中行、合计都在变），
    # 所以要「渲染 → 扫自己 → 还脏就掩码重渲染」，直到稳定。
    # 这一步不能省：逐字引用一处命中，报告自己就变成新的载体，
    # 而这正是本仓库踩过三次的那个回路（见 `DECISIONS.md` D-30）。
    text = render(items) + "\n" + dsc.section(ditems)
    self_carrier: list[str] = []
    for _ in range(3):
        carrier = sc.scan_report_carrier(text)
        if not carrier:
            break
        self_carrier = [sc.mask_all(c) for c in carrier]
        text = sc.mask_all(text)

    banned = runner.write_report(REPORTS / "final_selfcheck.md", text)
    # ★ 这里**不**写 `delivery_selfcheck.json`：那一份由 `experiments/28_delivery_selfcheck.py`
    #   落盘（它跑得到联网那两项）。闸门里这一份是离线子集，覆盖过去等于把联网的结论擦掉。

    for i in items:
        print(f"  [{i.status[0]}] {i.n}. {i.title:<18} 命中 {i.n_hits}"
              + (f"  {i.note}" if i.note else ""))
        for h in i.hits[:3]:
            print(f"        {h}")
    n = {s: sum(1 for x in items if x.status == s)
         for s in (PASS, FAIL, SKIP, ERROR)}
    print(f"\n合计 {len(items)} 项：PASS {n[PASS]} / FAIL {n[FAIL]} / "
          f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    print(f"报告：{paths.rel(REPORTS / 'final_selfcheck.md')}")

    if args.json:
        (REPORTS / "final_selfcheck.json").write_text(json.dumps({
            "items": [i.as_dict() for i in items],
            "note": "口径见 reports/final_selfcheck.md 的「口径」小节。"
                    "命中行是掩码之后才写进来的。",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    dn = dsc.totals(ditems)
    print(f"\n交付级自检 15 项：PASS {dn[PASS]} / FAIL {dn[FAIL]} / "
          f"SKIP {dn[SKIP]} / ERROR {dn[ERROR]}")
    for i in ditems:
        if i.status in (FAIL, ERROR):
            print(f"  [{i.status}] {i.n}. {i.title}：{i.note}")

    if self_carrier:
        print(f"[ERROR] 报告渲染时自己带着污染（已掩码）：{self_carrier}",
              file=sys.stderr)
        return E_ERROR
    if banned:
        # 这里不掩码：终端不是产物，被扫的是磁盘上的文件。
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_FAIL
    if n[ERROR] or dn[ERROR]:
        return E_ERROR
    if n[FAIL] or dn[FAIL]:
        return E_FAIL
    # ★ 15 项里的 SKIP **不算失败**：交付物不在本机、或者断网，都是预期状态
    #   （这一条在 `delivery_selfcheck.py` 的文件头写了理由）。
    #   9 项里的 SKIP 不一样 —— 那是「这份仓库的发布结论缺一项依据」，不能算通过。
    if n[SKIP]:
        print(f"[未完成] {n[SKIP]} 项 SKIP：没有实测依据，不是通过。"
              f"最小动作见 BLOCKERS.md 卡点 3", file=sys.stderr)
        return E_PENDING
    print("[OK] 9 项全部通过；交付级 15 项无 FAIL/ERROR"
          f"（其中 {dn[SKIP]} 项 SKIP，不是通过）")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
