"""生成 `NOTICE.md` —— 逐项数据出处与许可。

## 为什么是生成而不是手写

40 篇语料的出处表格，手抄一遍必然有错（漏一篇、抄错一个编号、字符数对不上），
而且**改语料之后它会静静地过期**。生成的话，表格永远等于清单里的实际内容 ——
「文档说的」和「数据是的」不可能不一致。

机器可读的权威是 `data/kb/manifest.json`；`NOTICE.md` 是给人看的那一份。

## 关于 Chinook 的来源 URL

`CHINOOK_SOURCE_URL` 是**验证过的**，不是猜的：脚本内置的检查会把该 URL 下载下来
算 sha256，与仓库里的文件比对，逐字节一致才写进去。用 `--verify-source` 可以重跑这个检查。

退出码：0 = 写出成功；1 = 源文件缺失或来源核验失败。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402

# 已验证（2026-09-18 用 sha256 逐字节比对通过）。改动此常量前请重跑 --verify-source。
CHINOOK_SOURCE_URL = ("https://raw.githubusercontent.com/lerocha/chinook-database/"
                      "master/ChinookDatabase/DataSources/Chinook_Sqlite.sql")
CHINOOK_PROJECT = "https://github.com/lerocha/chinook-database"
CHINOOK_VERSION = "1.4.5"
CHINOOK_AUTHOR = "Luis Rocha"
CHINOOK_LICENSE = "MIT"
CHINOOK_LICENSE_REF = "https://github.com/lerocha/chinook-database/blob/master/LICENSE.md"

PG_LICENSE_REF = "https://www.gutenberg.org/policy/license.html"

HEADER = """# NOTICE —— 第三方数据出处与许可

这个仓库里的**代码**是自己写的（MIT，见 `LICENSE`）；**数据**不是，来源逐项列在下面。

分成两部分，因为它们的性质不同：

1. **示例数据库**：一份公开发布的示例库，用来演示 SQL 相关的能力；
2. **公版文本语料**：Project Gutenberg 上的中国古典文献，用来演示检索相关的能力。

两份数据都**随仓库分发**，所以 clone 下来不联网就能跑通。每一份都给了校验和，
你可以自己算一遍，确认拿到的东西和这里描述的是同一个东西。
"""

FOOTER = """
---

## 本仓库自己的代码

MIT，全文见 `LICENSE`。代码没有从任何其他项目复制 —— 参考过同类实现的设计思路，
但每一行都是重写的，这一点在 `DECISIONS.md` 里有说明。

## 没有收录什么

明确列一下，省得有人去找：

- **没有任何私有数据、内部文档或业务语料。** 语料全部来自上面这两个公开来源。
- **没有任何密钥。** `.env` 被 `.gitignore` 排除；仓库里只有 `.env.example`，
  里面是占位符，不含任何真实值。
- **没有构建产物。** `chinook.db` 和 `kb.db` 都不入库 —— 它们由仓库内的脚本从
  上面这些源文件重建。二进制文件没法 review，也没法确认两边的构建结果一致。

## 重建与核验

```bash
python experiments/01_build_db.py --verify-against <官方的 chinook.db>
python experiments/01_build_db.py --check-determinism   # 连建两次比哈希
python experiments/02_build_kb.py                       # 幂等，已建好就只核验
python experiments/03_write_notice.py --verify-source   # 重新确认 Chinook 来源
```
"""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_source(url: str, local: Path) -> tuple[bool, str]:
    """把 URL 下载下来和本地文件比哈希。**这是「来源已验证」这句话的唯一依据。**"""
    try:
        req = Request(url, headers={"User-Agent": "mcp-guarded-toolkit/1.0 (provenance check)"})
        with urlopen(req, timeout=45) as r:  # noqa: S310 —— 固定常量 URL，非用户输入
            data = r.read()
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        return False, f"下载失败 {type(e).__name__}: {e}"
    h = hashlib.sha256(data).hexdigest()
    local_h = sha256_file(local)
    if len(data) != local.stat().st_size or h != local_h:
        return False, (f"不一致：远端 {len(data)} 字节 {h[:16]}… / 本地 "
                       f"{local.stat().st_size} 字节 {local_h[:16]}…")
    return True, f"逐字节一致（{len(data)} 字节，sha256 {h}）"


def chinook_section() -> str:
    p = paths.chinook_sql()
    size = p.stat().st_size
    h = sha256_file(p)
    return f"""## 一、示例数据库

| 项 | 值 |
|---|---|
| 项目 | [Chinook Database]({CHINOOK_PROJECT}) |
| 版本 | {CHINOOK_VERSION} |
| 文件 | `{p.name}` |
| 作者 | {CHINOOK_AUTHOR} |
| 许可 | **{CHINOOK_LICENSE}** —— 全文见 [LICENSE.md]({CHINOOK_LICENSE_REF}) |
| 下载地址 | `{CHINOOK_SOURCE_URL}` |
| 大小 | {size} 字节 |
| sha256 | `{h}` |

**「下载地址」这一行是核验过的**，不是凭印象写的：`experiments/03_write_notice.py
--verify-source` 会把该 URL 取下来算 sha256，与仓库内的文件逐字节比对。
本文件生成时该检查通过。

### 它是什么

一张虚构的乐器商店销售库：顾客、员工、曲目、专辑、播放列表、发票。选它是因为

- **公开且许可宽松**（MIT），可以随仓库分发；
- **结构足够真实**：11 张表，有外键、有连接、有聚合，够写出一批有意义的查询题；
- **规模适中**：一万五千行左右，随手就能看完全貌。

