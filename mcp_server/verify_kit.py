"""自检的**判定**部分：结果对象、退出码台账、不依赖外部世界的检查。

## 为什么这些不能写在 `experiments/19_verify.py` 里

`experiments/` 下的文件名以数字开头，**以数字开头的模块没法被 import** ——
写在 `19_verify.py` 里的判定逻辑只能靠「跑一遍 19、看它绿不绿」来测，
而那恰好是这套东西里最需要被独立测的一块：它本身就是「判定别人」的那段代码。

所以分成两半（同 `DECISIONS.md` D-20）：

- **`mcp_server/verify_kit.py`（本模块，纯）**：怎么判、期望退出码是多少、
  产物互斥怎么算。不跑子进程、不读环境变量。
- **`experiments/19_verify.py`（有副作用）**：真的去起子进程、收退出码、
  落盘报告，然后调本模块判。

## 「未完成」的判定条件

本仓库有一批脚本需要模型凭据，本机没有。它们以退出码 5 结束并写下
`reports/*_NOT_RUN.md`。这**不算失败**，但也不是喊一句「未完成」就算数。
三件事必须同时成立：

1. 退出码是脚本文件头声明的那一个（5）；
2. 留下了 `*_NOT_RUN.md`，写明卡在哪一步；
3. `BLOCKERS.md` 里有一条对应的最小动作。

缺任何一条就是 FAIL。**没有落盘、没有解法的「未完成」，和「忘了跑」
在报告里长得一模一样。**
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mcp_server import paths
from mcp_server import selfcheck as sc

PASS, FAIL, SKIP, ERROR = "PASS", "FAIL", "SKIP", "ERROR"
NO_LLM_EXIT = 5
NO_DB_EXIT = 3
BLOCKERS = "BLOCKERS.md"

# 「示例库还没构建」这一条的合法口径。
#
# `chinook.db` 是**构建产物**、按设计不入库：源 SQL 随仓库分发，
# `python experiments/01_build_db.py` 一步就能建出来（不需要联网、不需要凭据）。
# 于是新 clone 一台机器上，凡是查这个库的入口脚本都会以 3 结束
# （脚本文件头里写的 `E_NO_DB = 3`，`mcp_server/guardrails.py` 里同一个值），
# 并在输出里写明「[未完成] 示例库还没构建」。
#
# 这**不是失败**：脚本自己说的是「未完成」，台账照抄同一个口径，
# 用的是本表早就有的 `alt`（同 `12_cost_report` 的退出码 5、以及 25 早先那一条）。
# ★ `expect` 仍然是 0，一个字没松：维护者本机建了库，这几条依旧按 0 判，
#   真退 3 就说明库真没建，报告里会以 SKIP 摆出来（SKIP 计入「未完成」，
#   不计入 PASS —— 「没跑」不许被读成「干净」）。
NO_DB_ALT = {NO_DB_EXIT: "示例库还没构建（源 SQL 随仓库分发，"
                         "`python experiments/01_build_db.py` 一步即可建，不需要联网）"
                         " —— 未完成，非失败"}


class Result:
    """一条检查的结果。

    `caliber` 不是装饰：没有它，这张表的每一行都只是「我说它通过了」。
    有它，读的人才能判断这条检查**有没有资格**得出它给出的结论。
    """

    __slots__ = ("cid", "title", "caliber", "status", "detail", "hits")

    def __init__(self, cid: str, title: str, caliber: str, status: str,
                 detail: str = "", hits: list | None = None):
        self.cid, self.title, self.caliber = cid, title, caliber
        self.status, self.detail = status, detail
        self.hits = hits or []

    def as_dict(self) -> dict:
        return {"id": self.cid, "title": self.title, "caliber": self.caliber,
                "status": self.status, "detail": self.detail,
                "n_hits": len(self.hits), "hits": self.hits}

    def line(self) -> str:
        return f"| {self.cid} | {self.title} | {self.status} | {self.detail or '—'} |"


# 退出码台账。每一条声明：跑什么、期望退出码、为什么跑、
# 以及（可选）「未完成」的合法替代口径。
EXIT_LEDGER: list[dict] = [
    {"cmd": ["experiments/10_auth_test.py"], "expect": 0,
     "why": "鉴权矩阵：应拒 / 应放行 / 入口一致性三组用例都要全过"},
    {"cmd": ["experiments/11_guardrail_test.py"], "expect": 0, "alt": NO_DB_ALT,
     "why": "护栏负例矩阵：每条负例都要给出期望 / 实测 / 退出码"},
    {"cmd": ["experiments/08_mcp_smoke.py"], "expect": 0,
     "why": "真实 stdio 握手：协议用例 + 超时用例"},
    {"cmd": ["experiments/09_text2sql_eval.py", "--check-questions"], "expect": 0,
     "alt": NO_DB_ALT,
     "why": "参考解核验：不需要 LLM，任何时候都必须过"},
    {"cmd": ["experiments/13_text2sql_colloquial.py", "--check-questions"],
     "expect": 0, "alt": NO_DB_ALT,
     "why": "口语题集核验：同上，另加「参考 SQL 与正式题逐字相同」"},
    {"cmd": ["experiments/09_text2sql_eval.py", "--fake", "oracle"], "expect": 0,
     "alt": NO_DB_ALT,
     "why": "假模型自检：满分模型必须拿满分，否则是评分有 bug"},
    {"cmd": ["experiments/09_text2sql_eval.py", "--fake", "naive"], "expect": 0,
     "alt": NO_DB_ALT,
     "why": "假模型自检：故意答错必须拿不到分，否则是评分太松"},
    {"cmd": ["experiments/13_text2sql_colloquial.py", "--fake", "oracle"],
     "expect": 0, "alt": NO_DB_ALT, "why": "口语链路的假模型自检"},
    {"cmd": ["experiments/21_noise_band.py", "--fake", "stable"], "expect": 0,
     "alt": NO_DB_ALT,
     "why": "噪声带尺子正控：两遍完全一样时必须量出 0"},
    {"cmd": ["experiments/21_noise_band.py", "--fake", "jitter"], "expect": 0,
     "alt": NO_DB_ALT,
     "why": "噪声带尺子负控：第二遍故意抖 3 题，必须正好量出 3"},
    {"cmd": ["experiments/20_pair_test.py", "--selftest"], "expect": 0,
     "why": "配对统计与措辞自检：合成数据，7 组"},
    {"cmd": ["experiments/29_concurrency_bench.py"], "expect": 0,
     "why": "并发实测：同一批 24 条真调用的三种跑法，实测峰值 A 1 / B 1 / C 6。"
            "**B 那一行是负结果，刻意留着**（见 reports/concurrency.md 第二节）"},
    {"cmd": ["experiments/30_http_smoke.py"], "expect": 0,
     "why": "HTTP 入口冒烟：真起子进程、真端口、真 TCP，20 项检查 + 产物字节可复现"},
    {"cmd": ["experiments/09_text2sql_eval.py"], "expect": 0, "needs_llm": True,
     "not_run": "reports/text2sql_NOT_RUN.md",
     "result": "reports/text2sql_results.v3.json",
     "alt": NO_DB_ALT,
     "why": "Text2SQL 36 题 × 3 版（需要模型凭据）"},
    {"cmd": ["experiments/13_text2sql_colloquial.py"], "expect": 0, "needs_llm": True,
     "not_run": "reports/colloquial_NOT_RUN.md",
     "result": "reports/colloquial_vs_formal.json",
     "alt": NO_DB_ALT,
     "why": "口语 vs 正式对照（需要模型凭据）"},
    {"cmd": ["experiments/21_noise_band.py"], "expect": 0, "needs_llm": True,
     "not_run": "reports/noise_band_NOT_RUN.md",
     "result": "reports/noise_band.json",
     "alt": NO_DB_ALT,
     "why": "实测噪声带（需要模型凭据）"},
    {"cmd": ["experiments/31_ledger_under_concurrency.py"], "expect": 0, "needs_llm": True,
     "not_run": "reports/ledger_under_concurrency_NOT_RUN.md",
     "result": "reports/ledger_under_concurrency.json",
     "why": "并发下的账本对账：并发一批 + 串行一批，账本新增行数必须等于实际调用次数"
            "（需要模型凭据）。**不挂 `alt`**：它自己造临时库，不依赖 `data/`，"
            "所以「没有数据」不是它能拿 5 的理由 —— 唯一的 5 是「没有凭据」"},
    {"cmd": ["experiments/12_cost_report.py"], "expect": 0,
     "not_run": "reports/cost_report.md",
     "alt": {5: "账本为空（尚无任何真实模型调用）"},
     "why": "成本报告：只读账本，不需要模型凭据；账本为空时以 5 结束并写明「未完成」"},
    {"cmd": ["experiments/25_deliverable_check.py"], "expect": 0,
     "why": "交付物核验：文档里写的和仓库里的对不对得上。"
            "阶段 6 之前这条挂过 alt（退出码 5 = README 尚未创建），"
            "README 落地后已改回 expect 0 —— **这条不许再挂 alt**："
            "它现在没有任何「合理地不通过」的理由"},
]

# ★ 注意 `tools/check.py` 与 `experiments/26_final_selfcheck.py` **不在**这张台账里：
# 它们自己会去跑 19_verify，放进来就成了「19 跑它、它又跑 19」，转起来了。
# 两个都由 `tests/test_verify_kit.py` 里那条 `--list` 的子进程用例守着
# （闸门列出来的链条里必须看得见 26，否则它等于没有守门人）。


def cid_for(entry: dict) -> str:
    """给台账里的一条生成一个稳定的短 id（出现在报告表里，要能一眼对上）。"""
    return "X" + Path(entry["cmd"][0]).name[:2] + "_" + "_".join(entry["cmd"][1:3])[:10]


def artifacts_hash(root: Path | None = None) -> tuple[str, int]:
    """`reports/` 整目录的聚合哈希。返回 `(哈希, 文件数)`。

    ## 它证明的是什么

    「同一份代码跑两遍，产物**逐字节相同**」—— 也就是「跑完闸门 `git status`
    还是干净的」。这句话一旦不成立，`git status` 就不再是「有没有变化」的信号，
    而这是本仓最能自证的一句话（见 `DECISIONS.md` D-38）。

    ## 为什么要带文件名

    只哈希内容的话，把 `a.md` 改名成 `b.md` 不会改变哈希 —— 那样的「相同」
    证明不了目录相同。文件名和内容一起进哈希。

    ## 为什么也返回文件数

    一个空目录和一个满目录的哈希看起来没区别；文件数让「哈希相同」这件事
    有分母。0 个文件时的相同，说明不了任何事。
    """
    r = sc.ROOT if root is None else root
    base = r / "reports"
    h = hashlib.sha256()
    files = sorted(p for p in base.rglob("*") if p.is_file())
    for p in files:
        h.update(p.relative_to(base).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest(), len(files)


def tail_for_report(tail: str, limit: int = 400) -> str:
    """把子进程的输出尾部处理成**可以抄进报告**的形式。

    ## 为什么引用之前要掩码

    这段尾巴是**引用**，不是本仓的声明 —— 而子进程的 stdout 是任意文本。
    脚本把绝对路径打在自己的输出里（`print(f"报告：{REPORTS / 'x.md'}")` 这种），
    被原样抄进 `reports/verify.md` 之后，报告本身就成了那个路径的载体，
    下一轮 S7 会在自己的报告里命中它。实测就是这条路径红起来的。

    掩码之后**要说一句**，否则读报告的人不知道这段引用是不完整的 ——
    一份读起来完整、实际被剪过的证据，比明说剪过更糟。

    ## 耗时读数也要去掉（实测踩过）

    子进程的输出里带着时钟读数：`pytest` 结尾是「N passed in …s」，
    评测脚本每道题后面跟着 `calls=1 0.02s`。这些数字**每次都不同**，
    抄进报告之后，报告就变成「每次跑都不一样」的产物 ——
    于是每跑一次闸门 `git status` 都显示 `reports/verify.md` 已修改，
    而「跑完还是干净的」本来是这个仓库最能自证的一句话。

    只去**小数**形式（`22.28s` / `0.02s` / `7.0 秒`）：整数秒是有意义的声明
    （`read_timeout_seconds=20s`、`HARD_TIMEOUT=30s`），不能一起抹掉。

    ## `limit`：截断必须发生在掩码**之后**

    调用方原先各自写成 `tail_for_report(x)[:300]`，看着等价，其实不然：
    先截断会把一条绝对路径从中间切断，剩下半截 `C:\\Users` 两个都认不出来 ——
    **掩码漏掉、检查也认不出来**，于是它就安安静静躺在报告里（实测复现过）。
    所以截断收进来，顺序固定为「先掩码、再截断」，并且**给那句说明留出位置** ——
    说明被一起截掉的话，一份读起来完整、实际被剪过的引用就出现了。
    """
    if not tail:
        return ""
    masked = sc.mask_all(tail)
    for pat in VOLATILE_RES:
        masked = pat.sub("〔耗时已隐去〕", masked)
    note = "（引用前已隐去敏感形态与耗时读数）" if masked != tail else ""
    keep = max(0, limit - len(note))
    if len(masked) > keep:
        masked = masked[:keep]
    return masked + note


def _sha12(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest()[:12] if data is not None else "-"


class ProductsPreserved:
    """跑一段**会重写产物**的东西之前，先把这些产物按字节存下来，事后原样放回。

    ## 为什么需要它（实测踩过）

    `experiments/27_leak_fix_verify.py` 为了证明「读不到词表时判 SKIP，
    不是判 PASS」，会带着一个**指不到文件的** `MCP_TOOLKIT_WORDLIST`
    去跑闸门第 4 步。第 4 步里 `26_final_selfcheck.py` 又会跑
    `19_verify.py` 和 `pytest` —— 于是它**顺手把 `reports/verify.md` 与
    `reports/final_selfcheck.md` 重写成「本机没有词表」的样子**。
    实测：跑完 27，`git status` 里就躺着这两份**降级**的报告，
    而它们看起来和真的一模一样（PASS 变 SKIP、数量都对得上）。

    这不只是「工作区脏了」：那两份是**闸门的**产物，一次核验实验
    不该改写它们；「跑完还是干净的」是本仓最能自证的一句话（`DECISIONS.md` D-38）。

    ## 用法与留痕

    存的是**字节**，不是文本 —— 换行风格（本仓的 md 在 Windows 上会被
    git 换成 CRLF）也在还原范围内。跑完把每份的前后 sha256 记进 `evidence`，
    报告里照抄，于是「还原过」这件事本身是可核的，不是一句声明。

    本来**不存在**的文件，跑完若被创建出来就删掉 —— 否则它会留下来冒充产物。
    """

    def __init__(self, paths) -> None:
        self._paths = [Path(p) for p in paths]
        self._before: dict[Path, bytes | None] = {}
        self.evidence: list[dict] = []

    def __enter__(self) -> "ProductsPreserved":
        for p in self._paths:
            self._before[p] = p.read_bytes() if p.is_file() else None
        return self

    def __exit__(self, *_exc) -> bool:
        for p in self._paths:
            was = self._before[p]
            now = p.read_bytes() if p.is_file() else None
            err = ""
            try:
                if was is None:
                    if now is not None:   # 本来没有 → 别留下一个冒充产物的新文件
                        p.unlink()
                        now = None
                elif now != was:
                    p.write_bytes(was)
                    now = p.read_bytes()
            except OSError as e:
                # ★ 还原失败**不许把它变成一句安静的「已还原」**，也不许在这里
                #   抛出去盖掉调用方原本的异常（比如子进程超时）。记下来，
                #   由报告那一侧写明「否」。
                err = type(e).__name__
            self.evidence.append({
                "name": p.name,
                "before": _sha12(was), "after": _sha12(now),
                "restored": was == now,
                "error": err,
            })
        return False


def format_hit_lines(hits, limit: int = 20) -> list[str]:
    """把一批 `selfcheck.Hit` 渲染成「位置 + 掩码后的片段」若干行。

    ## 为什么放在这里

    它原来长在 `experiments/26_final_selfcheck.py` 的 `_from_hits()` 里，
    用的是 `h.file` / `h.line` —— 而 `Hit` 的属性叫 `rel` / `line_no`。
    这个错**只在命中数不为 0 时才发作**，因为
    `[f(...) for h in hits[:limit]]` 在 `hits` 为空时根本不求值：
    于是「报告不该出现的数字」这一项一路全绿，**恰恰在它该报出命中的那一刻
    崩成 ERROR**（实测：S3 命中 3 处，26 的第 3 项 ERROR）。

    单一口径：以数字开头的模块没法被 import，写在那里就只能靠跑整条自检来验。
    挪到这里之后，`tests/test_verify_kit.py` 可以直接喂一个**非空**的命中列表
    给它 —— 那正是原来漏掉的那种输入。
    """
    return [f"{h.rel}:{h.line_no} {sc.mask_all(h.text or h.needle)}"
            for h in hits[:limit]]


def finalize_report_text(text: str, path) -> tuple[str, list[str]]:
    """把一段**即将落盘**的报告文本变成不带自毒的版本：返回 `(文本, 扫出来的污染词)`。

    ## 这一个函数为什么必须存在

    「扫自己 → 掩码」这一段，`19_verify.py` 与 `26_final_selfcheck.py` 里
    **各写了一遍**，而 `25_deliverable_check.py` **一遍都没写** ——
    偏偏它自己就渲染 `文件:行号`，而行号本身可能就是被禁数字。实测：

    ```
    scan_report_carrier("某报告.md:<某个被禁数字> …") -> ['<那个数字>']
    mask_all(同上)                                       -> "某报告.md:〔已隐去〕 …"
    ```

    ★ **那几行长什么样子，这里故意不写出来。** 上一稿是把它们逐字写上的
    （`<某个被禁数字>` 就是被替换掉的原文），结果这段注释自己成了命中：
    实测 `pytest` 报「原项目数字 14 处真命中」，其中 8 处就来自本函数与
    `25_deliverable_check.py`、`DECISIONS.md` 里描述同一个机制的三段文字。
    **描述这个回路的那段文字，自己踩进了这个回路** —— 见 `DECISIONS.md` D-45。

    于是「命中恰好落在第 N 行（N 是词表里某个数）」时，
    引用它的那份报告**自己就成了新的载体**。行号会随文件改动而挪位，
    所以这种红是**偶尔出现、重跑就好**的那种 —— 最难查的一类。
    同一段逻辑抄三遍、漏一处，就是「有缺口但看着齐全」。

    ## 为什么不搬进 `eval_runner.write_report()`（想过，否决了）

    1. 那里对**所有**产物一视同仁。`reports/pair_test.md` 里有一个登记在
       `selfcheck.VERIFIED_COINCIDENCES` 的合法字面量（一对提示词版本的精确 p 值），
       一律掩码会**把一个真结果擦掉**；
    2. 产物里有 JSON。`"rows": <数字>` 掩成 `"rows": 〔已隐去〕` 就是**非法 JSON**，
       `json.loads` 直接抛 —— 那是把数据改坏，比带一点自毒更糟。

    所以掩码**只施加在给人读的 markdown 上**，并且**按路径尊重登记豁免**：
    `selfcheck.coincidence_paths()` 里没登记这份文件，就照掩不误。

    ## 关于 `形态:` 那一类

    `scan_report_carrier()` 也会返回密钥/绝对路径的**形态**命中（不是词表里的字面量，
    替换不掉），那种情况下退回整体掩码 —— 宁可多掩一点，也不能漏一条路径。
    """
    rel = str(paths.rel(Path(path))).replace("\\", "/")
    carrier = [n for n in sc.scan_report_carrier(text)
               if rel not in sc.coincidence_paths(n)]
    if not carrier:
        return text, []
    if any(c.startswith("形态:") for c in carrier):
        return sc.mask_all(text), carrier
    fixed = text
    for n in carrier:
        fixed = fixed.replace(n, sc.mask_all(n))
    return fixed, carrier


# 带小数的耗时读数。`\d+\.\d+` 而不是 `\d+`：见 `tail_for_report` 的说明。
# 单一口径：`tests/test_repo_hygiene.py` 里那条「产物里不许有时钟读数」用的是同一组，
# 所以这里是公开名字而不是 `_` 开头。
VOLATILE_RES = (
    re.compile(r"\bin \d+\.\d+s\b"),          # pytest：`513 passed in 22.28s`
    re.compile(r"\d+\.\d+\s*s(?![a-zA-Z])"),  # `calls=1 0.02s`
    re.compile(r"\d+\.\d+\s*秒"),             # 本仓自己的打印：`7.0 秒后失败`
    # pytest 的**第二种**耗时形态。第 4 条是补齐，不是新增口径：
    # `_pytest/terminal.py::format_session_duration` 在 `seconds < 60` 时返回
    # `f"{seconds:.2f}s"`，**在 `seconds >= 60` 时返回 `f"{seconds:.2f}s ({dt})"`** ——
    # 也就是同一个读数多带一个 `(H:MM:SS)`。前三条只吃掉前半截，括号原样留下，
    # 于是「同一次运行，套件跑进 60 秒以内就干净、超过 60 秒就脏」——
    # 一台慢一点的机器（或一次冷启动）就能让 `reports/verify.md` 每次显示「已修改」，
    # 而三条正则一条都不报。实测：`604 passed in 79.45s (0:01:19)` 掩码后得到
    # `604 passed 〔耗时已隐去〕 (0:01:19)`，括号里的读数进了产物。
    re.compile(r"\(\d+:\d{2}:\d{2}\)"),        # pytest：`604 passed in 79.45s (0:01:19)`
)


def judge_not_rerun(entry: dict, root: Path | None = None) -> Result:
    """**只读模式**下，需要凭据的条目怎么判：看盘上的产物，不执行命令。

    ## 为什么需要这个模式（实测出来的两个副作用）

    闸门原来是**真的把这四条跑一遍**的。于是「跑一次 `tools/check.py`」会做两件
    不该由闸门做的事：

    1. **花钱。** `09` 一次约 108 次调用、`21` 一次 72 次；（数字见各自脚本的打印）
    2. **覆盖产物。** 它们会重写 `reports/` 里的实测报告。而 `RESULTS.md` 里
       逐条引用了那些数字 —— 闸门一跑，文档里的数字和盘上的产物就对不上了。
       「每个数字都能指出脚本名 + 口径」是本仓的立论，这条不能由一次闸门破坏。

    所以默认改成只读：**验证「产物在不在、自不自洽」，不重跑**。
    要端到端重跑就显式加 `--rerun-llm`（那是花钱的动作，应当由人主动发起）。

    三种情形分别判 SKIP / SKIP / FAIL，**没有 PASS** ——
    这一轮确实没验证「脚本现在还能退 0」，报 PASS 就是把没做的事说成做了。
    """
    r = sc.ROOT if root is None else root
    cid, title = cid_for(entry), " ".join(entry["cmd"])
    caliber = (entry["why"] + "；只读模式：不重跑，按盘上的产物判。"
               "端到端重跑要显式加 `--rerun-llm`")
    result, nr = entry.get("result"), entry.get("not_run")

    if result and (r / result).is_file():
        return Result(cid, title, caliber, SKIP,
                      f"本轮未重跑；盘上已有实测产物 {result}")
    if nr and (r / nr).is_file():
        return Result(cid, title, caliber, SKIP,
                      f"本轮未重跑；盘上是未完成声明 {nr}（合法未完成）")
    return Result(cid, title, caliber, FAIL,
                  f"本轮未重跑，而盘上既没有实测产物"
                  f"（{result or '未登记'}）也没有未完成声明（{nr or '未登记'}）—— "
                  f"这一项没有任何东西可以佐证")


def judge_exit(entry: dict, got: int, root: Path | None = None,
               tail: str = "") -> Result:
    """把「期望码 vs 实际码」判成一个结果。**纯函数**（不跑命令），所以能被负控直接喂数据。"""
    r = sc.ROOT if root is None else root
    cid = cid_for(entry)
    title = " ".join(entry["cmd"])
    caliber = entry["why"] + f"；期望退出码 {entry['expect']}"
    tail = tail_for_report(tail)

    if got == entry["expect"]:
        return Result(cid, title, caliber, PASS, f"退出码 {got}")

    # 「未完成」的合法路径：退出码是脚本声明的那个，且留下了可追查的痕迹。
    if entry.get("needs_llm") and got == NO_LLM_EXIT:
        marker = r / entry["not_run"]
        blockers = r / BLOCKERS
        has_marker = marker.is_file()
        in_blockers = blockers.is_file() and marker.name in sc.read_text(blockers)
        if has_marker and in_blockers:
            return Result(cid, title, caliber, SKIP,
                          f"退出码 {got}：模型凭据未配置（未完成，非失败）。"
                          f"已留 {marker.name}，{BLOCKERS} 里有对应动作")
        # 还有一条**既不是未完成、也不是失败**的路径，原先没有：
        # 「盘上已经有实测产物，只是这一轮没能重跑」。
        #
        # 缺了这条会撞出一个假矛盾：无凭据时脚本写上 `*_NOT_RUN.md`，
        # 而盘上已有的结果还在 —— `artifact_checks()` 把「结果 + 未完成声明并存」
        # 判成自相矛盾（它防的是「跑成功后忘了删」，分不清这两种情况）。
        # 实测就是这样：把 `.env` 挪出仓库后，A1/A2/A3 三条一起红。
        #
        # 判 SKIP 而不是 PASS：这一轮**确实没验证**「脚本现在还能退 0」，
        # 报 PASS 就是把没做的事说成做了。
        if entry.get("result") and (r / entry["result"]).is_file():
            return Result(cid, title, caliber, SKIP,
                          f"退出码 {got}：本轮无凭据、未重跑；盘上已有实测产物 "
                          f"{entry['result']}（那是之前配好凭据时跑出来的）")
        missing = []
        if not has_marker:
            missing.append(f"缺 {entry['not_run']}")
        if not in_blockers:
            missing.append(f"{BLOCKERS} 里没提到 {marker.name}")
        return Result(cid, title, caliber, FAIL,
                      f"退出码 {got} 却没有把「未完成」交代清楚：{'、'.join(missing)}"
                      + (f" | 输出尾部：{tail}" if tail else ""))

    if got in entry.get("alt", {}):
        return Result(cid, title, caliber, SKIP, f"退出码 {got}：{entry['alt'][got]}")

    if got < 0:
        return Result(cid, title, caliber, ERROR,
                      f"命令没跑起来：{tail}")
    return Result(cid, title, caliber, FAIL,
                  f"退出码 {got}，期望 {entry['expect']}"
                  + (f" | 输出尾部：{tail}" if tail else ""))


def artifact_checks(root: Path | None = None) -> list[Result]:
    """产物之间不能自相矛盾。

    具体防的是这一种事故：脚本**跑成功了**，但上一次留下的 `*_NOT_RUN.md`
    忘了删 —— 于是仓库里同时挂着「跑过了」和「没跑成」两块牌子，
    而读报告的人只会看到其中一块。
    """
    r = sc.ROOT if root is None else root
    out: list[Result] = []
    pairs = [
        ("reports/noise_band.json", "reports/noise_band_NOT_RUN.md",
         "噪声带：实测结果 vs 未完成声明"),
        ("reports/text2sql_results.v3.json", "reports/text2sql_NOT_RUN.md",
         "Text2SQL：逐题记录 vs 未完成声明"),
        ("reports/colloquial_vs_formal.json", "reports/colloquial_NOT_RUN.md",
         "口语化对比：结果 vs 未完成声明"),
    ]
    for i, (result, not_run, title) in enumerate(pairs, 1):
        has_result = (r / result).is_file()
        has_not_run = (r / not_run).is_file()
        caliber = "两者互斥：要么有实测结果、要么有未完成声明，不许同时存在"
        if has_result and has_not_run:
            out.append(Result(f"A{i}", title, caliber, FAIL,
                              f"{result} 与 {not_run} 同时存在 —— "
                              f"跑成功后应当删掉未完成声明"))
        else:
            which = ("有实测结果" if has_result else
                     ("有未完成声明（未完成，非失败）" if has_not_run else "两者都没有"))
            out.append(Result(f"A{i}", title, caliber, PASS, which))
    return out


# 根目录必需文档。`later` 里的属于收尾阶段，缺失时判 SKIP 而不是 PASS ——
# 一张写着 PASS 的表会让人以为它们已经检查过了。
REQUIRED_DOCS = ("NOTICE.md", "LICENSE", "DECISIONS.md", "PROGRESS.md",
                 "BLOCKERS.md", ".env.example", "requirements.txt")
LATER_DOCS = ("README.md", "RESULTS.md")


def doc_checks(root: Path | None = None) -> list[Result]:
    r = sc.ROOT if root is None else root
    present = [n for n in REQUIRED_DOCS if (r / n).is_file()]
    missing = [n for n in REQUIRED_DOCS if n not in present]
    todo = [n for n in LATER_DOCS if not (r / n).is_file()]
    return [
        Result("D1", "根目录必需文档", "文件存在性检查；缺失项列在 detail 里",
               PASS if not missing else FAIL,
               f"{len(present)}/{len(REQUIRED_DOCS)} 存在"
               + (f"，缺：{'、'.join(missing)}" if missing else "")),
        Result("D2", "阶段 6 文档（README / RESULTS）",
               "这两份属于收尾阶段；未创建时判 SKIP 而不是 PASS —— "
               "一张写着 PASS 的表会让人以为它们已经检查过了",
               PASS if not todo else SKIP,
               "已创建" if not todo else f"尚未创建：{'、'.join(todo)}（阶段 6）"),
    ]
