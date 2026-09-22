"""给实验脚本用的一小份数据：两张表的小库 + 两篇文档的小知识库。

## 为什么要有这个文件

`experiments/29`（并发实测）与 `experiments/30`（HTTP 冒烟）都要**真的干活**：
真 SQL、真 FTS 检索、真返回行。但它们**不该依赖 `data/` 下那两份构建产物** ——
那样一来 `git clone` 之后没跑过构建脚本的人就跑不了这两个实验，而它们的结论
（并发度是多少、HTTP 状态码映射对不对）跟数据有多大毫无关系。

理由与 `tests/conftest.py` 里的 `tiny_db` / `tiny_kb` 完全一样。区别只是这两个
是实验脚本而不是测试，没法用 pytest 的 fixture，所以抽成一个模块共用 ——
**同一份小数据在三个地方各抄一遍**是这类文件最容易长出来的坏味道，
而抄错的那一份不会报错，只会让某个实验悄悄测到别的东西。

## 目录形状必须与真数据根一致

`mcp_server/paths.py` 约定的是 `<数据根>/chinook/chinook.db` 与
`<数据根>/kb/kb.db`。这里照着摆 —— 实验脚本只要把 `MCP_TOOLKIT_DATA_ROOT`
指过来，`paths` 那一层的代码一个字都不用改。**形状不一致的话，改的就是被测对象**。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

# 两张表、一条外键、四行数据。
TINY_SQL = """
CREATE TABLE artist (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE album  (id INTEGER PRIMARY KEY,
                     artist_id INTEGER NOT NULL REFERENCES artist(id),
                     title TEXT NOT NULL, year INTEGER);
INSERT INTO artist (id, name) VALUES (1, 'A'), (2, 'B');
INSERT INTO album  (id, artist_id, title, year)
     VALUES (1, 1, 'First', 1999), (2, 1, 'Second', 2001), (3, 2, 'Third', 2005);
"""

# 两篇短文。**内容刻意选成语义不重叠**，好让「查甲只该命中甲」成为一条可靠断言；
# 两篇用词高度重合的话，命中顺序就不再是断言了。
TINY_DOCS = {
    "d1": ("兵法之要",
           "凡用兵之道，先察地形，後量敵情。故曰：知彼知己，百戰不殆。"),
    "d2": ("茶經節選",
           "茶之為飲，發乎神農氏。其水，用山水上，江水中，井水下。"),
}


def build(root: Path, *, docs: bool = True) -> Path:
    """在 `root` 下造出数据根，返回 `root`。`docs=False` 就只造库、不造知识库。"""
    (root / "chinook").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "chinook" / "chinook.db")
    conn.executescript(TINY_SQL)
    conn.commit()
    conn.close()

    if not docs:
        return root

    from mcp_server import kb_tools

    kb = root / "kb"
    raw = kb / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    entries = []
    for doc_id, (title, body) in TINY_DOCS.items():
        text = body + "\n"
        (raw / f"{doc_id}.txt").write_text(text, encoding="utf-8", newline="\n")
        entries.append({
            "doc_id": doc_id, "file": f"{doc_id}.txt", "title": title, "author": "",
            "work": title, "source_url": "", "license": "experiment fixture",
            "release_date": "", "chars": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    man = kb / "manifest.json"
    man.write_text(json.dumps({"n_docs": len(entries), "docs": entries},
                              ensure_ascii=False), encoding="utf-8", newline="\n")
    kb_tools.build(db_path=kb / "kb.db", manifest_path=man, force=True)
    return root
