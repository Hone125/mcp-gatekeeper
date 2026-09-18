"""从建库脚本构建示例数据库，并核验构建结果。

## 为什么分发 `.sql` 而不是直接分发 `.db`

二进制文件没法 review。把它换成一个几百 KB 的文本建库脚本，任何人都能在自己的机器上
重新构建一遍，然后拿两次构建的哈希对一下 —— 「我拿到的库和作者拿到的库是不是同一个东西」
这件事就变成可验证的了。`.db` 因此不入库（见 `.gitignore`）。

## 三种运行方式

    python experiments/01_build_db.py                       # 缺就构建，已有就只核验
    python experiments/01_build_db.py --force               # 强制重建
    python experiments/01_build_db.py --verify-against X.db  # 与另一个库逐表比行数
    python experiments/01_build_db.py --check-determinism    # 连建两次，比哈希

退出码：0 = 成功；1 = 核验不通过；2 = 源文件缺失。
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, paths  # noqa: E402

# 期望存在的表。少一张或多一张都要报出来 —— 这是「换库了」最早能被发现的地方。
EXPECTED_TABLES = ("Album", "Artist", "Customer", "Employee", "Genre", "Invoice",
                   "InvoiceLine", "MediaType", "Playlist", "PlaylistTrack", "Track")


def build_into(db_path: Path, sql_path: Path) -> None:
    """把建库脚本执行一遍，写出一个新的库文件。已存在会先删掉，保证是全新构建。"""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql_path.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def survey(db_path: Path) -> dict:
    """列出所有用户表及其行数。"""
    conn = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(actual: dict) -> list[str]:
    """核验构建结果。返回问题清单，空列表 = 通过。"""
    problems = []
    missing = [t for t in EXPECTED_TABLES if t not in actual]
    extra = [t for t in actual if t not in EXPECTED_TABLES]
    if missing:
        problems.append(f"缺表：{missing}")
    if extra:
        problems.append(f"多出未预期的表：{extra}")
    empty = [t for t, n in actual.items() if n == 0]
    if empty:
        problems.append(f"空表：{empty}")
    return problems


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="构建并核验示例数据库")
    ap.add_argument("--force", action="store_true", help="即使库已存在也重建")
    ap.add_argument("--verify-against", metavar="OTHER.db",
                    help="与另一个 SQLite 库逐表比行数（用于核对构建脚本与官方二进制一致）")
    ap.add_argument("--check-determinism", action="store_true",
                    help="连建两次并比较 SHA-256，证明构建是确定性的")
    args = ap.parse_args()

    sql_path, db_path = paths.chinook_sql(), paths.chinook_db()
    if not sql_path.is_file():
        print(f"[ERR] 建库脚本不存在：{sql_path}")
        print("      见 NOTICE.md 的数据来源，或跑 experiments/00_fetch_corpus.py 重新获取。")
        return 2

    if args.force or not db_path.is_file():
        print(f"构建中：{sql_path.name} → {db_path}")
        build_into(db_path, sql_path)
    else:
        print(f"库已存在，跳过构建：{db_path}（要重建加 --force）")

    actual = survey(db_path)
    print(f"\n构建结果（{db_path}）")
    print(f"  sha256 = {sha256(db_path)}")
    total = 0
    for name, n in actual.items():
        print(f"  {name:<16} {n:>7}")
        total += n
    print(f"  {'合计':<15} {total:>7}")

    problems = check(actual)
    code = 0

    if args.verify_against:
        other = Path(args.verify_against)
        if not other.is_file():
            print(f"\n[ERR] 对比用的库不存在：{other}")
            return 2
        ref = survey(other)
        print(f"\n与 {other.name} 逐表对比")
        diffs = []
        for name in sorted(set(actual) | set(ref)):
            a, b = actual.get(name), ref.get(name)
            flag = "✅" if a == b else "❌"
            if a != b:
                diffs.append(f"{name}: 构建 {a} vs 参照 {b}")
            print(f"  {flag} {name:<16} {a} vs {b}")
        if diffs:
            problems.append(f"行数不一致：{diffs}")
        else:
            print("  ✅ 全部一致 —— 构建脚本与参照库内容相同")

    if args.check_determinism:
        with tempfile.TemporaryDirectory() as td:
            p2 = Path(td) / "again.db"
            build_into(p2, sql_path)
            h1, h2 = sha256(db_path), sha256(p2)
            same = h1 == h2
            print(f"\n确定性检查\n  第一次 = {h1}\n  第二次 = {h2}\n  {'✅ 一致' if same else '❌ 不一致'}")
            if not same:
                problems.append("连续两次构建的哈希不同，构建不是确定性的")

    if problems:
        print("\n[FAIL] 核验未通过：")
        for p in problems:
            print(f"  - {p}")
        code = 1
    else:
        print("\n[OK] 核验通过")

    return code


if __name__ == "__main__":
    sys.exit(main())
