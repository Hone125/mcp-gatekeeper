"""把 `data/kb/raw/*.txt` 建成 FTS5 全文索引。

## 这一步在干什么（用大白话说）

原始文本是一本本书；检索要的是「一句话进来，把最相关的几段话挑出来」。
中间需要一本**词典**：哪个词出现在哪一段。这个脚本就是编这本词典。

编法分两步：
1. **切开**：中文没有空格，得先切词（jieba）。「南京市长江大桥」要切成
   「南京市 / 长江大桥」而不是「南京 / 市长 / 江大桥」。
2. **建索引**：把切好的词丢给 SQLite 的 FTS5（倒排索引），它负责在查询时
   按相关性（bm25）排序返回。

## 为什么这个脚本可以无脑重跑

索引已经存在且核验通过时，它什么都不做直接返回。所以 `tools/check.py` 里
可以放心把它串进出，不用先判断「该不该跑」。

退出码：0 = 成功；1 = 核验不通过；2 = 源文件缺失。
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, kb_tools, paths  # noqa: E402


def roundtrip_sample(db: Path, n: int = 20, seed: int = 20260918) -> list[str]:
    """抽样核验偏移量：重新切一次词，看是否与索引里存的一致。

    **这是在防一类不会报错的 bug。** 如果构建期和读取期对「怎么切片」的理解
    差了一个字符，检索返回的就会是一段**看起来通顺、其实错位**的文本 ——
    没有异常、没有报错，只有内容悄悄错了。用固定随机种子抽样重算，能让它现形。

    用固定种子是为了可复现：同一次构建，任何人跑出的抽样集合都一样。
    """
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT rowid, doc_id, char_start, char_end, body_tok FROM chunks_fts").fetchall()
        texts = {r[0]: r[1] for r in conn.execute("SELECT doc_id, text FROM documents")}
    finally:
        conn.close()

    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))
    problems = []
    for rowid, doc_id, cs, ce, stored in sample:
        text = texts.get(doc_id)
        if text is None:
            problems.append(f"rowid={rowid} 的 doc_id={doc_id} 在 documents 里不存在")
            continue
        fresh = kb_tools.tokenize(text[cs:ce])
        if fresh != stored:
            problems.append(
                f"rowid={rowid} doc={doc_id} [{cs}:{ce}] 重新切词后与索引不一致"
                f"（索引 {len(stored)} 字符 / 重算 {len(fresh)} 字符）")
    return problems


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="构建全文索引（jieba 切词 + FTS5）")
    ap.add_argument("--force", action="store_true", help="即使索引已存在也重建")
    ap.add_argument("--sample", type=int, default=20, help="抽样核验的块数（默认 20，设 0 跳过）")
    args = ap.parse_args()

    man = paths.kb_manifest()
    if not man.is_file():
        print(f"[ERR] 清单不存在：{man}")
        print("      先跑 python experiments/00_fetch_corpus.py，或从仓库里恢复 data/kb/。")
        return 2

    raw_dir = paths.kb_raw_dir()
    n_raw = len(list(raw_dir.glob("*.txt"))) if raw_dir.is_dir() else 0
    if n_raw == 0:
        print(f"[ERR] 原始文本目录是空的：{raw_dir}")
        return 2

    print(f"语料：{n_raw} 篇 → {raw_dir}")
    print(f"索引：{paths.kb_db()}")
    print("切词中（首次加载 jieba 词典约需 1 秒）……")

    try:
        result = kb_tools.build(force=args.force)
    except kb_tools.KBError as e:
        print(f"[ERR] {e.err_code}: {e.detail}")
        return 2

    if not result["rebuilt"]:
        print("索引已存在且核验通过，跳过构建（要重建加 --force）")

    print(f"\n构建结果")
    print(f"  文档      {result['n_docs']:>7}")
    print(f"  索引块    {result['n_chunks']:>7}")
    print(f"  正文合计  {result['total_chars']:>7} 字符")
    print(f"  平均每块  {result['total_chars'] // max(1, result['n_chunks']):>7} 字符")

    problems = list(result["problems"])

    if args.sample:
        rt = roundtrip_sample(paths.kb_db(), args.sample)
        print(f"\n抽样往返核验（{args.sample} 块，固定种子 20260918）")
        if rt:
            problems.extend(rt)
            for p in rt:
                print(f"  ❌ {p}")
        else:
            print(f"  ✅ {args.sample} 块重新切片、重新切词后与索引完全一致")

    # 顺手做一次真实查询，证明索引是「能查的」而不只是「建好了」
    probe = kb_tools.search_passages("兵法 用兵", top_k=3)
    print("\n探针查询：兵法 用兵")
    if probe["ok"]:
        for h in probe["hits"]:
            head = h["text"].replace("\n", " ")[:42]
            print(f"  [{h['score']:>9.4f}] {h['title'][:24]:<26} {head}…")
    else:
        print(f"  ❌ {probe['err_code']}: {probe['detail']}")
        problems.append(f"探针查询失败：{probe['err_code']}")

    # 留一份机器可读的结果，供报告和自检引用
    out = paths.reports_dir() / "kb_build.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n_docs": result["n_docs"], "n_chunks": result["n_chunks"],
        "total_chars": result["total_chars"], "avg_chunk_chars":
            result["total_chars"] // max(1, result["n_chunks"]),
        "chunk_target_chars": kb_tools.CHUNK_CHARS,
        "sample_checked": args.sample, "sample_seed": 20260918,
        "sample_problems": len(problems),
        "ok": not problems,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"\n结果 → {out}")

    if problems:
        print("\n[FAIL] 核验未通过：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n[OK] 索引构建完成并通过核验")
    return 0


if __name__ == "__main__":
    sys.exit(main())
