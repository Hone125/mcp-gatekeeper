"""交付级自检：**15 项**，回答「这一批交付物能不能拿出去」。

## 和 `experiments/26_final_selfcheck.py` 那 9 项是什么关系

不是替代，是两层，各有各的问题：

- **9 项那层问的是**「这个仓库本身有没有越过自己划的红线」—— 它只看仓库。
- **15 项这层问的是**「这一批要交出去的东西（仓库 + 交付物 + 远端）整体是否成立」——
  它的检查对象**跨出了仓库**：交付物在仓库外面，远端在网络上，
  还有一项要问 git 历史。

两层都用 `mcp_server/selfcheck.py` 的同一批扫描函数（**单一口径**），
所以 15 项里凡是扫仓库的那几项，和第 1~3 项必然同结论 —— 这不是冗余，
是**故意让它们互相印证**：两边万一不一致，说明有一边被绕过了。

## 三条设计规矩（每一条都是为了不撒谎）

1. **算不了的判 SKIP，不判 PASS。** 交付物不在本机、或者断网，
   这一项就**没有实测依据**。「没查」写成「0 命中」是这套检查最想防的那种谎
   （`DECISIONS.md` D-44）。
2. **需要联网的两项默认不跑。** 闸门必须能在一台断网的机器上跑完 ——
   否则「clone 下来就能验」这句话就不成立了。联网的实测落在
   `reports/leak_fix_verify.md`（公网那一项）和 `reports/delivery_selfcheck.md`
   （带 `--online` 跑出来的那一份）。
3. **递归的项不许自己跑自己。** 第 11 项问的是「守门链整体退出码」，
   而本模块正是被守门链调用的 —— 自己跑自己会无限套娃。
   所以这一项不重新跑闸门，而是**由四步的退出码推得**：
   `tools/check.py` 的结论就是「四步里有几步不是 0」，四步全 0 时它必然返回 0。
   推论链写在那一项的「判据」里，读的人可以自己核。

## 交付物那一半怎么找到

交付物不在仓库里（**也不该在**：它是个人材料，不属于这个开源仓库）。
所以路径从环境变量 `DELIVERY_RESUME_DIR` 读，没设就整组判 SKIP。
**理由里不带路径** —— 这份报告会进仓库，而本仓有一条检查不许产物里出现
作者机器的目录（`selfcheck.ABS_PATH_PATTERN`）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from mcp_server import paths
from mcp_server import selfcheck as sc

ROOT = sc.ROOT
REPORTS = ROOT / "reports"

PASS, FAIL, SKIP, ERROR = "PASS", "FAIL", "SKIP", "ERROR"

ENV_RESUME_DIR = "DELIVERY_RESUME_DIR"

# 交付物的版本标签。★ 换版本**只改这一行**：下面两个选择器与它们的报错文案都引它。
# 为什么收成一处：这一组检查是**按文件名认交付物**的。标签散在好几处时，换一版
# 就会出现「检查照跑、查的却是上一份」—— 而且报告照样是绿的（实测见 D-55）。
RESUME_TAG = "6.0"

RESUME_PENDING = (f"本机没设 {ENV_RESUME_DIR}：交付物不在仓库里，"
                  "指到它所在的目录才会检查这一项")
NET_PENDING = ("本项要联网，按设计不在离线闸门里跑；"
               "联网实测落在 reports/leak_fix_verify.md"
               "（带 --online 跑 experiments/28_delivery_selfcheck.py 会就地跑）")

# 交付物里另外两个必须消失的说法。它们不属于仓库的语言红线（所以不进词表），
# 只属于这一次交付的措辞纪律：一个是「本仓是原项目的工具化封装」这个已被撤下的定位，
# 一个是「见上方某条」这种在本仓不存在、只在交付物里存在过的交叉指代。
RESUME_EXTRA_FORBIDDEN = ("实习链路之上", "上方 MCP 条")

# 措辞纪律的额外四个词。前六个用 `selfcheck.OVERSTATED`（**单一口径**，
# 绝不在这里抄一遍 —— 抄一遍就等于多了一处要同步的地方）。
OVERSTATED_EXTRA = ("掉点", "完全", "彻底", "零风险")

REPO_PENDING = ("解析不到本仓地址（环境变量、`origin`、仓外留档三处都没有）——"
                "本项**没有实测依据**，不是通过")


@dataclass
class Item:
    n: str
    title: str
    caliber: str
    cmd: str
    status: str
    n_hits: int = 0
    note: str = ""
    hits: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"n": self.n, "title": self.title, "caliber": self.caliber,
                "cmd": self.cmd, "status": self.status, "n_hits": self.n_hits,
                "note": self.note, "hits": self.hits}


def _fmt(hit) -> str:
    """命中行**掩码之后**才落盘。

    ★ 不能省。逐字引用一处命中，报告自己就变成新的载体 ——
    本仓为这个回路踩过三次（`DECISIONS.md` D-30）。
    """
    return f"{hit.rel}:{hit.line_no} {sc.mask_all(hit.text).strip()[:80]}"


def _from_hits(n, title, caliber, cmd, hits, pending="", note="") -> Item:
    if pending and not hits:
        return Item(n, title, caliber, cmd, SKIP, 0, f"{pending} —— 本项**没有实测依据**，"
                                                    f"不是通过", [])
    return Item(n, title, caliber, cmd, FAIL if hits else PASS, len(hits),
                note, [_fmt(h) for h in hits[:8]])


# ---------------------------------------------------------------- 仓库这一半

def check_1_names_in_repo() -> Item:
    return _from_hits(
        "1", "仓库里没有识别性名称",
        "扫全仓所有文本文件，比对词表第一张表（`selfcheck.FORBIDDEN_NAMES`）；"
        "命中 0 才算过。**含**本次修复前的自检脚本自己 —— 它已不在豁免区",
        "sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL)",
        sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL), pending=_wl_pending())


def check_2_numbers_in_repo() -> Item:
    real, coin = sc.split_coincidences(
        sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, word_boundary=True))
    note = (f"另有 {len(coin)} 处已核验巧合（登记在 `VERIFIED_COINCIDENCES`，"
            f"只放行到具体路径）："
            + "；".join(f"`{h.rel}:{h.line_no}`" for h in coin)) if coin else ""
    return _from_hits(
        "2", "仓库里没有那些不该出现的数字",
        "扫全仓比对词表第三张表（`FORBIDDEN_NUMBERS`），带词边界"
        "（`(?<![0-9A-Za-z_.])数字(?![0-9A-Za-z_])`），"
        "**已核验巧合除外**（那些是真实统计值，逐条列在报告里而不是抹掉）",
        "sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, word_boundary=True)",
        real, pending=_wl_pending(), note=note)


def check_3_terms_in_repo() -> Item:
    return _from_hits(
        "3", "自己写的文件里没有领域术语",
        "只扫**本仓作者写的**文件（`sc.SCOPE_AUTHORED`，第三方原文与语料不算），"
        "比对词表第二张表（`MEDICAL_TERMS`）",
        "sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED)",
        sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED), pending=_wl_pending())


def _wl_pending() -> str:
    """词表读不到时，凡是靠词表的项都判 SKIP。

    ★ 这一条不能省，而且是最容易漏的一条：词表为空时 `sc.grep()` 返回 `[]`，
    于是「命中 0」—— 判 PASS。**「没查」又被写成了「干净」**。
    这正是把词表搬出仓库之后新出现的那个坑（`DECISIONS.md` D-46）。
    """
    return sc.WORDLIST_REASON


def fetch_remote_hits() -> tuple[list | None, str]:
    """拉远端 `main` 上那份文件，用**同一套**词表扫。返回 `(命中列表或 None, 说明)`。

    `None` 表示**没取到** —— 与「取到了、而且干净」（空列表）是两件事，
    第 4 项的判据就建在这个区分上。

    ★ 写在这里、不写在驱动脚本里的理由：`experiments/` 下的脚本以数字开头、
    **不能被 import**，于是「取回算不算成功」这条判断没有用例守得住 ——
    它第一次真跑（`--online`）就踩了：`fetch_remote` 成功时给的是
    **HTTP 200 + bytes**，而调用处按「0 + str 才算成功」判，
    于是取回成功反被读成「没取到」。判据搬进本模块之后，用例能直接钉住它。
    """
    v27 = _load_27()
    if v27 is None:
        return None, "取不到 `experiments/27_leak_fix_verify.py`（取回与扫描的口径在它那儿）"
    url, why = paths.repo_raw_url(v27.TARGET.as_posix())
    if not url:
        return None, why
    code, body, tool = v27.fetch_remote(url)
    if code != 200 or not isinstance(body, bytes):
        why = body if isinstance(body, str) else f"HTTP {code}"
        return None, f"{tool} 没取到：{why}"
    return v27.scan(body.decode("utf-8", errors="replace")), f"{tool}（HTTP {code}）"


def check_4_public_raw(online: bool, _deps: dict) -> Item:
    caliber = ("把远端 `main` 上的 `mcp_server/selfcheck.py` 原样拉下来，"
               "用**同一套**词表扫描 —— 本地干净不等于公网干净，"
               "这一项就是那两者的差值")
    cmd = "curl.exe -s <raw url>/mcp_server/selfcheck.py"
    if not online:
        return Item("4", "公网 raw 上的同一份文件同样干净", caliber, cmd,
                    SKIP, 0, f"{NET_PENDING} —— 本项**没有实测依据**，不是通过", [])
    slug, why = paths.repo_slug()
    if not slug:
        return Item("4", "公网 raw 上的同一份文件同样干净", caliber, cmd,
                    SKIP, 0, f"{why} —— 本项**没有实测依据**，不是通过", [])
    hits = _deps.get("remote_hits")
    if hits is None:
        return Item("4", "公网 raw 上的同一份文件同样干净", caliber, cmd,
                    ERROR, 0, "联网取回失败（不是「干净」，是「没取到」）", [])
    return Item("4", "公网 raw 上的同一份文件同样干净", caliber, cmd,
                FAIL if hits else PASS, len(hits),
                _deps.get("remote_note", ""), [_fmt(h) for h in hits[:8]])


# -------------------------------------------------------------- 交付物那一半

def resume_html() -> tuple[Path | None, str]:
    d = os.environ.get(ENV_RESUME_DIR, "").strip()
    if not d:
        return None, RESUME_PENDING
    p = Path(d)
    if not p.is_dir():
        return None, f"{ENV_RESUME_DIR} 指到的不是一个目录"
    cands = sorted(x for x in p.glob("*.html") if RESUME_TAG in x.name)
    if len(cands) != 1:
        return None, (f"目录下文件名里带 `{RESUME_TAG}` 的 HTML 有 {len(cands)} 个，"
                      "需要恰好一个（多了就说不清在查哪一份）")
    return cands[0], ""


def resume_pdf() -> tuple[Path | None, str]:
    d = os.environ.get(ENV_RESUME_DIR, "").strip()
    if not d:
        return None, RESUME_PENDING
    cands = sorted(x for x in Path(d).glob("*.pdf") if RESUME_TAG in x.name)
    if len(cands) != 1:
        return None, (f"目录下文件名里带 `{RESUME_TAG}` 的 PDF 有 {len(cands)} 个，"
                      "需要恰好一个")
    return cands[0], ""


def _resume_scan(needles: list[str], word_boundary: bool = False):
    """在交付物 HTML 上扫一组串。返回 `(hits, pending)`。

    这里**不复用** `sc.grep`：那个函数只在仓库范围内走（`SCOPE_*` 都相对仓库根），
    而交付物在仓库外面。但**词表本身**是同一份 —— 口径没换，只是换了个目录。
    """
    p, why = resume_html()
    if p is None:
        return None, why
    text = p.read_text(encoding="utf-8", errors="replace")
    hits: list[tuple[str, int, str]] = []
    for needle in needles:
        if word_boundary:
            pat = re.compile(r"(?<![0-9A-Za-z_.])" + re.escape(needle)
                             + r"(?![0-9A-Za-z_])")
        else:
            pat = re.compile(re.escape(needle))
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append((p.name, i, line))
    return hits, ""


def _from_resume(n, title, caliber, needles, word_boundary=False, note="") -> Item:
    hits, why = _resume_scan(needles, word_boundary)
    if hits is None:
        return Item(n, title, caliber, f"扫 {ENV_RESUME_DIR} 下的 HTML", SKIP, 0,
                    f"{why} —— 本项**没有实测依据**，不是通过", [])
    shown = [f"{rel}:{ln} {sc.mask_all(tx).strip()[:80]}" for rel, ln, tx in hits]
    return Item(n, title, caliber, f"扫 {ENV_RESUME_DIR} 下的 HTML",
                FAIL if hits else PASS, len(hits), note, shown[:8])


def check_5_resume_stale() -> Item:
    """交付物上不许有**仓库标识**与旧数字。

    ★ 这里**不能**拿整张名字表去扫简历。词表第 1 张表的头几条是雇主名与内部
    系统名，而简历上写雇主是必须的 —— 那样写这一项永远红。要查的是
    「仓库标识「那一类，名单由词表自己划（`sc.RESUME_FORBIDDEN_NAMES`）；
    名单缺失时 `selfcheck` 会退回整张表（更严），那种情况下这一项会红，
    而不是静默放宽（`DECISIONS.md` D-47）。
    """
    if not sc.WORDLIST_AVAILABLE:
        return Item("5", "交付物里没有仓库标识与旧数字",
                    "扫交付物 HTML，比对**同一份词表**里的仓库标识与旧数字",
                    f"扫 {ENV_RESUME_DIR} 下的 HTML", SKIP, 0,
                    f"{_wl_pending()} —— 本项**没有实测依据**，不是通过", [])
    strict = ("；★ 词表没给交付物单独划名字范围，这里按**整张**名字表查（更严）"
              if sc.RESUME_NAMES_STRICT else "；雇主名与内部系统名那几条不在名单里 —— "
              "简历不写雇主就没法核实，那几条属于「仓库里不许有」，不适用于交付物")
    return _from_resume(
        "5", "交付物里没有仓库标识与旧数字",
        "扫交付物 HTML，比对**同一份词表**里划给交付物的那张名字表（仓库标识）"
        "与全部旧数字（子串匹配，不额外开词边界），外加两个只在交付物里出现过的说法",
        [*sc.RESUME_FORBIDDEN_NAMES, *sc.FORBIDDEN_NUMBERS, *RESUME_EXTRA_FORBIDDEN],
        word_boundary=False,
        note="数字那一类不单独开词边界之上的豁免：交付物**一处都不该有**，"
             "所以这里比仓库那一项更严（仓库那边有已核验巧合）" + strict)


def check_6_tool_count() -> Item:
    """工具数写的必须是 **6 个**，不是 8 个。

    ★ 判据写成「所有 `N 个工具` 里的 N 必须是 6」而不是「出现过 `6 个工具`」：
    后者在「6 个…8 个」同时出现时照样通过 —— 那正是这条要防的情况。
    """
    p, why = resume_html()
    caliber = ("正则抓出交付物里所有 `<数字> 个工具` 的说法，"
               "**每一个**都必须是 6 —— 出现别的数字、或者一个都没有，都算违规")
    if p is None:
        return Item("6", "工具数是 6 个（不是 8）", caliber,
                    f"扫 {ENV_RESUME_DIR} 下的 HTML", SKIP, 0,
                    f"{why} —— 本项**没有实测依据**，不是通过", [])
    text = p.read_text(encoding="utf-8", errors="replace")
    seen = set(re.findall(r"(\d+)\s*个工具", text))
    bad = sorted(n for n in seen if n != "6")
    hits = [f"{p.name} 出现 `{n} 个工具`" for n in bad]
    if not seen:
        hits = [f"{p.name} 一处 `N 个工具` 都没有"]
    return Item("6", "工具数是 6 个（不是 8）", caliber,
                f"扫 {ENV_RESUME_DIR} 下的 HTML",
                FAIL if hits else PASS, len(hits),
                f"抓到的写法：{sorted(seen) or '（无）'}", hits)


def check_7_tech_stack() -> Item:
    """实习那一节里不许出现 `MCP`。

    ★ 判据的范围是**实习经历那一节**，不是整份简历，也不靠 ` · MCP` 这种形状。
    两个反例都在真实交付物里：
    - 整份扫：MCP 条的标题与技术栈本来就写着 `MCP`，会把它自己判红；
    - 按 ` · MCP` 形状躲：MCP 条的技术栈是 `Python · MCP 官方 SDK`，
      形状一模一样 —— 想躲的那个写法，正是要抓的那个写法。
    """
    name, title = "7", "时间线不矛盾：实习那一节里没有 MCP"
    caliber = ("交付物「实习经历」一节（含技术栈行）里一处 `MCP` 都不许有 —— "
               "实习 2026.04-05、MCP 条 2026.06，写了就是时间线自相矛盾")
    cmd = f"扫 {ENV_RESUME_DIR} 下的 HTML"
    p, why = resume_html()
    if p is None:
        return Item(name, title, caliber, cmd, SKIP, 0,
                    f"{why} —— 本项**没有实测依据**，不是通过", [])
    text = p.read_text(encoding="utf-8", errors="replace")
    start, sec = _h2_section(text, "实习经历")
    if start < 0:
        return Item(name, title, caliber, cmd, FAIL, 1,
                    "交付物里找不到「实习经历」这一节 —— 结构变了，本项**无从判断**",
                    [f"{p.name} 没有 `<h2>实习经历</h2>`"])
    base = text[:start].count("\n") + 1
    hits = [f"{p.name}:{base + off} {line.strip()[:80]}"
            for off, line in enumerate(sec.splitlines()) if "MCP" in line]
    return Item(name, title, caliber, cmd, FAIL if hits else PASS, len(hits), "", hits)


def _h2_section(text: str, heading: str) -> tuple[int, str]:
    """取 `<h2>heading</h2>` 到下一个 `<h2>` 之间的那一段。

    返回 `(起始下标, 那一段)`；**找不到时返回 `(-1, "")`** —— 调用方必须把
    「找不到这一段」判红而不是放过：结构变了和内容干净，在报告上是两种东西。
    """
    i = text.find(f"<h2>{heading}</h2>")
    if i < 0:
        return -1, ""
    j = text.find("<h2>", i + 1)
    return i, text[i:j if j >= 0 else len(text)]


def _h3_blocks(section: str) -> list[tuple[str, str]]:
    """按 `<h3>` 把一段切成若干条目，返回 `[(去掉标签的标题, 条目全文)]`。"""
    marks = [(m.start(), m.group(1))
             for m in re.finditer(r"<h3>(.*?)</h3>", section, re.S)]
    out: list[tuple[str, str]] = []
    for k, (start, raw) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(section)
        out.append((re.sub(r"<[^>]+>", "", raw).strip(), section[start:end]))
    return out


# 字符类比 `\w` 更严：句末的全角括号、顿号不会被吞进网址里
GITHUB_URL = re.compile(r"(?:https?://)?github\.com/[A-Za-z0-9._\-]+/[A-Za-z0-9._\-]+")


def check_8_facts_link() -> Item:
    """MCP 条的事实行必须指向本仓。

    ★ 判据不是「出现过这个网址」，而是「出现次数 ≥ 1 且**那一条里没有指向别处的**」：
    只查前者的话，新链接加上、旧链接忘删，照样通过。

    ★ 范围是**标题带 `MCP` 的那个条目**，不是整份简历：交付物上本来就有另一个
    项目的公开仓库链接（犀牛鸟那条），整份扫会把那个正确的链接判成违规 ——
    而它判红之后，人只能去删一个本来就对的链接。

    ★ 网址**不要求带协议头**：简历里为了排版短，写的是 `github.com/…`。
    只认 `https://` 开头会把这种正常写法判成「一处都没有」——
    错的方向是「有链接而判成没有」，于是人去改一份本来就对的交付物。
    """
    name, title = "8", "MCP 条的 facts 链接指向本仓"
    caliber = ("MCP 那一条里必须出现**本仓地址**（**带不带协议头都算**），"
               "且**那一条**里不许出现指向另一个仓库的 github 地址"
               "（别的条目的仓库链接不管 —— 那是另一个项目）。"
               "本仓地址由 `mcp_server/paths.py` 现算（环境变量 → `origin` → 仓外留档），"
               "**这里不写死**：写死的话换地址要改两处，漏一处的表现是"
               "「检查照跑、只是查的是另一个仓库」")
    cmd = f"扫 {ENV_RESUME_DIR} 下的 HTML"
    p, why = resume_html()
    if p is None:
        return Item(name, title, caliber, cmd, SKIP, 0,
                    f"{why} —— 本项**没有实测依据**，不是通过", [])
    url, url_why = paths.repo_web_url()
    if not url:
        return Item(name, title, caliber, cmd, SKIP, 0,
                    f"{url_why} —— 本项**没有实测依据**，不是通过", [])
    text = p.read_text(encoding="utf-8", errors="replace")
    _, sec = _h2_section(text, "项目经历")
    blocks = [b for t, b in _h3_blocks(sec) if "MCP" in t]
    if len(blocks) != 1:
        return Item(name, title, caliber, cmd, FAIL, 1,
                    f"「项目经历」里标题带 `MCP` 的条目有 {len(blocks)} 个，"
                    "需要恰好一个 —— 说不清在查哪一条",
                    [f"{p.name} 标题带 `MCP` 的条目数 = {len(blocks)}"])
    found = GITHUB_URL.findall(blocks[0])
    norm = lambda u: u if u.startswith("http") else "https://" + u  # noqa: E731
    others = sorted({norm(u) for u in found} - {url})
    n_ok = sum(1 for u in found if norm(u) == url)
    hits = [f"MCP 条里的链接指向别处：{u}" for u in others]
    if n_ok == 0:
        hits.append(f"MCP 条里一处 {url} 都没有")
    return Item(name, title, caliber, cmd, FAIL if hits else PASS, len(hits),
                f"{url} 在 MCP 条里出现 {n_ok} 次", hits)


def check_9_resume_cost() -> Item:
    return _from_resume(
        "9", "交付物里没有金额字样",
        "扫交付物 HTML 里的 `成本` / `¥` / `花费` —— 这一批交付的数字全都不含单价，"
        "所以一个字都不该出现（**金额栏是空的，不等于免费**，见 D-25）",
        ["成本", "¥", "花费"])


def check_10_resume_overstated() -> Item:
    words = [*sc.OVERSTATED, *OVERSTATED_EXTRA]
    return _from_resume(
        "10", "交付物里没有夸大措辞",
        f"扫交付物 HTML 比对 `selfcheck.OVERSTATED`（{len(sc.OVERSTATED)} 个词，"
        f"与仓库那一项**同一份**）+ 本次另外拉黑的 {len(OVERSTATED_EXTRA)} 个"
        "（掉点 / 完全 / 彻底 / 零风险 —— 它们不是仓库红线，是这一批的措辞纪律）",
        words,
        note="这条**放宽不了**：可用措辞是「方向为 X，未达可判定水平」，"
             "写在 `DECISIONS.md` D-24")


# ------------------------------------------------------- 机器与远端那一半

def check_11_gate(_deps: dict) -> Item:
    rc = _deps.get("gate_rc")
    return Item("11", "守门链四项全过",
                "**不重新跑闸门**（本模块就是被闸门调用的，跑自己会无限套娃）。"
                "改为由四步的退出码推得：闸门返回什么，只看 `tools/check.py` 里"
                "那句 `first_bad` —— 四步全是 0 时 `first_bad` 恒为 0，"
                "所以闸门必然返回 0。四步各自的实测在第 12 项、本项上方"
                "第 8/9 项与 `reports/deliverable_check.md`",
                "python tools/check.py",
                SKIP if rc is None else (PASS if rc == 0 else FAIL),
                0, "未由本模块重新执行；判据是推论，不是本项自己跑出来的结论"
                   if rc is None else f"四步退出码：{_deps.get('steps_note', '')}",
                [] if rc in (0, None) else [f"闸门退出码 {rc}（非 0）"])


def check_12_pytest(_deps: dict) -> Item:
    rc = _deps.get("pytest_rc")
    n = _deps.get("pytest_n")
    if rc is None:
        return Item("12", "单元测试退出码 0", "跑 `python -m pytest -q`，只看退出码",
                    "python -m pytest -q", SKIP, 0, "未执行 —— 没有实测依据", [])
    return Item("12", "单元测试退出码 0",
                "跑 `python -m pytest -q`，只看退出码。**条数变化必须逐条说明**"
                "（新增用例写清为什么加、删的写清为什么删），"
                "所以条数也一并记在这里",
                "python -m pytest -q", PASS if rc == 0 else FAIL, 0,
                f"{n}；条数变化的逐条说明见 PROGRESS.md",
                [] if rc == 0 else [f"退出码 {rc}"])


def check_13_remote_sha(online: bool, _deps: dict) -> Item:
    caliber = ("用 GitHub API 读远端 `main` 的 sha，与本地 `git rev-parse HEAD` 比对 —— "
               "「我推上去了」这句话的机械判据。**不是**比对时间戳："
               "时钟不可复现（D-38）")
    if not online:
        return Item("13", "远端 main 的 sha == 本地 HEAD", caliber,
                    "GET api.github.com/repos/.../commits/main", SKIP, 0,
                    f"{NET_PENDING} —— 本项**没有实测依据**，不是通过", [])
    slug, why = paths.repo_slug()
    if not slug:
        # ★ 地址解析不出来时判 SKIP，不判 ERROR：ERROR 的措辞是「没读到」，
        #   会让人去查网络，而这里根本没有地址可查 —— 本项就是**没有实测依据**。
        return Item("13", "远端 main 的 sha == 本地 HEAD", caliber,
                    "GET api.github.com/repos/.../commits/main", SKIP, 0,
                    f"{why} —— 本项**没有实测依据**，不是通过", [])
    local, remote = _deps.get("local_head"), _deps.get("remote_sha")
    if not remote:
        return Item("13", "远端 main 的 sha == 本地 HEAD", caliber,
                    "GET api.github.com/repos/.../commits/main", ERROR, 0,
                    "没读到远端 sha（不是「不一致」，是「没读到」）", [])
    ok = local == remote
    # ★ 只打印**指纹**，不打印提交号（连前 7 位也不行）：7 位缩写在托管站点上照样
    #   解析回对象，而这份报告是公开产物 —— 写进去就是把钥匙挂在墙上（D-54）。
    #   指纹在有仓库的人那里可复核（`git rev-parse HEAD` → sha256 → 前 12 位）。
    lp, rp = sc.sha_fingerprint(local), sc.sha_fingerprint(remote)
    return Item("13", "远端 main 的 sha == 本地 HEAD", caliber,
                "GET api.github.com/repos/.../commits/main",
                PASS if ok else FAIL, 0 if ok else 1,
                f"本地指纹 {lp} / 远端指纹 {rp}（**为什么不写提交号**：见 `selfcheck.sha_fingerprint`）",
                [] if ok else [f"两边指纹不同：本地 {lp} != 远端 {rp}"])


def check_14_pdf_pages() -> Item:
    caliber = ("在交付物 PDF 里数 `/Type /Page` 对象（`/Type /Pages` 是目录节点，"
               "用负向断言排除）。**页数是硬指标**：这一版要求 2 页，"
               "多出一页说明版式被撑破了")
    p, why = resume_pdf()
    if p is None:
        return Item("14", "PDF 是 2 页", caliber, f"扫 {ENV_RESUME_DIR} 下的 PDF",
                    SKIP, 0, f"{why} —— 本项**没有实测依据**，不是通过", [])
    data = p.read_bytes()
    n = len(re.findall(rb"/Type\s*/Page(?![s])", data))
    return Item("14", "PDF 是 2 页", caliber, f"扫 {ENV_RESUME_DIR} 下的 PDF",
                PASS if n == 2 else FAIL, 0 if n == 2 else 1,
                f"实测 {n} 页", [] if n == 2 else [f"实测 {n} 页，期望 2 页"])


def check_15_history_residue() -> Item:
    """历史残留：**实测它还在**，并且**证明没人动过历史**。

    ★ 这一项的判据反直觉，值得读两遍：它**要求命中数 > 0**。

    因为这一批修复**只改了工作区**，没有重写历史 —— 也就是说，那些字面量
    在修复之前的提交里**仍然查得到**，这是已知事实，也是 `BLOCKERS.md` 里
    那三条路的由来。所以：

    - 命中数 **> 0** → 历史没被动过（残留还在，符合预期）；
    - 命中数 **== 0** → 反而要报警：说明有人已经重写过了；
    - 那个修复前的提交**取不出来** → 要看本机到底有没有那段历史（见下）。

    换句话说，这一项守的是「**没有在没人同意的情况下重写历史**」，
    不是「历史很干净」。

    ## 两条腿（判据的代码在 `experiments/27_leak_fix_verify.py`，本项只取它的结论）

    - **甲：合成负控。** 一段**已知含词**的样本喂给同一把尺子，必须报红。
      它不读 git 历史，**任何机器上都跑得起来**，于是「尺子当下还灵」这件事
      到处都有实测依据（原来没有这层覆盖：唯一的负控要读本机 git 对象）。
    - **乙：旧历史那一版。** 本机存着那段历史时取回来扫，命中 > 0 才算过；
      **本机没有那段历史**（新仓的正常状态 —— 历史自一条初始提交起算）判 **SKIP**，
      理由写「没有实测依据」：既不是通过，**也不等于**「历史被重写过」。

    ★ 判据**只写一处**：两条腿都住在 27 里，这里不重抄一遍 —— 抄第二遍就多了
      一个会腐烂的地方，两边报的就不是同一件事了。
    """
    caliber = ("两条腿一起看：**甲**把一段已知含词的合成样本喂给同一把尺子"
               "（必须报红 —— 否则上面那几个 0 只说明尺子坏了）；"
               "**乙**用 `git show <仓外留档里的那个编号>:mcp_server/selfcheck.py` "
               "取回历史版本，用同一套词表扫描，**命中数 > 0 才算过**"
               "（那正是残留仍在、历史未被重写的证据）。"
               "本机没有那段旧历史时乙判「没有实测依据」。**本项不执行任何重写**，只读")
    cmd = ("喂一段已知含词的样本给同一把尺子 + "
           "git show <仓外留档里的那个编号>:mcp_server/selfcheck.py")
    if not sc.WORDLIST_AVAILABLE:
        # ★ 这一条比第 1/2/3/5 项更要紧：那几项词表读不到时是「命中 0 → 判 PASS」，
        #   而本项的判据是**反的**（命中 > 0 才算过），于是同一个空的命中列表
        #   会在这里**判 FAIL**，注释还写着「说明有人已经重写过历史」——
        #   把「没查」读成了一条**严重且不实**的指控。所以这里必须显式判 SKIP。
        return Item("15", "历史残留已实测、且未执行任何重写", caliber, cmd,
                    SKIP, 0,
                    f"{_wl_pending()} —— 扫出来必然是 0 命中，而本项的判据是"
                    f"「命中 > 0 才算过」，所以本项**没有实测依据**："
                    f"既不是通过，**也不等于**「历史被重写过」", [])
    v27 = _load_27()
    if v27 is None:
        return Item("15", "历史残留已实测、且未执行任何重写", caliber, cmd,
                    ERROR, 0,
                    "取不到 experiments/27_leak_fix_verify.py（两条腿的判据都在它那儿）",
                    [])
    synth = v27.synthetic_negative_control()
    hist = v27.old_history_rows()
    if synth["status"] != PASS:
        return Item("15", "历史残留已实测、且未执行任何重写", caliber, cmd,
                    FAIL, 1, f"合成负控没按期望报红：{synth['note']}",
                    ["尺子当场失灵 —— 那几条 0 命中不能当结论"])
    head = f"合成负控通过（{synth['note']}）"
    tail = "。本项**只读**，没有执行任何重写"
    if hist["status"] == "无实测依据":
        return Item("15", "历史残留已实测、且未执行任何重写", caliber, cmd,
                    SKIP, 0, f"{head}；{hist['note']}{tail}", [])
    n_hits = 0 if hist["status"] == PASS else len(hist["hits"])
    return Item("15", "历史残留已实测、且未执行任何重写", caliber, cmd,
                hist["status"], n_hits, f"{head}；{hist['note']}{tail}", hist["hits"])


def _load_27():
    """把 `experiments/27_leak_fix_verify.py` 当模块加载，复用它的判据。

    ★ 判据**只写一处**。抄第二遍就多了一个会腐烂的地方：改了 27 忘了改这里，
    两边报的就不是同一件事了。

    ★ 那个脚本里还记着「修复前那一版」是哪个提交 —— 但**编号本身住在仓库外面**
      （仓外留档，由 `paths.pre_fix_commit()` 读），因为它是一把钥匙：
      七位缩写在托管站点上照样能解析回旧对象，写进仓库等于把钥匙挂在墙上。
    """
    p = ROOT / "experiments" / "27_leak_fix_verify.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location("_v27", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001
        return None
    return mod


CHECKS = (check_1_names_in_repo, check_2_numbers_in_repo, check_3_terms_in_repo,
          check_4_public_raw, check_5_resume_stale, check_6_tool_count,
          check_7_tech_stack, check_8_facts_link, check_9_resume_cost,
          check_10_resume_overstated, check_11_gate, check_12_pytest,
          check_13_remote_sha, check_14_pdf_pages, check_15_history_residue)


def run(deps: dict | None = None, online: bool = False) -> list[Item]:
    """跑完 15 项。

    `deps` 是**已经算好的子进程结果**（退出码之类）。这样分工很清楚：
    - 被闸门调用时（`experiments/26_final_selfcheck.py`），闸门已经跑过 pytest，
      把结果传进来即可，**不重复跑第二遍**；
    - 独立跑时（`experiments/28_delivery_selfcheck.py`），由它自己去跑，
      包括那条会套娃的闸门。
    """
    deps = dict(deps or {})
    items: list[Item] = []
    for fn in CHECKS:
        try:
            if fn is check_4_public_raw:
                items.append(fn(online, deps))
            elif fn is check_13_remote_sha:
                items.append(fn(online, deps))
            elif fn in (check_11_gate, check_12_pytest):
                items.append(fn(deps))
            else:
                items.append(fn())
        except Exception as e:  # noqa: BLE001
            items.append(Item(fn.__name__[6], fn.__name__, "检查自身抛异常",
                              fn.__name__, ERROR, 0, f"{type(e).__name__}: {e}", []))
    return items


def totals(items: list[Item]) -> dict:
    return {s: sum(1 for i in items if i.status == s)
            for s in (PASS, FAIL, SKIP, ERROR)}


def section(items: list[Item]) -> str:
    """渲染成 `reports/final_selfcheck.md` 里的**新一节**。

    ★ 一并渲染的是**判定**，不是原始命中行 —— 命中行留在
    `reports/delivery_selfcheck.md` 里。理由是那一段是掩码过的长文本，
    抄进第二份报告就多一处要维护的引用，而它对读结论没有增量。
    """
    n = totals(items)
    L: list[str] = []
    A = L.append
    A("## 交付级自检（15 项：仓库 + 交付物 + 远端）")
    A("")
    A("> 上面 9 项问的是「**这个仓库**有没有越过自己划的红线」，只看仓库。")
    A("> 这 15 项问的是「**这一批交付物整体**是否成立」—— 它的检查对象跨出了仓库：")
    A("> 交付物在仓库外面、远端在网络上、还有一项要问 git 历史。")
    A("> 两层用的是**同一批**扫描函数（单一口径），所以扫仓库的那几项必然同结论。")
    A("")
    A("- **脚本**：`mcp_server/delivery_selfcheck.py`（本节的判定就是它渲染的）")
    A("- **`SKIP` 的含义**：这一项**没有实测依据** —— 交付物不在本机（没设 "
      f"`{ENV_RESUME_DIR}`）、或者本项要联网。**「没查」不许写成「0 命中」**")
    A("- **第 4、13 项要联网**，默认不跑（闸门必须能在断网机器上跑完）。"
      "带 `--online` 跑 `experiments/28_delivery_selfcheck.py` 会就地跑这两项，"
      "公网那一项的常驻实测在 `reports/leak_fix_verify.md`")
    A("- **第 11 项不重新跑闸门**（本模块被闸门调用，跑自己会套娃），"
      "判据是由四步退出码推得，推论链写在那 15 项报告的「判据」栏里")
    A("- **第 15 项的判据是「命中数 > 0 才算过」** —— 它守的是"
      "「没有在没人同意的情况下重写历史」，不是「历史很干净」。"
      "理由写在那 15 项报告里，值得读一遍")
    A("")
    A(f"**合计**：15 项 —— PASS {n[PASS]} / FAIL {n[FAIL]} / "
      f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    A("")
    A("| # | 检查项 | 判定 | 违规数 |")
    A("|---|---|---|---|")
    for i in items:
        A(f"| {i.n} | {i.title} | {i.status} | {i.n_hits} |")
    A("")
    A("命中行、每项的判据与命令、以及 `SKIP` 的具体理由，**将由** "
      "`experiments/28_delivery_selfcheck.py` 逐条写进 "
      "`reports/delivery_selfcheck.md` —— 那一份**生成后**才有内容"
      "（它跑得到联网那两项，闸门里这两项一定是 SKIP）。")
    A("")
    A("★ 本节与那一份的**第 11 项措辞不同，不是不一致**：闸门里这一份写的是"
      "「由四步退出码推得」（本模块被闸门调用，自己跑闸门会套娃），"
      "独立那一份写的是「本项自己跑出来的」。判据栏把这件事写明了，"
      "两边合起来看才是完整的。")
    A("")
    return "\n".join(L)


def render_report(items: list[Item], online: bool) -> str:
    """15 项的完整报告（`reports/delivery_selfcheck.md`）。"""
    n = totals(items)
    L: list[str] = []
    A = L.append
    A("# 交付级自检（15 项：仓库 + 交付物 + 远端）")
    A("")
    A("## 口径")
    A("")
    A(f"- **脚本**：`mcp_server/delivery_selfcheck.py`；"
      f"本次由 `experiments/28_delivery_selfcheck.py` 驱动"
      f"（{'联网' if online else '离线'}模式）")
    A("- **每行的「违规数」都是实测的**：大部分数的是扫描出来的命中行数；"
      "第 6、8、14 项数的是**违规条数**（判据见各节）；"
      "第 11、12 项看的是子进程退出码；第 15 项看的是历史版本里的命中条数 —— "
      "**那一项 > 0 才算过**，理由见该节")
    A("- **PASS 的含义**：违规数为 0，或退出码为 0。`ERROR` 表示这一项**自己**"
      "出错了（命令没跑起来、没取到）—— **不算通过**")
    A("- **`SKIP` 的含义**：这一项**没有实测依据**，既不是通过也不是失败。"
      "来源只有两种：交付物不在本机（没设 `DELIVERY_RESUME_DIR`），或者断网")
    A("- **命中行是掩码之后才写进来的**：逐字引用一处命中会让报告自己成为"
      "新的载体（`DECISIONS.md` D-30）")
    A("- **为什么交付物那一半要判 SKIP 而不是干脆不写**：不写的话，"
      "「交付物干净」这句话在报告上**没有任何痕迹**；判 SKIP 才是实话 —— "
      "本机确实没查。交出报告的人自己清楚差哪一项")
    A("")
    A(f"**合计**：15 项 —— PASS {n[PASS]} / FAIL {n[FAIL]} / "
      f"SKIP {n[SKIP]} / ERROR {n[ERROR]}")
    A("")
    A("## 明细")
    A("")
    A("| # | 检查项 | 判定 | 违规数 | 命令 |")
    A("|---|---|---|---|---|")
    for i in items:
        A(f"| {i.n} | {i.title} | {i.status} | {i.n_hits} | `{i.cmd}` |")
    A("")
    A("## 每项的判据与实测")
    A("")
    for i in items:
        A(f"### {i.n} {i.title} —— {i.status}（违规 {i.n_hits}）")
        A("")
        A(f"- **判据**：{i.caliber}")
        A(f"- **命令**：`{i.cmd}`")
        if i.note:
            A(f"- **备注**：{i.note}")
        if i.hits:
            A("- **违规明细**：")
            for h in i.hits:
                A(f"  - `{h}`")
        else:
            A("- **违规明细**：无")
        A("")
    A("## 这张表**不能**说明什么")
    A("")
    A("- 它不评价交付物写得好不好、能不能过筛 —— 那件事没有机械判据。"
      "它只查「说好的红线有没有被越过」。")
    A("- **静态项是词表驱动的**：词表里没有的说法扫不出来。"
      "「0 命中」的确切含义是「词表里的东西一处都没有」，不是「绝对干净」。")
    A("- 判 `SKIP` 的项在这份报告里**不代表任何结论**。"
      "把 SKIP 读成通过，是这张表最容易被误用的方式，所以它单列一栏。")
    A("")
    return "\n".join(L)


def write_report(text: str) -> list[str]:
    """落盘并做一次禁用词自检（和 `26` 用的是同一个写入口）。"""
    from mcp_server import eval_runner as runner
    return runner.write_report(REPORTS / "delivery_selfcheck.md", text)


def write_json(items: list[Item], online: bool) -> None:
    (REPORTS / "delivery_selfcheck.json").write_text(json.dumps({
        "online": online,
        "items": [i.as_dict() for i in items],
        "note": "口径见 reports/delivery_selfcheck.md 的「口径」小节。"
                "命中行是掩码之后才写进来的。",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
