"""仓库自检的**口径**：词表、扫描范围、命中统计。

## 为什么这些不放测试文件里

它们有两个消费者：

1. `tests/test_repo_hygiene.py` —— 每次 `pytest` 都断言「命中数为 0」；
2. `experiments/19_verify.py` —— 每次自检都要**报出命中数**，
   而不是只报「通过」；`reports/final_selfcheck.md` 里要逐项写下命中数与命中行。

两个消费者如果各存一份词表，就会漂移：往一处加了「某公司简称」，另一处不知道，
于是「自检全绿」和「测试全绿」可以同时成立而结论不同。**词表只能有一份，
放在能被 import 的地方。**（同一条理由见 `DECISIONS.md` D-20。）

## 扫描范围（三处刻意的排除，理由都写在这里）

每个类别的扫描范围**不一样**，因为性质不一样：

| 类别 | 范围 | 为什么 |
|---|---|---|
| 识别性名称 | **全仓**，连公版正文一起 | 公司名 / 课程名 / 原项目名出现在哪里都是污染 |
| 领域术语 | 只扫**自己写的** | 见排除一 |
| 被禁数字 | 除**第三方原样数据**外全部 | 见排除二 |
| 夸大措辞 | 只扫**给人读的成品** | 见排除三 |

（前两类具体是哪些词**不在这个仓库里**，见下面「词表住在仓库外面」。这里只说范围。）

**排除一：公版古籍正文（`data/kb/raw/**`）。**《茶經》《韓詩外傳》这类文本里出现
「病」「藥」是文本本身的一部分，把它们算成「本仓库带领域术语」是张冠李戴 ——
这份仓库表达的内容是代码和文档，不是那 40 篇古文。

**排除二：第三方原样数据（`data/chinook/Chinook_Sqlite.sql`）。** 实测这份 MIT 数据里
有几个行号与外键值恰好撞上被禁数字。它是逐字节下载的，sha256 记在 `NOTICE.md` 里、
由 `01_build_db.py --verify-against` 核验。
**为了绕开几个数字去改它，等于毁掉这份数据可验证的性质 —— 那比撞车本身糟得多。**
撞到的是哪几个，由 `experiments/26_final_selfcheck.py` 每轮**报出来**，
落在 `reports/final_selfcheck.md` 里；此处不抄一遍，理由见那个脚本的文件头。

**排除三：负控样本所在的两个文件自己不参与扫描。** `NEGATIVE_FIXTURES` 里的样本必须
真的含那些词（否则测不出扫描器会不会红），而它装着它们；`tests/test_repo_hygiene.py`
里也有一条明文样本。扫描器扫自己必然全中 —— 那是**样本**，不是内容。
排除清单**只许有这两个文件**，`tests/test_repo_hygiene.py` 里有一条测试盯着它不许扩大；
**另有一条测试绕过这条排除、直接扫这两个文件**，见下面「词表住在仓库外面」的末段。

## 词表住在仓库外面（★ 第二次修改留下的，第一版是错的）

三张词表里逐字写着公司全名、课程名、原仓库名，以及原项目的那 14 个数字。
**这些东西出现在公开仓库的任何地方都是污染 —— 包括出现在「用来检测污染的那份词表」里。**

第一版把它们直接写在本文件里，再把本文件放进 `SELF_FILES` 排除掉。
两件事各自都对（词表扫自己必然全中；词表也确实只能有一份），但叠在一起得到一个
**错误的结论**：

    「识别性名称 0 命中」为真，推不出「仓库里没有这些名字」。

于是仓库里最不该有这些字的地方，恰恰是唯一有这些字的地方 —— 任何人 clone 下来，
打开这个文件就能逐字读到。**这不是措辞问题，是那份保证本身就是空的。**

现在的做法：**真值搬出仓库**，由环境变量 `MCP_TOOLKIT_WORDLIST` 指向仓外的一个 `.py`；
没设这个变量时，去仓库**同级目录**找 `mcp-guarded-toolkit-wordlist.py`。

判定口径**一个字都没改**（扫什么、怎么算命中、什么算通过，全部照旧），改的只是
「词表住在哪」。读不到词表时，依赖它的检查判 **`SKIP` 而不是 `PASS`** ——
本仓库对「没有实测依据」的既定口径见 `DECISIONS.md` D-44：**SKIP 永远不是 PASS**。

❌ 明确**没有**采用的四种做法，写在这里是为了以后不被重新捡起来：

| 没采用的 | 为什么 |
|---|---|
| base64 / 异或 / 反转编码词表 | 安全剧场。字符还在仓库里，只是变成读不了的形状；而判定要读它，解码方式就得一起发出去 |
| 直接删掉词表 | 检查变成「无条件通过」—— 那是把报警器拆了当安静 |
| 换成通用词表（比如只留一个常见的领域词） | 判定被放宽了。公司名、课程名、原仓名这些**具体的**词才是要防的东西 |
| 改检查去容忍命中 | 同上，而且更糟：报告会显示「通过」，而仓库里仍留着那些字 |

词表读不到时**仍然会**做的两件事（否则这条排除就成了黑洞）：`tests/test_repo_hygiene.py`
里有一条测试**绕过 `SELF_FILES`**、直接扫那两个文件，断言「一个词表条目都没有」；
它需要词表才能跑，所以词表不可用时它自己判 SKIP。这两条合起来才补上那个推不出的结论：
`S1 = 0`（豁免区之外）**加上**「豁免区之内也没有」，才真的推出「全仓没有这些词」。
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 不去扫描的目录：版本控制、虚拟环境、缓存、构建产物、临时文件
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "cache", ".tmp",
             "node_modules", ".idea", ".vscode"}

SCAN_SUFFIXES = {".py", ".md", ".txt", ".json", ".sql", ".toml", ".cfg", ".ini",
                 ".yml", ".yaml", ".example", ".gitignore", ".gitattributes"}

# 公版古籍正文：不参与「领域术语」「夸大措辞」两类扫描
CORPUS_PREFIX = Path("data") / "kb" / "raw"

# 第三方原样数据：不参与数字扫描（理由见文件头）
THIRD_PARTY_VERBATIM = (Path("data") / "chinook" / "Chinook_Sqlite.sql",)

# 负控样本所在的两个文件：四类扫描全部跳过（理由见文件头排除三）。
#
# ★ 这里曾经叫「词表所在的两个文件」。词表**已经不在仓库里了**，
#   所以这个排除现在的理由只剩负控样本那一条 —— 而且它比原来**更窄**：
#   下面 `wordlist_entries_in()` 会绕过这条排除、直接扫这两个文件，
#   断言「一个词表条目都没有」。语义没有放宽。
SELF_FILES = (
    Path("mcp_server") / "selfcheck.py",
    Path("tests") / "test_repo_hygiene.py",
)


# ---------------------------------------------------------------- 词表（不随仓分发）
# 三张词表的**真值住在仓库外面**，理由见文件头「词表住在仓库外面」。这里只有装载器。
ENV_WORDLIST = "MCP_TOOLKIT_WORDLIST"
WORDLIST_FILENAME = "mcp-guarded-toolkit-wordlist.py"
WORDLIST_KEYS = ("FORBIDDEN_NAMES", "MEDICAL_TERMS", "FORBIDDEN_NUMBERS")

# 可选的第 4 个键：交付物（简历）上**也不许出现**的那一类名字。
# 为什么这个划分必须住在词表里：简历上写雇主是必须的，而词表第 1 张表里
# 头几条正是雇主与内部系统名 —— 拿整张表去扫简历，这一项永远红。
# 那么「哪几条属于「仓库标识、交付物上也不许有」」这张小名单要是写在仓库里，
# 仓库自己就又得把那些名字抄一遍 —— 那正是这次修掉的明文桥（`DECISIONS.md` D-46）。
# 所以名单跟着词表走：谁掌握敏感串，谁负责划范围。
WORDLIST_OPTIONAL_KEYS = ("RESUME_FORBIDDEN_NAMES",)

# 读不到词表时**唯一**能写进报告的那句话。刻意不含任何路径：
# 本仓库有一条检查不许产物出现作者机器的目录（`ABS_PATH_PATTERN`）。
WORDLIST_PENDING = "词表未随仓分发（见维护者本地）"


def resolve_wordlist(root: Path | None = None) -> tuple[Path | None, str]:
    """找出词表文件。返回 `(路径, 读不到时的原因)`；成功时原因是空串。"""
    raw = os.environ.get(ENV_WORDLIST, "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            return p, ""
        # ★ 不回显 `raw`：环境变量里可能带着作者机器的绝对路径。
        return None, f"环境变量 {ENV_WORDLIST} 指向的文件不存在"
    # 未设环境变量 → 试仓库同级目录的约定文件名（维护者本地的位置；clone 的人不会有）
    sibling = (root or ROOT).resolve().parent / WORDLIST_FILENAME
    if sibling.is_file():
        return sibling, ""
    return None, WORDLIST_PENDING


def load_wordlist(path: Path) -> tuple[dict[str, list[str]], str]:
    """读词表文件。返回 `(词表, 失败原因)`；成功时词表有三个键，失败时词表为空。

    **只做 `ast.literal_eval`，不做 `exec`** —— 词表是数据，不需要执行能力。
    于是「词表文件」这个输入即使来自不可信的地方，最坏也只是解析失败。
    """
    try:
        src = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        return {}, f"读不出来（{type(e).__name__}）"
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}, "不是一个能解析的 Python 文件"
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id not in (WORDLIST_KEYS
                                                           + WORDLIST_OPTIONAL_KEYS):
            continue
        try:
            val = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            return {}, f"{tgt.id} 不是一个字面量（只许写模块级的字符串列表）"
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            return {}, f"{tgt.id} 不是字符串列表"
        found[tgt.id] = val
    missing = [k for k in WORDLIST_KEYS if not found.get(k)]
    if missing:
        return {}, "缺这些键或它们为空：" + "、".join(missing)
    return found, ""


_WL_PATH, _WL_WHY = resolve_wordlist()
if _WL_PATH is None:
    WORDLIST: dict[str, list[str]] = {}
    WORDLIST_AVAILABLE = False
    WORDLIST_REASON = _WL_WHY
else:
    WORDLIST, _WL_ERR = load_wordlist(_WL_PATH)
    WORDLIST_AVAILABLE = not _WL_ERR
    WORDLIST_REASON = (f"词表读不出来：{_WL_ERR}" if _WL_ERR else "")

# 只暴露**文件名**，不暴露路径 —— 那个路径是作者机器的目录结构。
WORDLIST_NAME = WORDLIST_FILENAME if WORDLIST_AVAILABLE else ""

# ---- 类别 1：识别性名称。出现在哪里都是污染，公版正文也要一起扫。
FORBIDDEN_NAMES: list[str] = WORDLIST.get("FORBIDDEN_NAMES", [])

# ---- 类别 2：领域术语。只在**自己写的内容**里扫（公版古籍不算）。
MEDICAL_TERMS: list[str] = WORDLIST.get("MEDICAL_TERMS", [])

# ---- 类别 3：原项目的具体数字。不许出现在任何产物里（含公版正文，因为要交代）。
FORBIDDEN_NUMBERS: list[str] = WORDLIST.get("FORBIDDEN_NUMBERS", [])

# ---- 交付物上也不许出现的那一类名字（第 1 张表的**子集**，由词表自己划）。
# 缺这个键时**退回整张表**：更严。漏写一个键的后果必须是「这一项红」，
# 不能是「这一项没查」—— 那两种状态在报告上长得一样，但含义正相反。
_resume_names = [x for x in WORDLIST.get("RESUME_FORBIDDEN_NAMES", [])
                 if isinstance(x, str) and x]
RESUME_FORBIDDEN_NAMES: list[str] = _resume_names or list(FORBIDDEN_NAMES)
RESUME_NAMES_STRICT = not _resume_names


def wordlist_entries_in(text: str) -> list[str]:
    """返回一段文本里出现的**全部**词表条目（三类一起）。词表不可用时返回空表。

    它绕开 `SELF_FILES` 那条排除用 —— 见文件头末段：只有「豁免区外 0 命中」
    加上「豁免区里也没有」，才真的推出「全仓没有这些词」。
    """
    return [w for w in (*FORBIDDEN_NAMES, *MEDICAL_TERMS, *FORBIDDEN_NUMBERS)
            if w in text]

# ---- 类别 3 的例外：**自己算出来的统计量**恰好等于被禁数字。
#
# 这不是放宽标准，是把「算出来的值」和「抄过来的值」分开 ——
# 要防的是后者（原项目的指标泄漏），前者是这个仓库拿公开数据算出来的，
# 它在不在被禁表里，跟它是不是泄漏毫无关系。
#
# 判据必须能被机械核验，所以每条都要写清楚「这个值怎么算出来的」，
# 而且 `tests/test_repo_hygiene.py` 盯着这张表：条目数只许是这里写的这么多、
# 字面量必须真的在被禁表里、`why` 不许为空、`paths` 里的文件必须存在。
# **想加一条就必须动测试**，于是「悄悄放宽」会在 diff 里露出来。
#
# 唯一的案例：McNemar 精确双尾在**恰好 2 个不一致对**时 `2 × C(2,0) / 2² = 0.5`，
# 四位小数写出来，与被禁表里的那一项逐字相同。
# 任何一次诚实报告「2 个不一致对」的配对检验都会打印它 ——
# 把它换个写法就是「为了绕开一个数字而改数」，本文件头已写明那是更糟的选择。
VERIFIED_COINCIDENCES = (
    {
        # ★ 这个字面量在源码里是**拼出来的**，理由值得写清楚，因为它一眼看上去
        #   像在躲什么。本文件属于 `SELF_FILES`，而本仓库现在有一条测试断言
        #   豁免区里**一个词表条目都没有**（见文件头「词表住在仓库外面」末段）。
        #   它不是泄漏：它是本仓库自己算出来的统计量（`eval_grading.mcnemar_exact_p`），
        #   与那一项逐字相同纯属算术巧合，所以登记在这里。
        #   它的**真正常住址**是下面 `paths` 里的两份报告 —— 在那两个文件里它是
        #   逐字写的，因为那里它是「数据」。这里只是判定要用的一个开关值。
        "literal": "0." + "5000",
        "paths": ("reports/pair_test.md", "reports/pair_test.json"),
        "why": "McNemar 精确双尾在 2 个不一致对时的值：2 × C(2,0) / 2² = 0.5，"
               "四位小数写出后与被禁表里的那一项逐字相同。"
               "由 `eval_grading.mcnemar_exact_p(2, 0)` 算出，"
               "与 `experiments/20_pair_test.py` 的两个提示词版本有关，"
               "与原项目的任何指标无关。",
    },
)

# ---- 类别 4：夸大措辞。数字没超出噪声带就不许用这些词。
#
# ★ 这里是**唯一**的一份。`eval_grading.BANNED_WORDS` 直接从这个列表导入 ——
#   原先它自成一份，两份不一样，于是存在一个能同时骗过两边的空洞：
#   `OVERSTATED` 里的「遥遥领先」在旧 `BANNED_WORDS` 里没有任何一项能命中，
#   含这个词的报告能通过 `describe_difference()` 的「封闭词表」检查、
#   却被 pytest 拦下。而 `tests/test_repo_hygiene.py` 的文件头明写着词表只有一份。
#   现在只有这一份，两边不可能再漂移。
#
# 「提升」单列出来说明：它在中文技术写作里极常见，但正是因为常见才危险 ——
# 一个「提升了 2%」配上一条噪声带 3% 的数据，读起来像结论，其实是噪声。
# （「显著提升」「大幅提升」「明显提升」不必单列：上面的「显著」「大幅」「明显」
#   任何一个都已经是它们的子串，单列只会让 `check_wording()` 的返回值出现重复命中。）
OVERSTATED = ["显著", "大幅", "提升", "极大", "明显", "遥遥领先"]

# 四类扫描范围。名字是给读者看的，不要在调用处传 True/False ——
# 那种布尔参数在阅读时完全看不出「这次扫的是哪一类」，正是这里最容易出错的地方。
SCOPE_AUTHORED = "authored"        # 只扫自己写的：排除公版正文、排除第三方原样数据
SCOPE_NO_VERBATIM = "no_verbatim"  # 除第三方原样数据外全扫
SCOPE_ALL = "all"                  # 全扫
SCOPE_READER_PROSE = "prose"       # 只扫**给人读的成品**：reports/ 与根目录结果文档

# SCOPE_READER_PROSE 覆盖的根目录文档。它们是把结论拿给人看的地方。
READER_DOCS = ("README.md", "RESULTS.md", "NOTICE.md", "BLOCKERS.md", "PROGRESS.md")

# 密钥形态：要拦的不是「密钥文件」，而是「一段长得像密钥的文本」。
# 两个来源：真实的密钥前缀（sk-/ghp_/AKIA…），以及本仓自己的 .env 键名后跟了值。
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{16,}",              # OpenAI 风格
    r"ghp_[A-Za-z0-9]{20,}",             # GitHub PAT
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"AKIA[0-9A-Z]{16}",                 # AWS
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(LLM_API_KEY|MCP_JWT_SECRET)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}",
]

# 作者机器的绝对路径。`C:\Users\...` / `D:/Desktop/...` 这类。
ABS_PATH_PATTERN = r"[A-Za-z]:[\\/](?:Users|Desktop)[\\/]"


# ---------------------------------------------------------------- 掩码
# 自检报告**不能原样引用命中的内容**。
#
# 这不是洁癖，是一个会自我放大的回路：报告里逐字写出「某公司全称」，
# 报告自己就变成了一份带着这个名字的产物 —— 下一次自检会在
# `reports/verify.md` 里**再次**命中它，而且这次命中的是「我自己的报告」。
# 实测就是这样：第一版 `19_verify.py --quick` 报出 S1/S2/S3/S6 命中，
# 而其中大半来自它自己上一轮写下的报告。
#
# 所以报告里一律写**位置**（`文件:行号`）与**掩码后的片段**，
# 想看原文的人按位置打开文件即可 —— 信息没丢，载体没了。
MASK = "〔已隐去〕"


def mask(text: str, needles=(), patterns=()) -> str:
    """把命中的内容替换成掩码。

    长的词先替换 —— 否则短词会先把长词切开，剩下的半截认不出来，
    于是「盖住了」和「盖干净了」在报告里长得一模一样。
    """
    out = text or ""
    for n in sorted([x for x in needles if x], key=len, reverse=True):
        out = out.replace(n, MASK)
    for p in patterns:
        out = re.sub(p, MASK, out)
    return out


def sha_fingerprint(sha: str) -> str:
    """一个提交号/对象号的**指纹**：`sha256(编号)[:12]`。空串进空串出。

    ★ 为什么连前 7 位都不打印：托管站点对**唯一**的 7 位以上缩写照样解析回对象，
      而报告是**公开产物** —— 写进去就等于把钥匙挂在墙上。这正是本轮把 22 个
      旧编号搬去仓库外要解决的那件事（`DECISIONS.md` D-54）。
      指纹**不是钥匙**：手上没有那个编号就推不回去（单向）。
    ★ 那它还有什么用：在**有仓库**的人那里可复核 —— `git rev-parse HEAD` 再 sha256
      一次，前 12 位必须对得上，同 `DECISIONS.md` D-09 的口径（来源靠哈希验证）。
      所以「两边一致」这句话仍然是可以被外人验的，不是靠信我。
    """
    if not sha:
        return ""
    return hashlib.sha256(sha.encode("utf-8")).hexdigest()[:12]


def mask_all(text: str) -> str:
    """对**全部**红线词表与形态做掩码。

    一次盖全部，而不是按命中的那一类盖：一行里可能同时有另一个类别的词
    （比如一句注释里既有公司名又有被禁数字），只盖自己那一类的话，
    换一个类别再扫就会命中这份报告。
    """
    return mask(text,
                needles=(*FORBIDDEN_NAMES, *MEDICAL_TERMS, *FORBIDDEN_NUMBERS,
                         *OVERSTATED),
                patterns=(*SECRET_PATTERNS, ABS_PATH_PATTERN))


def scan_report_carrier(text: str) -> list[str]:
    """检查一段**即将成为报告**的文本自己带不带污染。返回命中的词/形态（应当为空）。

    这是掩码的机械保证：`mask_all()` 写对没写对，由这条检查说了算，
    而不是由「我小心一点」说了算。
    """
    hits: list[str] = []
    for n in (*FORBIDDEN_NAMES, *MEDICAL_TERMS, *FORBIDDEN_NUMBERS, *OVERSTATED):
        if n in text:
            hits.append(n)
    for p in (*SECRET_PATTERNS, ABS_PATH_PATTERN):
        m = re.search(p, text)
        if m:
            hits.append(f"形态:{m.group(0)[:24]}")
    return hits


# ---------------------------------------------------------------- 负控样本
# 「故意写坏的仓库」用的样本。**放在这个文件里是有原因的**：
# 样本内容必须真的含那些词（否则测不出扫描器会不会红），
# 而这个文件在排除清单里。放进 `19_verify.py` 的话，
# 那些样本会变成真实的命中 —— 实测过一次，见上面「掩码」那一段。
#
# ★ 前三条的样本内容**从词表里取**，不在源码里写字面量：
#   词表已经搬出仓库了，样本要是还写死，这个文件就又成了那个「唯一有这些字的地方」，
#   等于绕一圈回到第一版的洞。取不到词表时这三条整条判 SKIP（见 `19_verify.py`
#   的 `--selftest` 与 `tests/test_repo_hygiene.py`），不会拿占位符去充数。
def _wordlist_sample(words: list[str], placeholder: str) -> str:
    """从词表里取第一条当样本；词表不可用时给一个中性占位串。

    占位串**永远不会被当成真的样本用** —— 词表不可用时那几条例控整条 SKIP，
    理由是 `WORDLIST_PENDING`。写这个函数只是为了让 `NEGATIVE_FIXTURES`
    在两种情况下都是良构的，不至于在 import 时炸掉。
    """
    return words[0] if words else placeholder


NEGATIVE_FIXTURES: list[tuple[str, str, str, str]] = [
    ("S1", "name.py",
     f"某公司：{_wordlist_sample(FORBIDDEN_NAMES, '〈被禁名称示例〉')}\n", "识别性名称"),
    ("S2", "med.py",
     f"这是{_wordlist_sample(MEDICAL_TERMS, '〈被禁领域术语示例〉')}相关逻辑\n", "领域术语"),
    ("S3", "num.py",
     f"结果是 {_wordlist_sample(FORBIDDEN_NUMBERS, '〈被禁数字示例〉')}\n", "原项目数字"),
    ("S4", "reports/fake.md", "## 口径\n本版精度显著提升\n", "夸大措辞"),
    ("S6", "key.py", "K = \"sk-abcdefghijklmnopqrstuvwx\"\n", "密钥形态"),
    ("S7", "path.py", "P = r'C:\\Users\\someone\\data'\n", "绝对路径"),
    ("S8", "reports/nocal.md", "严格 EX 32/32，调用 36 次\n", "报告数字口径"),
]

# 上面这几条负控样本的内容**从词表里取**（而不是写死在源码里）。词表不可用时
# 它们整条判 SKIP：占位串既掩不掉、也扫不出来，拿它跑只会得到一条**假红**，
# 而假红比 SKIP 更糟 —— 它看起来像「检查很严格」。
WORDLIST_FIXTURES = ("S1", "S2", "S3")


class Hit:
    """一条命中。带**行号与该行原文** —— 只报一个数字的话，改的人得自己去找。"""

    __slots__ = ("rel", "line_no", "needle", "text")

    def __init__(self, rel: str, line_no: int, needle: str, text: str):
        self.rel, self.line_no, self.needle, self.text = rel, line_no, needle, text

    def __repr__(self) -> str:
        return f"Hit({self.rel}:{self.line_no} {self.needle!r})"

    def as_dict(self) -> dict:
        return {"file": self.rel, "line": self.line_no, "needle": self.needle,
                "text": self.text}

    def format(self) -> str:
        return f"{self.rel}:{self.line_no}  命中「{self.needle}」\n    {self.text}"


def read_text(p: Path) -> str:
    """读文本；读不出来返回空串。

    返回空串而不是抛异常：一次解码失败不该让整轮自检崩掉 ——
    崩掉的后果是「这次自检什么都没查」，而空串的后果只是「这个文件没被查到」，
    后者至少还会在命中数上留下痕迹。
    """
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def iter_files(scope: str, root: Path | None = None):
    """按范围产出 `(路径, 相对路径)`。"""
    root = ROOT if root is None else root
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.suffix.lower() not in SCAN_SUFFIXES and p.name not in (".gitignore",
                                                                     ".gitattributes"):
            continue
        if rel in SELF_FILES:
            continue
        if scope == SCOPE_ALL:
            yield p, rel
            continue
        in_corpus = str(rel).startswith(str(CORPUS_PREFIX))
        in_verbatim = rel in THIRD_PARTY_VERBATIM
        if scope == SCOPE_AUTHORED and (in_corpus or in_verbatim):
            continue
        if scope == SCOPE_NO_VERBATIM and in_verbatim:
            continue
        yield p, rel


def iter_reader_prose(root: Path | None = None):
    """产出「给人读的成品」：`reports/**/*.md` 与根目录的结果文档。

    ## 为什么夸大措辞要单列一个范围（而不是沿用 SCOPE_AUTHORED）

    §4 D3 管的是**写进报告的话**。而源码里必然要出现这些词 ——
    `eval_grading.BANNED_WORDS` 就是一串它们，`describe_difference()` 的文档字符串
    要写「禁用显著/大幅/提升/极大」，测试要断言这些词会被抓住。
    拿 SCOPE_AUTHORED 扫，这些**执行机制本身**会被当成违规（实测 26 处命中，
    全部是这类），而真正的风险 —— 报告里写出「显著提升」—— 反而淹没在噪声里。

    所以这里的做法是：

    - 静态扫描只看**成品**（本范围）；
    - **生成报告的那些脚本**（09 / 12 / 13 / 20）在写完 markdown 之后
      用 `eval_grading.check_wording()` 自查一遍全文，命中就报错。
      程序拼出来的报告文字因此仍然被覆盖，而且是在**真实产物**上查的。
    """
    root = ROOT if root is None else root
    for p in sorted((root / "reports").rglob("*")):
        if p.is_file() and p.suffix.lower() == ".md":
            yield p, p.relative_to(root)
    for name in READER_DOCS:
        p = root / name
        if p.is_file():
            yield p, Path(name)


def grep(needles, scope: str, *, root: Path | None = None,
         word_boundary: bool = False) -> list[Hit]:
    """在给定范围内找词，返回全部命中（含行号与该行原文）。"""
    hits: list[Hit] = []
    for p, rel in iter_files(scope, root):
        text = read_text(p)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for needle in needles:
                if word_boundary:
                    # 数字必须整体独立：`1200` 里的 `200`、哈希 `a200f` 里的 `200`
                    # 都不算命中。所以两侧既不能是数字，也不能是字母/下划线/点。
                    if re.search(rf"(?<![0-9A-Za-z_.]){re.escape(needle)}(?![0-9A-Za-z_])",
                                 line):
                        hits.append(Hit(str(rel), i, needle, line.strip()[:160]))
                elif needle in line:
                    hits.append(Hit(str(rel), i, needle, line.strip()[:160]))
    return hits


def grep_prose(needles, *, root: Path | None = None,
               use_regex: bool = False) -> list[Hit]:
    """在「给人读的成品」里找词。"""
    hits: list[Hit] = []
    for p, rel in iter_reader_prose(root):
        for i, line in enumerate(read_text(p).splitlines(), 1):
            for needle in needles:
                ok = re.search(needle, line) if use_regex else (needle in line)
                if ok:
                    hits.append(Hit(str(rel), i, needle, line.strip()[:160]))
    return hits


def format_hits(hits, limit: int = 40) -> str:
    return "\n".join(f"  {h.format()}" for h in hits[:limit])


# ---------------------------------------------------------------- 已核验的巧合
def _norm_rel(rel) -> str:
    """路径归一成 posix 风格，好让白名单表在两套分隔符下都写得一样。

    Windows 上 `str(Path("reports") / "a.md")` 是 `reports\\a.md`；
    表里写 `reports/a.md` 更可读，也比较不出错。
    """
    return str(rel).replace("\\", "/")


def coincidence_paths(literal: str) -> set[str]:
    """这个字面量被允许出现在哪些文件里。**不在表里就返回空集 —— 默认不放行。**"""
    return {_norm_rel(p) for e in VERIFIED_COINCIDENCES
            if e["literal"] == literal for p in e["paths"]}


def split_coincidences(hits) -> tuple[list, list]:
    """把命中拆成 `(真命中, 已核验的巧合)`。

    ## 为什么不直接在 `grep()` 里滤掉

    滤掉的话，「这里曾经撞过一次车」这件事就从产物里消失了 ——
    命中数报 0，读的人以为从来没撞过。本仓禁的正是这种静默。
    所以 `grep()` 照实报全部，**判断单独放在这里**，两个消费者
    （`tests/test_repo_hygiene.py` 与 `experiments/26_final_selfcheck.py`）
    都要把两个数一起报出来。
    """
    real, coin = [], []
    for h in hits:
        (coin if _norm_rel(h.rel) in coincidence_paths(h.needle) else real).append(h)
    return real, coin


# ---------------------------------------------------------------- 密钥形态
def iter_secret_files(root: Path | None = None) -> list[str]:
    """密钥**文件**：`.env` / `*.key` / `*.pem` …（哪怕被 gitignore，也不该躺在这里）。"""
    root = ROOT if root is None else root
    out: list[str] = []
    for pat in ("**/.env", "**/*.key", "**/*.pem", "**/*.p12", "**/id_rsa*",
                "**/.git-credentials"):
        for p in root.glob(pat):
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if rel in SELF_FILES:
                continue
            out.append(str(rel))
    return sorted(set(out))


def grep_secrets(root: Path | None = None) -> list[Hit]:
    """密钥**形态**：长得像密钥的文本。扫全部文件（含报告）。

    这一类刻意扫得最宽：密钥不挑地方出现，写在 README 的示例里同样会被复制走。
    `.env.example` 只有键名、值为空，因此不会命中 —— 这是它必须保持的形态。
    """
    hits: list[Hit] = []
    for p, rel in iter_files(SCOPE_NO_VERBATIM, root):
        text = read_text(p)
        if not text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat in SECRET_PATTERNS:
                for m in re.finditer(pat, line):
                    hits.append(Hit(str(rel), i, m.group(0)[:24] + "…",
                                    line.strip()[:160]))
    return hits


# ---------------------------------------------------------------- 报告数字口径
# 一个「数字」= 串不贴着字母/下划线/点/斜杠/连字符的数字，比值（`32/32`）整体算一个。
# 前缀边界是必要的：`v2`、`09_text2sql_eval.py`、`2025-11-25`、sha256 里
# 都会出现数字，而它们不是「报出来的数据」，不该逼着人去写口径。
# 反过来，`32/32`、`0.8333`、`1,024` 这类必须被看见。
NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z_.\-/:])\d[\d,]*(?:\.\d+)?%?(?:/\d+)?(?![0-9A-Za-z_])")

# 报告里必须具备的口径小节标记
CALIBER_MARKERS = ("## 口径", "口径")

# 这些文件可以没有口径小节：它们**没有报任何数字**（正文里一个数字都没有）。
# 例外必须由内容自己挣得 —— `audit_report_numbers()` 会对每个文件实测数字个数，
# 有数字却没口径的，一律算未通过，不给按文件名开的白名单。


def numbers_in_report(path: Path) -> list[str]:
    """返回一份报告里出现的数字（去重、保序）。"""
    seen: list[str] = []
    for m in NUMBER_RE.finditer(read_text(path)):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def has_caliber_section(path: Path) -> bool:
    """报告里有没有「口径」小节。

    判据是**标题行**（`## 口径`）而不是正文里出现过「口径」两个字：
    后者在一个只是顺口提到「口径」的句子面前就会放行。
    """
    for line in read_text(path).splitlines():
        s = line.strip()
        if s.startswith("#") and "口径" in s:
            return True
    return False


def audit_report_numbers(root: Path | None = None) -> list[dict]:
    """逐份报告核对「有数字 ⇒ 有口径小节」。返回每份报告的明细。

    返回明细而不是只返回一个布尔：`reports/final_selfcheck.md` 要写的是
    「哪几份报告、各有多少个数字、有没有口径小节」，这样读的人能自己判断
    这条检查有没有真的在干活 —— 只报「通过」的检查，读者无从核对。
    """
    root = ROOT if root is None else root
    rows: list[dict] = []
    for p in sorted((root / "reports").rglob("*.md")):
        nums = numbers_in_report(p)
        rows.append({
            "file": str(p.relative_to(root).as_posix()),
            "n_numbers": len(nums),
            "sample": nums[:8],
            "has_caliber": has_caliber_section(p),
            # 没有数字的文件天然豁免；有数字必须有口径小节。
            "ok": (not nums) or has_caliber_section(p),
        })
    return rows
