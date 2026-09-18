"""测试基建。

## 两条纪律

**1. 测试一律不联网、不调模型。** 断言只建立在「仓库里已有的文件」和「零依赖的本地重算」上。
一条需要联网或需要 API key 才能过的测试，等于一条在别人机器上必然失败的测试。

**2. 不依赖 `data/` 下的构建产物。** 需要数据库的测试自己在临时目录里造一个小库
（见 `tiny_db`），这样 `git clone` 之后**不跑任何构建脚本**也能直接 `pytest` 全绿。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 让 `import mcp_server.*` 在测试里可用。仓库根必须在 sys.path 上。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def child_env(**overrides) -> dict:
    """拉起子进程时用的环境：**显式钉死 stdio 编码为 UTF-8**。

    本文件里所有断言都按 `decode("utf-8")` 读子进程输出。那就得自己把这个约定
    设定好 —— **`PYTHONIOENCODING` 的优先级高于 `-X utf8`**，所以外层环境里只要
    有人设了 `gbk`，子进程就按 GBK 写，这边解出来一片乱码，断言随之误报。

    这不是假设出来的：`PYTHONIOENCODING=gbk python experiments/08_mcp_smoke.py`
    实测会让「stderr 里有没有启动信息」这条检查红掉，而服务端什么都没做错。
    """
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(overrides)
    return env


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture
def tiny_db(tmp_path: Path) -> Path:
    """造一个两表带外键的小库，供护栏与执行层的测试使用。

    刻意不用仓库的示例库：那些测试要能在数据还没构建时也跑。
    """
    path = tmp_path / "tiny.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE artist (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE album  (id INTEGER PRIMARY KEY,
                             artist_id INTEGER NOT NULL REFERENCES artist(id),
                             title TEXT NOT NULL, year INTEGER);
        INSERT INTO artist (id, name) VALUES (1, 'A'), (2, 'B');
        INSERT INTO album  (id, artist_id, title, year)
             VALUES (1, 1, 'First', 1999), (2, 1, 'Second', 2001), (3, 2, 'Third', 2005);
        """
    )
    conn.commit()
    conn.close()
    return path


# 自造语料用的两篇短文。内容刻意选成语义不重叠的两段，好让检索测试能断言
# 「查甲只该命中甲」—— 如果两篇用词高度重合，命中顺序就不再是可靠断言了。
_TINY_DOCS = {
    "d1": (
        "兵法之要",
        "凡用兵之道，先察地形，後量敵情。故曰：知彼知己，百戰不殆。"
        "夫兵者，國之大事，死生之地，存亡之道，不可不察也。",
    ),
    "d2": (
        "茶經節選",
        "茶之為飲，發乎神農氏。其水，用山水上，江水中，井水下。"
        "其沸，如魚目，微有聲，為一沸。",
    ),
}


@pytest.fixture
def tiny_kb(tmp_path: Path) -> Path:
    """造一个两篇文档的小知识库，返回它的 db 路径。

    和 `tiny_db` 同样的理由：知识库测试不该依赖 `data/kb/` 里那份 40 篇的构建产物 ——
    那样一来 `git clone` 之后不跑构建脚本就没法跑测试了。
    """
    from mcp_server import kb_tools

    kb_dir = tmp_path / "kb"
    raw = kb_dir / "raw"
    raw.mkdir(parents=True)
    docs = []
    for doc_id, (title, body) in _TINY_DOCS.items():
        text = body + "\n"
        (raw / f"{doc_id}.txt").write_text(text, encoding="utf-8", newline="\n")
        docs.append({
            "doc_id": doc_id, "file": f"{doc_id}.txt", "title": title, "author": "",
            "work": title, "source_url": "", "license": "test fixture",
            "release_date": "", "chars": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
    man = kb_dir / "manifest.json"
    man.write_text(json.dumps({"n_docs": len(docs), "docs": docs}, ensure_ascii=False),
                   encoding="utf-8", newline="\n")

    db = kb_dir / "kb.db"
    kb_tools.build(db_path=db, manifest_path=man, force=True)
    return db


@pytest.fixture(scope="session")
def run_py():
    """跑一个仓库内的脚本，返回 CompletedProcess（stdout/stderr 已解码成 `out`）。

    带超时：没有超时的子进程调用会在服务端卡死时把整个测试套件挂住。
    """

    def _inner(rel: str, *extra: str, timeout: int = 300) -> subprocess.CompletedProcess:
        p = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(ROOT / rel), *extra],
            cwd=str(ROOT), capture_output=True, timeout=timeout, env=child_env(),
        )
        p.out = (p.stdout + p.stderr).decode("utf-8", errors="replace")  # type: ignore[attr-defined]
        return p

    return _inner