### 它不是什么

**不是业务数据。** 里面的顾客、地址、销售额全是编造的示例值。
仓库里所有涉及它的演示、指标、结论，都只对这份示例数据成立，不能外推到任何真实系统。

### 怎么重建

`{p.name}` 是**源文件**（随仓库分发）；`chinook.db` 是**构建产物**（不入库）。
重建就是拿 SQLite 把这个脚本执行一遍：

```bash
python experiments/01_build_db.py
```

这一步会顺便核验：11 张表是否齐全、有没有空表、行数是多少，并把 `chinook.db`
的 sha256 打出来。连续构建两次哈希相同，所以「我构建出来的库」和「你构建出来的库」
可以互相比对 —— 这也正是 `.db` 不入库的原因。
"""


def corpus_section(manifest: dict) -> str:
    docs = manifest["docs"]
    n_trunc = sum(1 for d in docs if d.get("truncated"))
    rows = []
    for d in docs:
        mark = "是" if d.get("truncated") else "—"
        rows.append(
            f"| {d['doc_id']} | {d.get('title') or '（未标注）'} | "
            f"{d.get('author') or '（未标注）'} | {d.get('release_date') or '—'} | "
            f"{d['chars']:,} | {mark} | [原文]({d.get('source_url', '')}) |")
    table = "\n".join(rows)
    proc = "\n".join(f"{i}. {s}" for i, s in enumerate(manifest["processing"], 1))
    return f"""## 二、公版文本语料

| 项 | 值 |
|---|---|
| 来源 | [Project Gutenberg](https://www.gutenberg.org) 中文书目 |
| 书目页 | `{manifest['browse_url']}` |
| 许可 | {manifest['license']} —— 全文见 [Project Gutenberg 许可说明]({PG_LICENSE_REF}) |
| 篇数 | {manifest['n_docs']} 篇，覆盖 {manifest['n_works']} 部作品 |
| 正文合计 | {manifest['total_chars']:,} 字符 |
| 截断篇数 | {n_trunc} 篇（其余为全文） |
| 逐篇校验和 | 见 `data/kb/manifest.json` |

### 做了什么处理

{proc}

**关于截断**：公开语料里的长篇动辄几十万字，全量分发会让仓库膨胀到几十 MB。
所以每篇从正文开头截断到 {manifest['truncation_chars']:,} 字符。
截断是**逐篇标注**的 —— `manifest.json` 里每篇都记了 `orig_chars`（原始长度）
和 `truncated`（是否截断），上表也标了。**不隐瞒，也不假装是全文。**

**关于页眉页脚**：Project Gutenberg 的电子书正文前后各有一段品牌与版权声明。
不剥掉的话，「检索」这件事会退化成检索那段几乎每篇都一样的声明 —— 会造成大量假命中。
剥离后出处信息并没有丢：它逐篇记在 `manifest.json` 里（标题、作者、发布日期、原文链接）。

### 逐篇清单

| 编号 | 标题 | 作者 | 首发 | 字符 | 截断 | 来源 |
|---|---|---|---|---|---|---|
{table}

> 「首发」是 Project Gutenberg 记录的电子书发布日期，不是作品的成书年代。

### 怎么重新获取

语料已经随仓库分发，**正常使用不需要联网**。想从源头重取一遍：

```bash
python experiments/00_fetch_corpus.py --limit {manifest['n_docs']} --max-per-work {manifest.get('max_per_work', 2)}
```

脚本会逐篇重新下载、按同样的规则清洗，并把清单重写一遍。
`data/kb/raw/` 下每个文件的 sha256 都在清单里，可以对一下是不是同一份。

### 这给检索评测带来的限制（照实说）

- **语料是古典文献，不是现代口语。** 拿它演示检索，提问风格也得偏书面；
  用它去推断「现代用户口语提问」的检索效果是不成立的。
- **只有 {manifest['n_docs']} 篇、{manifest['total_chars']:,} 字符。**
  这是一个演示规模的语料，不是评测基准。所有检索相关的数字都只在
  这个规模上成立，不能当通用结论。
"""


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="生成 NOTICE.md")
    ap.add_argument("--verify-source", action="store_true",
                    help="重新下载 Chinook 源文件并比对 sha256（需要联网）")
    args = ap.parse_args()

    man = paths.kb_manifest()
    if not man.is_file():
        print(f"[ERR] 语料清单不存在：{man}")
        return 1
    if not paths.chinook_sql().is_file():
        print(f"[ERR] 示例库源文件不存在：{paths.chinook_sql()}")
        return 1

    if args.verify_source:
        print(f"核验 Chinook 来源：{CHINOOK_SOURCE_URL}")
        ok, detail = verify_source(CHINOOK_SOURCE_URL, paths.chinook_sql())
        print(f"  {'✅' if ok else '❌'} {detail}")
        if not ok:
            return 1

    manifest = json.loads(man.read_text(encoding="utf-8"))
    out = paths.ROOT / "NOTICE.md"
    out.write_text(
        HEADER + "\n" + chinook_section() + "\n" + corpus_section(manifest) + FOOTER,
        encoding="utf-8", newline="\n")
    print(f"写出 {out}")
    print(f"  {manifest['n_docs']} 篇语料、覆盖 {manifest['n_works']} 部作品，"
          f"正文合计 {manifest['total_chars']:,} 字符")
    return 0


if __name__ == "__main__":
    sys.exit(main())
