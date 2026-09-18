"""验证「明文桥」真的拆掉了：**本地一份、公网一份**，两份实测输出都落盘。

## 它验的是什么

`mcp_server/selfcheck.py` 曾经逐字写着公司名 / 课程名 / 原仓库名 / 原项目的数字，
而且它自己住在扫描的豁免清单里 —— 于是「识别性名称 0 命中」为真，却推不出
「仓库里没有这些名字」。这个文件被推到公网之后，那几行字就跟着上了公网。

修法是把**词表搬到仓库外面**（判定口径一个字没改，见那个文件的文件头）。
本脚本回答的问题只有一个：

    **修完之后，这个文件里还读得出那些字吗？本地那台机器上、公网上，各读一遍。**

## 三条检查，两条负控

| # | 查什么 | 为什么非有它不可 |
|---|---|---|
| 1 | 工作区里的 `mcp_server/selfcheck.py` | 本地的结论 |
| 2 | 公网 raw 上的同一个文件 | 推上去的才是别人能读到的 —— 只做第 1 条等于没查 |
| 3 | 两边的 sha256 是否相同 | 证明公网上那份**就是**本地这份，而不是「碰巧也没命中」的旧版 |
| 负控甲 | 一段**已知含词**的合成样本 | ★ 尺子当场还灵不灵。不读 git 历史，任何机器上都跑得起来 |
| 负控乙 | 修复前那个提交里的同一个文件 | ★ 词表里有、它就该报出来；顺带证明那段旧历史还在、没被人无声重写 |

**负控为什么是两条。** 原来只有一条（乙），而它要读本机 git 对象库里的旧版本 ——
换一台机器 clone，那个对象根本不存在，于是这一条从「实测」退化成「读不到」，
而那三个 0 就再也没有东西撑着了。现在：

- **甲**拿 `selfcheck.NEGATIVE_FIXTURES` 里 S1/S2/S3 三条样本（正文**从仓外词表现取**，
  所以本文件里一个字面量都没有），喂给**与乙同一个** `scan()` 与 `count_occurrences()`：
  同一张词表、同一个函数，喂一段必然含词的文本，就必须报出来。
  ★ 样本**只在内存里过一遍，不落盘** —— 它含真实的敏感串，写到磁盘上就等于又造了一处明文。
- **乙**要的是旧历史对象，只有本机留着那段历史的机器才取得到。取不到时本脚本判
  **退出码 3（负控没有实测依据）**，而不是判失败 —— 判失败等于说「历史被人重写过」，
  那是一句**不实且严重**的指控（新仓的历史自一条初始提交起算，取不到旧对象是**正常状态**）。

**乙钉在一个编号上，而那个编号已经不写在仓库里了**（写在仓外留档，由
`mcp_server/paths.py` 读，打印时只用它的标签）：编号本身就是钥匙 —— 七位缩写在托管站点上
照样能解析回旧对象，所以公开文档里只留标签。钉的是**修复前那一版**而不是 `HEAD`：
钉 `HEAD` 的话，这份报告在修复提交之后会自己变一次，而本仓要求产物逐字节可复现
（`tools/check.py --snapshot`）。

## 口径

- **扫描用的模式是整张词表**（`selfcheck` 的三张表），不是点名的那几段。
  点名的那几段都是词表里的成员，所以这是**超集** —— 判据更严，不是更松。
- **模式不逐字写进本报告**：报告是产物。逐字写出那些词，报告自己就成了新载体
  （本仓踩过三次的回路，见 `DECISIONS.md` D-30）。要看是哪几段，读词表文件。
- **历史编号也不逐字写进本报告**：同上，报告是公开产物，写缩写与写全号一样是钥匙，
  所以只打印标签（`旧编号·X`）。
- **命中数是「模式在文本里出现的次数」**，用子串匹配（`in`），不要求整词独立 ——
  这与 §3.4 那条 grep 的行为一致（grep 也是子串）。
- **不算时间戳之类的东西**：这份报告要能被 `--snapshot` 判为逐字节可复现。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 本地 0 命中、公网 0 命中、两边哈希相同、两条负控都按期望报红 |
| 1 | 有上面任一条不成立（**报告照写**，写明是哪一条） |
| 2 | 公网取不到（没网 / 仓库还没推 / 路径写错）—— **不算通过**，但和 1 分开 |
| 3 | **两条负控至少有一条没有实测依据**（拿不到样本 / 拿不到旧历史对象）—— 同样**不算通过** |

★ 本脚本**不进** `tools/check.py` 的闸门，也**不进** `verify_kit.EXIT_LEDGER`：
它要联网，而闸门与台账都是零网络的（那三条需要模型凭据的链路也已经不在闸门里）。
把它挂进闸门，等于让「clone 下来断网也能跑通」这句话不再成立。

★ 退出码 3 与闸门口径一致：那边的「未完成」是 5，意思一样 ——
**「没查」既不是通过，也不是失败**（`DECISIONS.md` D-44）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402
from mcp_server import selfcheck as sc  # noqa: E402
from mcp_server import verify_kit as vk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TARGET = Path("mcp_server") / "selfcheck.py"

# 降级演示那一步（跑闸门第 4 步）会顺手重写的**别人的**产物。
# 清单是实测出来的：26 会跑 19_verify 与 pytest，这两样分别写这两份。
DEGRADED_SIDE_EFFECTS = (REPORTS / "verify.md", REPORTS / "final_selfcheck.md")

# 被查的那个文件；公网地址由 `paths` 现算（地址一处可改）。
# 「修复前那一版」的编号**不在这里** —— 它写在仓外留档里，理由见文件头。

E_OK, E_FAIL, E_OFFLINE, E_NO_CONTROL = 0, 1, 2, 3

PASS, FAIL = "PASS", "FAIL"


def _sha256(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def scan(text: str) -> list[str]:
    """用整张词表扫一段文本，返回命中的条目（去重、保序）。"""
    out: list[str] = []
    for w in sc.wordlist_entries_in(text):
        if w not in out:
            out.append(w)
    return out


def count_occurrences(text: str) -> int:
    """模式在文本里出现的**次数**（不是命中了几个不同的条目）。"""
    n = 0
    for w in (*sc.FORBIDDEN_NAMES, *sc.MEDICAL_TERMS, *sc.FORBIDDEN_NUMBERS):
        n += text.count(w)
    return n


def fetch_remote(url: str, timeout: int = 30) -> tuple[int, bytes | str, str]:
    """取公网 raw。返回 `(HTTP 状态码或 -1, 字节内容或错误串, 用的哪个工具)`。

    先试 `curl.exe`（Windows 10+ 自带，也是任务书里点名的那条命令），
    起不来再退回 `urllib`。**用的是哪个，报告里写清楚** —— 换个取法数字可能不同，
    读的人得知道这份数字是怎么来的。
    """
    try:
        p = subprocess.run(["curl.exe", "-s", "-w", "\n%{http_code}", url],
                           capture_output=True, timeout=timeout)
        if p.returncode == 0 and p.stdout:
            body, _, code = p.stdout.rpartition(b"\n")
            return int(code.strip() or -1), body, "curl.exe"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read(), "urllib"
    except urllib.error.HTTPError as e:
        return e.code, f"HTTPError {e.code}", "urllib"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}", "urllib"


def local_text() -> str:
    return (ROOT / TARGET).read_text(encoding="utf-8")


def pre_fix_text(pre: str) -> str:
    """修复前那一版。取不到（比如换了一台没有那段历史的机器）返回空串。"""
    p = subprocess.run(["git", "-C", str(ROOT), "show", f"{pre}:{TARGET.as_posix()}"],
                       capture_output=True, timeout=120)
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""


def _rev_count() -> int:
    """本机这个仓库一共有多少笔提交（所有分支一起算）。读不出来返回 -1。"""
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--all", "--count"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return -1
    try:
        return int(p.stdout.strip()) if p.returncode == 0 else -1
    except ValueError:
        return -1


def synthetic_negative_control() -> dict:
    """★ 负控甲：拿三段**已知含词**的样本文本，试试这把尺子当下还灵不灵。

    样本不是新写的，而是复用 `selfcheck.NEGATIVE_FIXTURES` 里 S1/S2/S3 三条 ——
    它们的正文**从仓外词表现取**（`_wordlist_sample`），所以本文件里没有任何字面量。
    判据用的是与负控乙**同一个** `scan()` 与 `count_occurrences()`。

    ★ 样本只在内存里过一遍，**不落盘**：它含真实的敏感串，写到磁盘上就等于
      又造了一处明文 —— 那正是这一整套修复要拆掉的东西。

    为什么非有它不可：乙要读本机的旧历史对象，换台机器就没有了。有了甲，
    「尺子还灵」这件事**在任何机器上**都有实测依据 —— 原来没有这层覆盖。
    """
    if not sc.WORDLIST_AVAILABLE:
        return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "rows": [],
                "note": f"{sc.WORDLIST_PENDING} —— 样本正文就是从词表里取的，"
                        f"没有词表就没有样本"}
    rows: list[str] = []
    n_kinds = n_occur = 0
    for cid, _rel, body, what in sc.NEGATIVE_FIXTURES:
        if cid not in sc.WORDLIST_FIXTURES:
            continue                    # 只取「正文来自词表」的那三条
        k, o = scan(body), count_occurrences(body)
        n_kinds += len(k)
        n_occur += o
        rows.append(f"{cid}（{what}）命中 {len(k)} 条 / 出现 {o} 次")
    if not rows:
        return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "rows": [],
                "note": "取不到可用的合成样本 —— 本项没有实测依据"}
    ok = n_kinds > 0 and n_occur > 0
    return {"status": PASS if ok else FAIL, "n_kinds": n_kinds, "n_occur": n_occur,
            "rows": rows,
            "note": "；".join(rows) + ("" if ok else
                                      "　★ 尺子当场失灵：上面那几个 0 不能当结论")}


def old_history_rows() -> dict:
    """★ 负控乙：修复前那一版里还读不读得出那些字。

    返回 `{status, n_kinds, n_occur, sha, note, hits}`。`status` 有三个值：

    - `PASS` —— 取得到那一版，而且**命中 > 0**（残留还在 = 历史没被人无声重写）；
    - `FAIL` —— 取得到而命中 0（有人在没人同意的时候重写过），或者本机明明存着
      多笔历史却取不回那个对象（同上）；
    - `无实测依据` —— 本机根本没有那段历史（新仓的正常状态）。**既不是通过，
      也不是失败。** ★ 这个分支是这次特意加的：以前这种情况判 FAIL，而它的措辞是
      「那个提交也不在了 —— 历史被重写过」。对一台新 clone 的机器来说，
      那是一条**不实且严重**的指控。
    """
    pre, why = paths.pre_fix_commit()
    label, _ = paths.pre_fix_label()
    shown = label or "修复前那一版"
    if not sc.WORDLIST_AVAILABLE:
        # ★ 读不到词表时扫出来必然是 0 命中，而本项的判据是「命中 > 0 才算过」——
        #   于是同一份空的命中列表会把这一条推成 FAIL，措辞还是「历史被重写过」。
        #   那是**不实且严重**的指控（交付级第 15 项踩过同一个坑），所以显式判「没有依据」。
        return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "sha": "-",
                "note": f"{sc.WORDLIST_PENDING} —— 扫出来必然是 0 命中，"
                        f"而本项要求「命中 > 0 才算过」，所以本项没有实测依据："
                        f"既不是通过，**也不等于**「历史被重写过」", "hits": []}
    if not pre:
        return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "sha": "-",
                "note": f"{why} —— 于是「那一版里还读得出那些字吗」没有实测依据",
                "hits": []}
    text = pre_fix_text(pre)
    if not text:
        n_all = _rev_count()
        if n_all < 0:
            return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "sha": "-",
                    "note": "本机读不出 git 历史（`git rev-list` 跑不起来）——"
                            "本项没有实测依据", "hits": []}
        if n_all > 1:
            return {"status": FAIL, "n_kinds": 0, "n_occur": 0, "sha": "-",
                    "note": f"本机存着 {n_all} 笔提交，却取不回 `{shown}` 那一版",
                    "hits": [f"取不回 `{shown}` 那一版"
                             "（本机明明存着多笔历史 —— 历史被重写过）"]}
        return {"status": "无实测依据", "n_kinds": 0, "n_occur": 0, "sha": "-",
                "note": f"本机只有 {n_all} 笔提交（历史自一条初始提交起算），"
                        f"那段旧历史不在本机、存档在维护者本地 —— "
                        f"本项**没有实测依据**：既不是通过，**也不等于**"
                        f"「历史被重写过」", "hits": []}
    k, o = scan(text), count_occurrences(text)
    ok = bool(k)
    return {"status": PASS if ok else FAIL, "n_kinds": len(k), "n_occur": o,
            "sha": _sha256(text.encode("utf-8")),
            "note": f"`{shown}` 那一版实测：**命中 {len(k)} 条 / 出现 {o} 次** ——"
                    "命中 > 0 才是「残留仍在、历史未被重写」的证据",
            "hits": [] if ok else
                    [f"命中 {len(k)} 条（>0 才算过）—— 有人重写过这段历史"]}


def degraded_demo() -> dict:
    """★ 另一条要落的证据：**读不到词表时判的是 SKIP，不是 PASS。**

    做法：把 `MCP_TOOLKIT_WORDLIST` 指到一个不存在的文件，跑闸门的第 4 步。
    预期是退出码 5（「未完成」），而不是 0（通过）——判 0 就等于说
    「本机没有词表，所以仓库是干净的」，那正是这一整套检查要防的谎。

    ★ 用它而不是「临时把词表改名」：那个做法在中断时会留下一个**没有词表的仓库**，
    而且它改的是真文件。指一个不存在的路径，效果一样，代价是零。

    ★ 但这第 4 步**自己会重写两份产物**：`26_final_selfcheck.py` 会跑
    `19_verify.py` 与 `pytest`，于是 `reports/verify.md` 与
    `reports/final_selfcheck.md` 被刷成「本机没有词表」的样子（实测踩过：
    跑完 27，`git status` 里躺着两份降级的报告，看着和真的一样）。
    所以整个子进程外面套一层按字节还原，并把前后 sha256 记进 `evidence`。
    """
    bogus = Path(tempfile.gettempdir()) / "mcp-guarded-toolkit-no-such-wordlist.py"
    if bogus.exists():                      # 万一真有人建了这么个文件
        return {"status": "无实测依据", "cmd": "-", "code": -1,
                "note": f"探测用的路径居然存在（{bogus.name}），没敢动它",
                "evidence": []}
    env = {**os.environ, "MCP_TOOLKIT_WORDLIST": str(bogus)}
    cmd = [sys.executable, "tools/check.py", "--only", "4"]
    evidence: list[dict] = []
    try:
        with vk.ProductsPreserved(DEGRADED_SIDE_EFFECTS) as guard:
            p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=1800)
        evidence = guard.evidence
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "无实测依据", "cmd": " ".join(cmd), "code": -1,
                "note": f"没跑起来：{type(e).__name__}", "evidence": evidence}
    tail = (p.stdout or "").strip().splitlines()[-3:]
    return {"status": PASS if p.returncode == 4 else FAIL,
            "cmd": " ".join(cmd[1:]),
            "code": p.returncode,
            "note": "；".join(x.strip() for x in tail),
            "evidence": evidence}


def render(rows: list[dict], degraded: dict) -> str:
    L: list[str] = []
    A = L.append
    A("# 明文桥修复核验（本地 + 公网）")
    A("")
    A("## 口径")
    A("")
    A("- **脚本**：`experiments/27_leak_fix_verify.py`（本文件由它生成，可重跑）")
    A("- **被查的文件**：`mcp_server/selfcheck.py` —— 它曾经逐字写着公司名 / 课程名 / "
      "原仓库名 / 原项目的数字，而且住在扫描的豁免清单里，所以别的检查**都不会**"
      "报它。这一份是专门查它的")
    A("- **模式**：`selfcheck` 的三张词表**全部**（`FORBIDDEN_NAMES` + `MEDICAL_TERMS` + "
      "`FORBIDDEN_NUMBERS`），**逐条**在文本里找。这是**超集**：既然全部条目都是 0 命中，"
      "那么单挑其中任何几条来查必然也是 0 —— 反过来不成立。所以口径取全表，"
      "不做「挑几个样本查一下」那种看起来更省事、实际覆盖面更窄的写法")
    A("- **为什么不把那几段逐字写在这里**：这份报告是产物。逐字写出那些词，"
      "报告自己就成了新的载体（本仓踩过三次的回路，见 `DECISIONS.md` D-30）。"
      "要看是哪几段，读词表文件（路径见 `BLOCKERS.md` 卡点 3）")
    A("- **命中数**：模式在文本里出现的**次数**，子串匹配（`in`）—— 与那条 grep 同口径")
    A("- **词表不在仓库里**：读不到时本脚本**不下结论**（判 2 之外的另一条路是"
      "「无实测依据」，见下面的判定栏），因为「词表是空的」会让任何文本都 0 命中")
    A("- **负控甲**：三段**已知含词**的合成样本（正文从仓外词表现取，只在内存里过一遍）。"
      "它**必须**报出命中 —— 否则这把尺子当场就是坏的，上面那几个 0 只说明检查失灵，"
      "不说明修好了。它不读 git 历史，所以**任何机器上都跑得起来**")
    A("- **负控乙**：修复前那一版（词表还在 `selfcheck.py` 里的最后一版），"
      "历史编号只写标签、真值在仓外留档里。它**必须**报出命中 —— "
      "命中 > 0 同时是「那段旧历史还在、没被人无声重写」的证据。"
      "本机没有那段历史时它判**没有实测依据**（不是失败）")
    A("- **公网取法**：先 `curl.exe`（Windows 自带的那个），起不来才退回 `urllib`。"
      "**本次成功的是哪一种，不写进这份产物** —— 那是逐次可变的读数"
      "（实测：同一台机器上一次成功的是 `curl.exe`、下一次是 `urllib`），"
      "写进去这份报告就不可能逐字节复现，理由与 `DECISIONS.md` D-38 拒绝"
      "时钟读数完全相同。它与下面几行的 sha256 / 命中数**无关**："
      "取回来的是同一份字节，第 2、3 项比的就是这个。"
      "本次用的是哪一种，跑的时候打在控制台上")
    A("")
    A("| # | 查的是哪一份 | sha256（前 12 位） | 命中条目数 | 出现次数 | 判定 |")
    A("|---|---|---|---|---|---|")
    for r in rows:
        A(f"| {r['n']} | {r['what']} | `{r['sha'][:12]}` | {r['n_kinds']} | "
          f"{r['n_occur']} | {r['status']} |")
    A("")
    A("## 逐条")
    A("")
    for r in rows:
        A(f"### {r['n']} {r['what']} —— {r['status']}")
        A("")
        A(f"- **口径**：{r['caliber']}")
        A(f"- **sha256**：`{r['sha']}`")
        A(f"- **命中条目数**：{r['n_kinds']}；**出现次数**：{r['n_occur']}")
        if r["note"]:
            A(f"- **备注**：{r['note']}")
        A("")
    A("## 读不到词表时会怎样（★ 这一条和上面三条同等重要）")
    A("")
    A("「词表搬到仓库外面」有一个必须交代的后果：**clone 下来的人没有词表**。")
    A("那时候检查是判「通过」还是判「没查」？判错的话，这个修法就从")
    A("「把门锁上」变成了「把报警器拆掉」—— 看起来更安静，实际更糟。")
    A("")
    A("所以这里把那个状态**真的跑一遍**：把 `MCP_TOOLKIT_WORDLIST` 指到一个")
    A("不存在的文件，再跑闸门的第 4 步。")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| 命令 | `python {degraded['cmd']}`（带一个指向不存在文件的"
      f"`MCP_TOOLKIT_WORDLIST`） |")
    A(f"| 实测退出码 | **{degraded['code']}** |")
    A(f"| 判定 | {degraded['status']} |")
    A(f"| 输出尾部 | {degraded['note']} |")
    A("")
    A("- **这一步会顺手重写闸门自己的两份报告**：第 4 步里的 `19_verify.py` 与 "
      "`pytest` 会真的跑一遍，于是 `reports/verify.md` 与 "
      "`reports/final_selfcheck.md` 被刷成「本机没有词表」的样子"
      "（PASS 变 SKIP，数字还对得上，看上去和真的一样）。那两份是**闸门的**产物，"
      "一次核验实验不该改写它们 —— 所以本脚本在跑之前按**字节**存下来、跑完原样放回。"
      "存的是字节不是文本（换行风格也在还原范围内），前后 sha256 实测如下，"
      "「还原过」这句话因此是可核的：")
    A("")
    if degraded.get("evidence"):
        A("| 被重写又还原的文件 | 跑之前 sha256 | 跑完 sha256 | 还原 |")
        A("|---|---|---|---|")
        for e in degraded["evidence"]:
            A(f"| `reports/{e['name']}` | `{e['before']}` | `{e['after']}` | "
              f"{'是' if e['restored'] else '**否**'} |")
        A("")
        if not all(e["restored"] for e in degraded["evidence"]):
            A("★ **有文件没还原成原样**：上面写着「否」的那几行要人去看一眼。"
              "这份报告自己就成了「产物被改过」的证据。")
            A("")
    else:
        A("（这一步没跑起来，所以没有还原记录 —— 也就没有「有没有被改过」的实测依据）")
        A("")
    A("- 代价说清：还原发生在**子进程跑完之后**。要是那一步中途被强杀"
      "（比如手工 Ctrl+C），这两份报告会停在降级状态 —— 重跑一次闸门即可复原，"
      "`git status` 会明确告诉你有哪两份被改过")
    A("- 还有一条**没有**被这层还原覆盖：`27` 自己写的 "
      "`reports/leak_fix_verify.md`。它是本脚本的产物，本来就该被改写")
    A("")
    A("- **期望的是 4**：闸门的退出码 = 卡住那一步的序号（第 4 步），而第 4 步自己")
    A("  报的是退出码 **5 = 「未完成」**（`experiments/26_final_selfcheck.py` 文件头"
      "有那张表）。**0 才是错的** —— 0 等于说「本机没有词表，所以仓库是干净的」")
    A("- 第 1~3 项在那种状态下判 `SKIP`，理由文本是"
      "「词表未随仓分发（见维护者本地）」，**不含任何路径** ——"
      "本仓有一条检查不许产物出现作者机器的目录")
    A("- 依赖词表的 pytest 用例同样判 SKIP 而不是假装通过")
    A("（`tests/test_repo_hygiene.py` 里带 `needs_wordlist` 标记的那几条）")
    A("- 最小动作写在 `BLOCKERS.md` 卡点 3：造一个词表文件、设一个环境变量")
    A("")
    A("## 这张表**不能**说明什么")
    A("")
    A("- 它只管 `mcp_server/selfcheck.py` 这**一个文件**。仓库其余部分的红线由 "
      "`experiments/19_verify.py` 的 S1~S9 与 `tests/test_repo_hygiene.py` 管，"
      "那是另一套检查，不在这里重复")
    A("- 「0 命中」的含义是「词表里的东西一处都没有」，**不是**「绝对干净」——"
      "词表外还有别的说法能指回同一件事，那些扫不出来")
    A("- 它**不**说明 `git` 历史里没有这些字。历史是另一件事 ——"
      "这次修的是**工作区**，没有重写历史，所以修复之前那些提交里照样查得到。"
      "实测证据与三条可选路径（各自要付什么代价、推荐哪条）写在 "
      "`BLOCKERS.md` 的**卡点 4**，那里也写明了**一条都没有执行**")
    A("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="核验明文桥已拆：本地 + 公网")
    ap.add_argument("--out", default=str(REPORTS / "leak_fix_verify.md"),
                    help="报告落盘位置")
    args = ap.parse_args()

    rows: list[dict] = []

    # ---- 1 本地
    lt = local_text()
    lb = lt.encode("utf-8")
    k, o = scan(lt), count_occurrences(lt)
    rows.append({
        "n": "1", "what": "工作区 `mcp_server/selfcheck.py`",
        "sha": _sha256(lb), "n_kinds": len(k), "n_occur": o,
        "status": PASS if not k else FAIL,
        "caliber": "读工作区那份文件，用整张词表做子串扫描",
        "note": "" if not k else "★ 工作区里还留着词表条目",
    })

    # ---- 负控甲：三段已知含词的合成样本（不读 git 历史，任何机器上都跑得起来）
    synth = synthetic_negative_control()
    rows.append({
        "n": "负控甲", "what": "三段已知含词的合成样本",
        "sha": "-", "n_kinds": synth["n_kinds"], "n_occur": synth["n_occur"],
        "status": synth["status"],
        "caliber": "把 `selfcheck.NEGATIVE_FIXTURES` 里 S1/S2/S3 三条的正文喂给"
                   "**同一个** `scan()` / `count_occurrences()`；正文从仓外词表现取，"
                   "只在内存里过一遍，**不落盘**",
        "note": synth["note"],
    })

    # ---- 负控乙：修复前那一版（要本机存着那段旧历史）
    hist = old_history_rows()
    rows.append({
        "n": "负控乙", "what": "修复前那一版里的同一个文件",
        "sha": hist["sha"], "n_kinds": hist["n_kinds"], "n_occur": hist["n_occur"],
        "status": hist["status"],
        "caliber": "`git show <仓外留档里的那个编号>:mcp_server/selfcheck.py`，"
                   "同一张词表、同一个函数",
        "note": hist["note"] if hist["status"] != FAIL
                else f"{hist['note']}　{'；'.join(hist['hits'])}",
    })

    # ---- 2 公网（地址现算：解析不到就判「没有实测依据」，不去猜）
    raw_url, raw_why = paths.repo_raw_url(TARGET.as_posix())
    code, body, tool = -1, raw_why or "", "-"
    if raw_url:
        code, body, tool = fetch_remote(raw_url)
    if not raw_url or code != 200 or not isinstance(body, bytes):
        reason = (raw_why if not raw_url
                  else (body if isinstance(body, str) else f"HTTP {code}"))
        rows.append({
            "n": "2", "what": "公网 raw 上的同一个文件",
            "sha": "-", "n_kinds": 0, "n_occur": 0, "status": "取不到",
            "caliber": "取本仓地址 → `curl.exe -s <raw url>`（退回 urllib）",
            "note": f"没取到：{reason}。**公网这一条没有实测依据** —— "
                    "要么没网、要么还没推上去、要么地址解析不出来",
        })
        remote_ok = False
    else:
        rt = body.decode("utf-8", "replace")
        rk, ro = scan(rt), count_occurrences(rt)
        remote_ok = not rk
        rows.append({
            "n": "2", "what": "公网 raw 上的同一个文件",
            "sha": _sha256(body), "n_kinds": len(rk), "n_occur": ro,
            "status": PASS if not rk else FAIL,
            "caliber": f"`curl.exe -s {raw_url}`（退回 urllib），同一张词表",
            "note": "" if not rk else "★ 公网上那份还留着词表条目",
        })

        # ---- 3 两边是不是同一份
        same = _sha256(body) == _sha256(lb)
        rows.append({
            "n": "3", "what": "本地与公网是不是**同一份**",
            "sha": _sha256(body), "n_kinds": 0, "n_occur": 0,
            "status": PASS if same else FAIL,
            "caliber": "比两边的 sha256。相同 = 公网上那份就是本地这份",
            "note": "" if same else
                    "★ 哈希不同：公网上是**另一个版本**。"
                    "即使它也 0 命中，也不能说「本地这份推上去了」",
        })

    # ---- 4 降级：读不到词表时判 SKIP 而不是 PASS
    degraded = degraded_demo()

    ok = ((not scan(lt)) and remote_ok and synth["status"] == PASS
          and degraded["status"] == PASS
          and all(r["status"] != FAIL for r in rows))

    REPORTS.mkdir(parents=True, exist_ok=True)
    from mcp_server import eval_runner as runner
    from mcp_server import verify_kit as vk
    text, self_carrier = vk.finalize_report_text(
        render(rows, degraded), REPORTS / "leak_fix_verify.md")
    if self_carrier:
        print(f"  注：本次渲染有 {len(self_carrier)} 处自毒，已按登记豁免规则掩码")
    banned = runner.write_report(REPORTS / "leak_fix_verify.md", text)

    for r in rows:
        print(f"  [{r['status']:<6}] {r['n']:<5} {r['what']:<26} "
              f"命中 {r['n_kinds']} 条 / 出现 {r['n_occur']} 次")
    # 逐次可变的读数只打在控制台上，不进产物（理由见报告的口径节）
    print(f"  （公网取法：本次成功的是 {tool}）")
    print(f"  [{degraded['status']:<6}] 4     读不到词表时的判定"
          f"（闸门第 4 步退出码 {degraded['code']}，期望 4）")
    print(f"\n报告：{paths.rel(REPORTS / 'leak_fix_verify.md')}")

    if banned:
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_FAIL
    # 判定顺序：**真失败** > 「没实测依据」 > 公网取不到 > 通过。
    # 一个真的红不该被「没查」盖住，反过来「没查」也不该被写成红。
    if any(r["status"] == FAIL for r in rows):
        print("[FAIL] 有检查没通过，详见报告", file=sys.stderr)
        return E_FAIL
    no_control = [r["n"] for r in rows if r["status"] == "无实测依据"]
    if no_control:
        print(f"[未完成] {'、'.join(no_control)} 没有实测依据 —— "
              "尺子没验过，「0 命中」就不能当结论。这不是通过，也不是失败",
              file=sys.stderr)
        return E_NO_CONTROL
    if not ok:
        print("[FAIL] 有检查没通过，详见报告", file=sys.stderr)
        return E_FAIL
    if not remote_ok and code != 200:
        print("[未完成] 公网那一份没取到：本地结论成立，但「公网干净」没有实测依据",
              file=sys.stderr)
        return E_OFFLINE
    print("[OK] 本地 0 命中、公网 0 命中、两边同一份、两条负控都按期望报红、"
          "读不到词表时判 SKIP")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
