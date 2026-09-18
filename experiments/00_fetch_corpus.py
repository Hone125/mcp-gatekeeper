"""获取公版中文文本语料，清洗后落到 `data/kb/raw/`，并写出清单。

## 这个脚本是**可选的**

语料已经随仓库分发（`data/kb/raw/*.txt` + `manifest.json`），clone 下来不需要联网。
这个脚本存在的意义是两个：

1. **可复现**：任何人都能重跑一遍，验证仓库里的文本确实来自它声称的来源。
2. **可替换**：想换成自己的一套公版文本时，改这里而不是手搓文件。

## 清洗口径（这是「口径」，报告里的每个数字都依赖它）

1. 去掉 Project Gutenberg 的页眉与页脚（`*** START/END OF ... ***` 之间的版权声明段）。
   保留它们会让「检索」这件事变成检索版权声明 —— 几乎每篇都有同一段文字，
   会造成大量假命中。出处信息不丢：它记在 `manifest.json` 里，逐篇一行。
2. Unicode 归一化成 **NFC**。同一个汉字可能有多种码点组合，不归一化的话
   「按字符位置取原文」在跨平台时会对不上。
3. 统一换行为 `\\n`。
4. **截断到每篇 `--max-chars` 个字符**（默认 30000）。公开语料里长篇动辄几十万字，
   全量分发会让仓库膨胀到几十 MB。截断是可复现的（固定从正文开头取），
   且 `manifest.json` 里逐篇记了 `orig_chars` 与 `truncated` 标记，**不隐瞒**。

## 退出码

0 = 全部成功；1 = 有篇目失败但仍取够了数量；2 = 候选列表都取不到（网络问题）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402

BROWSE_URL = "https://www.gutenberg.org/browse/languages/zh"
CACHE_URL = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
FILES_URL = "https://www.gutenberg.org/files/{id}/{id}-0.txt"

UA = "mcp-guarded-toolkit/1.0 (corpus fetch; contact via repository issues)"

_RE_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
                       re.I | re.S)
_RE_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*", re.I | re.S)
_RE_TITLE = re.compile(r"^Title:\s*(.+)$", re.M)
_RE_AUTHOR = re.compile(r"^Author:\s*(.+)$", re.M)
_RE_RELEASE = re.compile(r"^Release date:\s*(.+?)(?:\s*\[.*)?$", re.M)
_RE_LANG = re.compile(r"^Language:\s*(.+)$", re.M)

# 分卷标题的归一化：`韓詩外傳, Vol. 1-2` / `粉妝樓1-10回` / `夢溪筆談, Volume 01-06`
# 都归到同一个作品名下，这样才能限制「同一部书最多占几篇」。
_RE_VOL_SUFFIX = re.compile(r"\s*(?:volume|vol\.?)\s*[\divxlcd\-\s]*$", re.I)
_RE_JUAN_SUFFIX = re.compile(r"[\d\-–—]+\s*回$")
_RE_TRAILING_NUM = re.compile(r"\s*\d+\s*$")


def work_key(title: str) -> str:
    """把分卷标题折叠成作品名。用于「同一部书最多取 N 篇」的计数。"""
    t = title.split(",")[0].split("：")[0].strip()
    t = _RE_VOL_SUFFIX.sub("", t)
    t = _RE_JUAN_SUFFIX.sub("", t)
    t = _RE_TRAILING_NUM.sub("", t)
    return t.strip() or title.strip()


def cache_dir() -> Path:
    """下载缓存。原始响应存在这里，重跑时不必再打一次对方站点。"""
    d = paths.cache_dir() / "fetch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(url: str, timeout: int = 60, retries: int = 3) -> str:
    """取一个 URL，返回文本。失败重试，仍失败抛最后一个异常。"""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as r:  # noqa: S310 —— 固定域名，非用户输入
                return r.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    assert last is not None
    raise last


def list_candidates() -> list[int]:
    """从语言浏览页抓出所有中文书目的编号。"""
    html = fetch(BROWSE_URL)
    ids = sorted({int(m) for m in re.findall(r"/ebooks/(\d+)", html)})
    return ids


def strip_boilerplate(raw: str) -> str:
    """去掉 PG 页眉页脚，归一化并统一换行。**不做截断。**"""
    m = _RE_START.search(raw)
    body = raw[m.end():] if m else raw
    m2 = _RE_END.search(body)
    if m2:
        body = body[:m2.start()]
    body = unicodedata.normalize("NFC", body)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    # 收敛连续空行，否则截断预算会被空白吃掉一大块
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def parse_meta(raw: str) -> dict:
    """从页眉里抠出元数据。抠不到就留空 —— 不编。"""
    def first(rx):
        m = rx.search(raw)
        return m.group(1).strip() if m else ""
    return {
        "title": first(_RE_TITLE),
        "author": first(_RE_AUTHOR),
        "release_date": first(_RE_RELEASE),
        "language": first(_RE_LANG),
    }


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="获取公版中文语料并写清单")
    ap.add_argument("--limit", type=int, default=40, help="目标篇数（默认 40）")
    ap.add_argument("--max-chars", type=int, default=30000, help="每篇截断到多少字符（默认 30000）")
    ap.add_argument("--min-chars", type=int, default=4000, help="短于此长度的篇目跳过（默认 4000）")
    ap.add_argument("--max-per-work", type=int, default=2,
                    help="同一部作品最多取几卷（默认 2）。公开语料里长篇动辄十几卷，"
                         "不限制的话 40 篇可能只覆盖 8 部书，检索演示会失真。")
    ap.add_argument("--sleep", type=float, default=0.7, help="每篇之间的间隔秒数，别把人家站点打疼")
    ap.add_argument("--force", action="store_true", help="忽略断点续跑，重新处理全部")
    args = ap.parse_args()

    out_dir = paths.kb_raw_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"抓取候选列表：{BROWSE_URL}")
    try:
        candidates = list_candidates()
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] 取不到候选列表：{type(e).__name__}: {e}")
        return 2
    print(f"候选 {len(candidates)} 篇，目标取 {args.limit} 篇（每篇截断到 {args.max_chars} 字符）")

    cache = cache_dir()
    entries: list[dict] = []
    skipped: list[dict] = []
    per_work: dict[str, int] = {}

    for book_id in candidates:
        if len(entries) >= args.limit:
            break

        # 原始响应优先从缓存取。缓存的存在让这个脚本可以随时重跑、
        # 也可以在断网时重跑 —— 已经抓到的部分不会白费。
        cached = cache / f"{book_id}.txt"
        if cached.is_file() and not args.force:
            raw = cached.read_text(encoding="utf-8", errors="replace")
        else:
            try:
                raw = fetch(CACHE_URL.format(id=book_id))
            except Exception:
                try:
                    raw = fetch(FILES_URL.format(id=book_id))
                except Exception as e:  # noqa: BLE001
                    skipped.append({"id": book_id, "reason": f"下载失败 {type(e).__name__}: {e}"})
                    print(f"  [skip] {book_id} 下载失败：{type(e).__name__}")
                    time.sleep(args.sleep)
                    continue
            cached.write_text(raw, encoding="utf-8", newline="\n")
            time.sleep(args.sleep)

        body = strip_boilerplate(raw)
        if len(body) < args.min_chars:
            skipped.append({"id": book_id, "reason": f"正文仅 {len(body)} 字符，短于下限"})
            continue

        meta = parse_meta(raw)
        key = work_key(meta["title"] or str(book_id))
        if per_work.get(key, 0) >= args.max_per_work:
            skipped.append({"id": book_id, "reason": f"同一作品《{key}》已取满 {args.max_per_work} 卷"})
            continue
        per_work[key] = per_work.get(key, 0) + 1

        orig_chars = len(body)
        truncated = orig_chars > args.max_chars
        body = body[:args.max_chars]

        dest = out_dir / f"{book_id}.txt"
        dest.write_text(body, encoding="utf-8", newline="\n")
        entry = {
            "doc_id": str(book_id),
            "file": dest.name,
            "work": key,
            "title": meta["title"],
            "author": meta["author"],
            "language": meta["language"],
            "release_date": meta["release_date"],
            "source_url": f"https://www.gutenberg.org/ebooks/{book_id}",
            "text_url": CACHE_URL.format(id=book_id),
            "license": "Public domain in the United States",
            "license_ref": "https://www.gutenberg.org/policy/license.html",
            "note": "已移除 Project Gutenberg 页眉页脚（其中含品牌与版权声明）；"
                    "逐篇出处（标题/作者/发布日期/原文链接）记于本清单。",
            "orig_chars": orig_chars,
            "chars": len(body),
            "truncated": truncated,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
        entries.append(entry)
        print(f"  [{len(entries):>2}/{args.limit}] {book_id}  {entry['title'][:30]:<32}"
              f" {entry['chars']:>6} 字符{'（已截断）' if truncated else ''}")

    entries.sort(key=lambda e: e["doc_id"])
    manifest = {
        "source": "Project Gutenberg（https://www.gutenberg.org），中文书目",
        "browse_url": BROWSE_URL,
        "license": "Public domain in the United States",
        "license_ref": "https://www.gutenberg.org/policy/license.html",
        # 这里只陈述脚本**实际做了什么**，不对许可条款做解释 ——
        # 解释别人的许可条款是我没有依据的事，写进去就是编造。许可原文链接在 license_ref。
        "processing": [
            "移除 Project Gutenberg 页眉页脚（其中含品牌与版权声明，逐篇内容几乎相同）",
            "Unicode NFC 归一化，换行统一为 \\n",
            f"从正文开头截断到每篇 {args.max_chars} 字符",
            f"同一作品最多取 {args.max_per_work} 卷，保证篇目覆盖足够多的不同作品",
        ],
        "truncation_chars": args.max_chars,
        "max_per_work": args.max_per_work,
        "n_docs": len(entries),
        "n_works": len(per_work),
        "total_chars": sum(e["chars"] for e in entries),
        "skipped": skipped,
        "docs": entries,
    }
    paths.kb_manifest().write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")

    print(f"\n写出 {len(entries)} 篇（覆盖 {len(per_work)} 部作品）→ {out_dir}")
    print(f"清单 → {paths.kb_manifest()}")
    print(f"合计 {manifest['total_chars']} 字符；截断篇数 "
          f"{sum(1 for e in entries if e['truncated'])}；跳过 {len(skipped)} 篇")
    if len(entries) < args.limit:
        print(f"[WARN] 只取到 {len(entries)} 篇，少于目标的 {args.limit} 篇")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
