"""交付级自检的**驱动器**：把 15 项跑全，包括闸门自己跑不了的那两项。

## 它和闸门是什么关系

15 项的判定逻辑在 `mcp_server/delivery_selfcheck.py`。那个模块被两处调用：

| 调用方 | 跑什么 | 为什么不能跑全 |
|---|---|---|
| `experiments/26_final_selfcheck.py`（闸门第 4 步） | 13 项（离线） | 第 11 项要问「闸门退出码」，而它就是闸门的一部分 —— **跑自己会套娃**；第 4、13 项要联网，闸门必须能在断网机器上跑完 |
| **本脚本** | **15 项（可联网）** | 没有限制 —— 它不在闸门里面 |

## 为什么第 11 项在这里能真跑

本脚本不在闸门里，所以它可以**真的把闸门跑一遍**，拿到的是实测退出码，
不是推论。这也是为什么两份报告的同一项措辞不同：闸门里那一份写的是「由四步推得」，
这里写的是「本项自己跑出来的」。

## 落盘

- `reports/delivery_selfcheck.md` —— 15 项全文（含掩码后的命中行）
- `reports/delivery_selfcheck.json` —— 同一批判定的机器可读版

退出码：0 = 无 FAIL/ERROR；1 = 有 FAIL；2 = 有 ERROR；5 = 只有 SKIP（**不是通过**）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server import console, delivery_selfcheck as dsc  # noqa: E402
from mcp_server import paths  # noqa: E402
from mcp_server import verify_kit as vk  # noqa: E402

PY = sys.executable
E_OK, E_FAIL, E_ERROR, E_PENDING = 0, 1, 2, 5

# 第 13 项要的那个接口地址**现算**，不写死在这里：地址一处可改，见 `paths.repo_slug()`。
# ★ 原来这两行是 `dsc._load_27().RAW_URL` 拆出来的 —— 那样写有个坑：
#   `_load_27()` 返回 `None` 时，模块 import 阶段就 `AttributeError` 崩掉，
#   连 `--help` 都打不出来。地址搬进 `paths` 之后，这个坑自然没有了。


def _run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    """跑一条命令。**产物不留作者机器的路径**，理由同 `26` 的 `_run`。"""
    try:
        p = subprocess.run([PY, *cmd], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）"
    except OSError as e:
        return -1, f"起不来：{type(e).__name__}: {e}"
    tail = (p.stdout or "").strip().splitlines()[-3:]
    err = (p.stderr or "").strip().splitlines()[-2:]
    return p.returncode, vk.tail_for_report(" / ".join(tail + err), limit=400)


def run_gate() -> tuple[int, str]:
    """真的跑一遍闸门，并把四步各自的判定抠出来做备注。

    ★ 抠的是闸门**自己打印的判定行**，不是我另做一次解释 ——
    另做一次就又多了一处会跟闸门不一致的地方。
    ★ 闸门**只跑一遍**（约两分钟）：跑两遍拿同一份结论，纯属白花时间。
    """
    try:
        p = subprocess.run([PY, "tools/check.py"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=2400)
        code, out = p.returncode, (p.stdout or "")
    except subprocess.TimeoutExpired:
        return -1, "超时（2400s）"
    except OSError as e:
        return -1, f"起不来：{type(e).__name__}: {e}"
    steps: list[str] = []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\[(\d+)/(\d+)\]", line.strip())
        if m and i + 1 < len(lines):
            steps.append(f"{m.group(1)}={lines[i + 1].strip()[:24]}")
    return code, vk.tail_for_report(" / ".join(steps) if steps
                                    else "（闸门输出里没抠到判定行）", limit=400)


def remote_sha() -> str:
    """远端 `main` 最新提交的 sha。**地址解析不出来、或取不到，都返回空串** ——
    两者由交付级第 13 项分开判（地址没有 → SKIP；地址有但取不到 → ERROR）。"""
    url, _why = paths.repo_api_commits_url()
    if not url:
        return ""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "mcp-guarded-toolkit-selfcheck",
                          "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("sha", "")
    except Exception:  # noqa: BLE001
        return ""


def local_head() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8")
    return p.stdout.strip() if p.returncode == 0 else ""


def _pytest_n(tail: str) -> str:
    m = re.search(r"(\d+)\s+passed", tail)
    return f"{m.group(1)} 条通过" if m else "（没读通过数）"


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="交付级自检：15 项跑全（可联网）")
    ap.add_argument("--online", action="store_true",
                    help="连公网跑第 4、13 项；不加这两项判 SKIP")
    args = ap.parse_args()

    deps: dict = {}
    print("[1/4] 跑单元测试 …")
    deps["pytest_rc"], pt_tail = _run(["-m", "pytest", "-q"], timeout=900)
    deps["pytest_n"] = _pytest_n(pt_tail)

    print("[2/4] 跑守门链（含第 4 步，约两分钟）…")
    deps["gate_rc"], deps["steps_note"] = run_gate()
    deps["steps_note"] += f"；pytest={deps['pytest_rc']}"

    if args.online:
        print("[3/4] 拉公网那份文件 + 远端 sha …")
        # 取回与扫描的口径在 `mcp_server/delivery_selfcheck.py` 里 ——
        # 写在脚本里就没法被测试钉住（`experiments/` 下的脚本不能 import），
        # 而这条判据恰恰在第一次真跑时被写反过。
        deps["remote_hits"], deps["remote_note"] = dsc.fetch_remote_hits()
        deps["local_head"] = local_head()
        deps["remote_sha"] = remote_sha()
    else:
        print("[3/4] 离线：第 4、13 项判 SKIP")
        deps["local_head"] = local_head()

    print("[4/4] 交付物那 6 项（靠 DELIVERY_RESUME_DIR）…")
    items = dsc.run(deps=deps, online=args.online)

    text = dsc.render_report(items, args.online)
    banned = dsc.write_report(text)
    dsc.write_json(items, args.online)

    n = dsc.totals(items)
    for i in items:
        mark = {"PASS": " ", "FAIL": "!", "SKIP": "-", "ERROR": "E"}[i.status]
        print(f"  [{mark}] {i.n:>2}. {i.title:<24} {i.status:<5} 违规 {i.n_hits}"
              + (f"  {i.note[:60]}" if i.note else ""))
    print(f"\n合计 15 项：PASS {n['PASS']} / FAIL {n['FAIL']} / "
          f"SKIP {n['SKIP']} / ERROR {n['ERROR']}")
    print(f"报告：reports/delivery_selfcheck.md")

    if banned:
        print(f"[FAIL] 报告里出现禁用词 {sorted(set(banned))}", file=sys.stderr)
        return E_FAIL
    if n["ERROR"]:
        return E_ERROR
    if n["FAIL"]:
        return E_FAIL
    if n["SKIP"]:
        print(f"[未完成] {n['SKIP']} 项 SKIP：没有实测依据，不是通过。"
              f"交付物那几项的最小动作是设 DELIVERY_RESUME_DIR；"
              f"联网那两项加 --online", file=sys.stderr)
        return E_PENDING
    print("[OK] 15 项全部通过")
    return E_OK


if __name__ == "__main__":
    sys.exit(main())
