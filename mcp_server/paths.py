"""路径与地址的单点收敛。

## 为什么要有这个文件

路径散落在各个脚本里，是「换台机器就跑不起来」的头号原因。早先的写法是把数据目录的
**绝对路径硬编码**在代码里，结果别人 clone 下来必然报「文件不存在」，而且报错信息指向的是
一个和他的机器毫无关系的路径，排查起来很费劲。

这里的约定是：

1. **默认值一律相对于仓库根**，跟着仓库走，不跟机器走；
2. 想换数据目录，只改**一个环境变量**（`MCP_TOOLKIT_DATA_ROOT`），不改代码；
3. 所有需要数据路径的地方都**调这里的函数**，不再自己拼路径。

## 本仓地址也收在这里

同样的毛病在**仓库地址**上又长了一遍：它原来写死在两个脚本里，换一次地址要改两处，
漏一处的表现是「检查照跑、只是查的是另一个仓库」——那种绿比红危险。所以地址也走这里：
环境变量 → 本机 `origin` → 仓外留档，解析不到就返回 `None`，**不猜**（见 `repo_slug()`）。

## 关于「源文件」和「构建产物」

本仓库区分两类文件，`describe()` 会把它们分开展示：

- **源文件**（随仓库分发，必须在）：示例库的建库脚本、知识库的原始文本与清单。
- **构建产物**（不入库，由 `experiments/01_build_db.py` 和 `02_build_kb.py` 生成）：
  `chinook.db` 和 `kb.db`。它们缺失**不是错误**，跑一次构建脚本就有了。

这个区分是有意的：二进制产物不入库，才能保证「重建出来的东西 == 别人重建出来的东西」这件事
是可验证的。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

# 仓库根目录：本文件在 <root>/mcp_server/paths.py，所以上跳一级。
ROOT = Path(__file__).resolve().parents[1]

# 覆盖数据根的环境变量。改名时记得同步 README 与 .env.example。
ENV_DATA_ROOT = "MCP_TOOLKIT_DATA_ROOT"


def data_root() -> Path:
    """数据根目录。环境变量 `MCP_TOOLKIT_DATA_ROOT` 优先，默认 `<仓库根>/data`。

    刻意写成函数而不是模块级常量：模块级常量在 import 时求值一次，
    之后再改环境变量就不生效了，测试里想指向临时目录会很难受。
    """
    override = os.environ.get(ENV_DATA_ROOT)
    return Path(override).expanduser() if override else ROOT / "data"


def chinook_sql() -> Path:
    """示例库的建库脚本（源文件，随仓库分发）。"""
    return data_root() / "chinook" / "Chinook_Sqlite.sql"


def chinook_db() -> Path:
    """示例库的 SQLite 文件（构建产物，不入库）。"""
    return data_root() / "chinook" / "chinook.db"


def kb_raw_dir() -> Path:
    """公版文本原始文件目录（源文件，随仓库分发）。"""
    return data_root() / "kb" / "raw"


def kb_manifest() -> Path:
    """公版文本清单：逐篇记录标题、作者、来源 URL、发布日期、校验和（源文件）。"""
    return data_root() / "kb" / "manifest.json"


def kb_db() -> Path:
    """知识库全文索引（构建产物，不入库）。"""
    return data_root() / "kb" / "kb.db"


def reports_dir() -> Path:
    """所有实验产物的落盘目录。"""
    return ROOT / "reports"


def cache_dir() -> Path:
    """运行期缓存（逐题结果等），不入库。"""
    return ROOT / "cache"


# ---------------------------------------------------------------- 仓库地址（同样单点收敛）
#
# 这一段和上面的数据路径是同一个毛病、同一副药：地址原来**写死在两个脚本里**
# （交付级自检的第 8 项、以及 27 那个核验脚本），换一次地址就要改两处，
# 漏一处的表现是「检查照跑、只是查的是另一个仓库」—— 那种绿比红危险得多。
#
# 三处来源的优先级、以及「两处说法不一致」时怎么办，见 `repo_slug()`。

# 覆盖本仓地址的环境变量。接受 `owner/name`，也接受各种完整写法（见 `_parse_slug`）。
ENV_REPO_URL = "MCP_TOOLKIT_REPO_URL"

# 仓外那份维护者留档的文件名。与仓外词表**同一个约定**：仓库同级的目录、不随仓分发。
MAINTAINER_FILENAME = "mcp-guarded-toolkit-maintainer.py"

# 读不到仓外留档时**唯一**能写进报告的那句话。刻意不含任何路径（理由同 `WORDLIST_PENDING`）。
MAINTAINER_PENDING = "仓外留档不在本机（见维护者本地）"

# 认得的托管站点。**只收这一家**：换一家要连着改它的三条地址形状
# （网页 / 原始文件 / commits 接口），而接口路径各家不一样 ——
# 「不猜远端地址」是本仓的教义（`DECISIONS.md` D-37 那一族）。
_WEB = "https://github.com"
_RAW = "https://raw.githubusercontent.com"
_API = "https://api.github.com"
_KNOWN_HOSTS = ("github.com",)

_SLUG_RE = re.compile(
    r"^https?://(?:[^@/]+@)?([^/:]+)(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$")


def maintainer_file() -> Path | None:
    """仓外那份维护者留档。不在（clone 的人不会有）返回 `None`。"""
    p = ROOT.resolve().parent / MAINTAINER_FILENAME
    return p if p.is_file() else None


def maintainer_constants() -> tuple[dict[str, str], str]:
    """读仓外留档里的字符串常量。返回 `(常量表, 读不到时的原因)`；成功时原因是空串。

    **只做 `ast.parse` + `ast.literal_eval`，不 `exec`** —— 与仓外词表同一套约定，
    理由也一样：那是数据，不需要执行能力。

    ★ 失败原因里**不回显文件内容、也不回显它的路径**：这个文件记的是本仓旧历史的
      对象号，把它抄进报告，等于把钥匙又挂回墙上（那正是这一轮要拆掉的东西）。
    """
    p = maintainer_file()
    if p is None:
        return {}, MAINTAINER_PENDING
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return {}, f"仓外留档读不动（{type(e).__name__}）"
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        try:
            val = ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(val, str):
            out[tgt.id] = val
    return out, ""


def _parse_slug(value: str) -> str | None:
    """把各种写法归一化成 `owner/name`。认不出来返回 `None`。

    吃这几种：`https://host/owner/name[.git]`、`git@host:owner/name.git`、
    `ssh://git@host/owner/name.git`、`host/owner/name`，以及光秃秃的 `owner/name`。

    ★ 返回 `None` 时**不回显原值**：那个值可能来自环境变量，也就是可能带着
      作者机器的路径，而调用处会把原因写进报告。
    """
    s = (value or "").strip()
    if not s:
        return None
    if s.startswith("git@"):                        # scp 形态：git@host:owner/name.git
        host, _, rest = s[4:].partition(":")
        s = f"https://{host}/{rest}"
    elif s.startswith("ssh://"):                    # ssh://git@host/owner/name.git
        s = "https://" + s[6:]
    elif "://" not in s:
        # 两种短写法：`host/owner/name`（两段斜杠）与 `owner/name`（一段）
        s = ("https://" + s) if s.count("/") >= 2 else (_WEB + "/" + s)
    # 查询串与锚点不改变「是哪个仓库」，先切掉 —— 不然 `?tab=readme` 会被
    # 当成仓库名的一部分，于是判成「认不出的地址」，而它本来是个好地址。
    s = s.split("#")[0].split("?")[0]
    m = _SLUG_RE.match(s)
    if not m:
        return None
    host, owner, name = m.group(1).lower(), m.group(2), m.group(3)
    if host not in _KNOWN_HOSTS or not owner or not name or name in (".", ".."):
        return None
    return f"{owner}/{name}"


def _slug_from_git() -> str:
    """问本机的 `origin`。取不到（不是克隆、没设 origin、git 起不来）返回空串。"""
    try:
        p = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if p.returncode != 0:
        return ""
    return _parse_slug(p.stdout) or ""


def repo_slug() -> tuple[str | None, str]:
    """本仓的 `owner/name`。返回 `(值或 None, 说明)`；成功时说明是空串。

    三处来源，优先级从高到低：

    1. 环境变量 `MCP_TOOLKIT_REPO_URL` —— **人为指定的覆盖，说了就以它为准**
       （测试就是靠它把地址钉成确定值，不然用例会跟着本机的 origin 走）；
    2. 本机的 `git remote get-url origin`；
    3. 仓外留档里的 `REPO_URL`。

    第 2、3 两处是**两处本机状态**：都解得出来且**不一致**时判「切换做了一半」，
    返回 `None`，而不是随便挑一个。挑错一个，下面几项就会去查**另一个仓库**
    并且报绿 —— 那比判「没查」危险得多。迁移那一步要改 origin、也要改留档，
    这个判断就是替那一步兜底的。

    **没有写死的兜底常量**：本仓教义是不猜远端地址，解析不到就交给调用方判
    「没有实测依据」（与仓外词表那套完全同构）。
    """
    env = _parse_slug(os.environ.get(ENV_REPO_URL, ""))
    if env:
        return env, ""
    git = _slug_from_git()
    consts, _why = maintainer_constants()
    pinned = _parse_slug(consts.get("REPO_URL", ""))
    if git and pinned and git != pinned:
        return None, ("本机两处地址说法不一致（`origin` 与仓外留档）——"
                      "切换做了一半，本项**没有实测依据**，不是通过")
    if git or pinned:
        return (git or pinned), ""
    return None, (f"解析不到本仓地址（环境变量 {ENV_REPO_URL} 没设、"
                  f"取不到 `origin`、本机也没有仓外留档）")


def repo_web_url() -> tuple[str | None, str]:
    """仓库的网页地址（不含 `.git`）—— 交付物里那条 facts 链接就该指向它。"""
    slug, why = repo_slug()
    return (f"{_WEB}/{slug}" if slug else None), why


def repo_raw_url(relpath: str, ref: str = "main") -> tuple[str | None, str]:
    """托管站点上「原始文件」的直链。交付级第 4 项拿它取远端那份文件。"""
    slug, why = repo_slug()
    if not slug:
        return None, why
    return f"{_RAW}/{slug}/{ref}/{relpath}", ""


def repo_api_commits_url(ref: str = "main") -> tuple[str | None, str]:
    """某个分支最新提交的接口地址。交付级第 13 项拿它读远端 sha。"""
    slug, why = repo_slug()
    if not slug:
        return None, why
    return f"{_API}/repos/{slug}/commits/{ref}", ""


def pre_fix_commit() -> tuple[str | None, str]:
    """「修复前那一版」的完整编号。**只能从仓外留档读**；读不到返回 `(None, 原因)`。

    为什么不写在仓库里：七位缩写在托管站点上照样能解析回旧对象，所以公开文档里
    写缩写与写全号**一样是钥匙**。编号搬去仓外，文档里只留标签（见 `pre_fix_label()`）。
    """
    consts, why = maintainer_constants()
    val = consts.get("PRE_FIX_COMMIT", "").strip()
    if not val:
        return None, why or "仓外留档里没有 `PRE_FIX_COMMIT`"
    return val, ""


def pre_fix_label() -> tuple[str | None, str]:
    """上面那个编号**在公开文档里的写法**（`旧编号·X`）。

    ★ 代码与产物**只许打印这个标签**，不许打印编号的任何前缀长度 —— 理由同上。
    """
    consts, why = maintainer_constants()
    val = consts.get("PRE_FIX_LABEL", "").strip()
    if not val:
        return None, why or "仓外留档里没有 `PRE_FIX_LABEL`"
    return val, ""


def rel(p: Path) -> str:
    """把路径显示成**仓库相对**形式，专供打印。

    ## 为什么打印和落盘要分开

    落盘要的是绝对路径（能直接打开）；**打印**要的是相对路径 —— 因为 stdout
    会被抄进报告里。脚本在自己输出里打一行 `C:\\Users\\某人\\...`，被
    `19_verify.py` 原样引用进 `reports/verify.md` 之后，报告本身就成了那个
    路径的载体（实测踩过：S7 和 S9 都是这么红起来的）。

    仓库外的路径（比如数据根被环境变量指到别处）无法相对化，原样返回 ——
    那种情况下它本来就是「配置出来的」，不是作者机器的目录结构。
    """
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# 源文件清单：不在就是环境没准备好，属于硬错误。
REQUIRED_SOURCES: tuple[str, ...] = (
    "chinook/Chinook_Sqlite.sql",
    "kb/manifest.json",
)

# 构建产物：不在只是「还没构建」，不算错误。
BUILD_PRODUCTS: tuple[str, ...] = (
    "chinook/chinook.db",
    "kb/kb.db",
)


def describe() -> dict:
    """当前解析出的路径 + 各类文件在不在。**只读文件系统，不打开任何数据文件。**"""
    root = data_root()
    src = {rel: (root / rel).is_file() for rel in REQUIRED_SOURCES}
    n_raw = len(list(kb_raw_dir().glob("*.txt"))) if kb_raw_dir().is_dir() else 0
    src["kb/raw/*.txt（至少 1 篇）"] = n_raw > 0
    return {
        "data_root": str(root),
        "source": f"环境变量 {ENV_DATA_ROOT}" if os.environ.get(ENV_DATA_ROOT) else "默认值（仓库内的 data/）",
        "exists": root.is_dir(),
        "sources": src,
        "n_raw_texts": n_raw,
        "build_products": {rel: (root / rel).is_file() for rel in BUILD_PRODUCTS},
        "ROOT": str(ROOT),
    }


def main() -> int:
    """自检入口。退出码：0 = 源文件齐全；1 = 缺源文件；2 = 数据根不存在。"""
    d = describe()
    print("=" * 68)
    print("数据路径自检")
    print("=" * 68)
    print(f"数据根   : {d['data_root']}")
    print(f"来源     : {d['source']}")
    print(f"仓库根   : {d['ROOT']}")
    print("-" * 68)

    if not d["exists"]:
        print(f"❌ 数据根目录不存在：{d['data_root']}")
        print()
        print("   如果你把数据放在别处，设置环境变量指向它：")
        print(f'     PowerShell:  $env:{ENV_DATA_ROOT} = "D:\\my-data"')
        print(f"     bash:        export {ENV_DATA_ROOT}=/data/my-data")
        return 2

    missing = [k for k, v in d["sources"].items() if not v]
    print("源文件（随仓库分发）：")
    for k, v in d["sources"].items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("构建产物（不入库，跑构建脚本生成）：")
    for k, v in d["build_products"].items():
        print(f"  {'✅' if v else '·'} {k}")
    print("-" * 68)

    if missing:
        print(f"❌ 缺 {len(missing)} 个源文件：{', '.join(missing)}")
        print("   见 NOTICE.md 的数据来源说明，或跑 experiments/00_fetch_corpus.py 重新获取。")
        return 1

    print("✅ 源文件齐全。构建产物缺失不影响，跑 01_build_db.py / 02_build_kb.py 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
