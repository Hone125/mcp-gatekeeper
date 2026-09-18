"""守门链：一条命令回答「现在能不能交出去」。

## 它和 `19_verify.py` / `25_deliverable_check.py` 的关系

那两份是**台账**：把每一项的实测结果摊开给人看，包括不合格的那些。
这一份是**闸门**：按顺序跑，遇到第一个不过的就停下，退出码告诉你卡在第几步。

两者都要有，因为它们的读者不同：

- 台账给「想知道细节的人」——它必须跑完全部，把失败也列出来；
- 闸门给「决定要不要发布的那一刻」——它只要一个数字：第几步断了。
  CI 里挂一个退出码就够了，不需要人去读表。

## 为什么按这个顺序

**从快到慢、从局部到整体。** 单元测试几十秒、覆盖最细；`19_verify` 要把每个入口脚本
真的跑一遍（几分钟）；`25` 要算全仓文档和数据的哈希。前面断了后面就不用跑 ——
`pytest` 红的时候去读数据校验和是浪费时间。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部通过（可以交出去） |
| 1..N | **第一个不是 OK 的步骤的序号**（1 = 单元测试、2 = 自检、3 = 交付物核验、4 = 收尾自检） |
| 90 | 用法错误 |

## `--snapshot`：证明产物可复现

```powershell
python tools/check.py --snapshot   # 记下哈希
python tools/check.py              # 跑闸门（会重写 reports/ 下的产物）
python tools/check.py --snapshot   # 应当还是同一个哈希
```

两次相同 = 同一份代码跑两遍，产物**逐字节相同**，也就是跑完 `git status`
还是干净的。这件事不是自动成立的：产物里只要混进一个时钟读数
（`pytest` 尾巴里那个「N passed in …s」就是一种），它每跑一次就不一样
（见 `DECISIONS.md` D-38）。`tests/test_repo_hygiene.py` 里那条静态检查守着它 ——
所以这里**故意不写一个真实的秒数当例子**：那会变成一个「示例本身就是违规样本」的坑。

★ 用法错误**不能**用 2 或 4：链条现在有 4 步，那两个码已经表示「卡在第 2 / 第 4 步」。
一个会和正常结果撞车的错误码，比没有错误码更糟 —— 读到 4 的人会去查收尾自检，
而真正的问题是他把参数敲错了。

「不是 OK」包含两种：`失败`（这一步自己报了错）和 `未完成`（这一步说它依赖的东西
还没就绪，比如模型凭据没配）。两者都给非零退出码 —— **闸门只认「全都真的过了」**，
但它会把两者分开打印，因为处理方式不同。

**「未完成」必须由脚本自己在文件头声明退出码**（`pending_exit`），闸门只记录、
不改写它的含义。谁都可以给自己的脚本加一条「合理地不通过」的理由，
所以那条理由写在脚本里、和 `BLOCKERS.md` 里的最小动作一一对应，别人读得到。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, verify_kit as vk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

E_OK = 0
E_USAGE = 90       # 见文件头的退出码表：不能与「步骤序号」撞车

# 链条。`pending_exit` 是「这一步说它自己还没就绪」的退出码，由脚本自己在文件头声明。
#
# ★ 第 4 步第一次用上了这个字段。它判 5 的原因**不是仓库里写错了什么**，
#   而是本机缺一个**仓外**文件：三张词表不在仓库里（见 `mcp_server/selfcheck.py`
#   文件头「词表住在仓库外面」），读不到时收尾自检的第 1~3 项判 SKIP。
#   收尾自检是**发布结论**，所以「9 项里有 3 项没资格下结论」不能算通过；
#   但也不该显示成「失败」—— 那会让人去翻仓库内容，而该做的是造一个词表文件。
#   最小动作写在 `BLOCKERS.md` 卡点 3，与这里的 `pending_why` 一一对应。
#
# 需要模型凭据的那三条链路依旧**不在**闸门里：它们在 `19_verify.py` 的台账里。
STEPS: list[dict] = [
    {"cmd": ["-m", "pytest", "-q"],
     "why": "单元测试：最细一层，几十秒，覆盖鉴权矩阵 / SQL 护栏 / 偏移量 / 报告口径"},
    {"cmd": ["experiments/19_verify.py"],
     "why": "台账式自检：真的把每个入口脚本跑一遍，比对声明的退出码"},
    {"cmd": ["experiments/25_deliverable_check.py"],
     "why": "交付物核验：文档里写的和仓库里的对不对得上"},
    {"cmd": ["experiments/26_final_selfcheck.py"],
     "why": "收尾自检：发布前 9 项红线逐条报命中数，产出 reports/final_selfcheck.md。"
            "它自己会再跑一次 pytest 与 19_verify（为了自证而不是引用别人的结论），"
            "所以整个闸门大约多花一分钟",
     "pending_exit": 5,
     "pending_why": "词表未随仓分发（见 BLOCKERS.md 卡点 3）：第 1~3 项判 SKIP，"
                    "没有实测依据，不是通过"},
]


def _run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    """跑一步。返回 `(退出码, 输出尾部)`；跑不起来返回 -1。

    ★ 尾部除了最后几行，还要**专门捞回 `FAILED` / `ERROR` 行**。
    实测踩过：`pytest -q` 的失败摘要形如

        ===== short test summary info =====
        FAILED tests/x.py::test_y - AssertionError
        1 failed, 519 passed in 22.90s

    只留最后三行的话，`FAILED` 那行**正好卡在边界上**，有时留下、有时被切掉 ——
    而它是唯一告诉你「哪条测试红了」的一行。一个不说是谁红了的闸门，
    读的人只能去重跑一遍再猜。
    """
    full = [sys.executable, *cmd]
    try:
        p = subprocess.run(full, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"超时（{timeout}s）"
    except OSError as e:
        return -1, f"起不来：{type(e).__name__}: {e}"
    out_lines = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    named = [ln for ln in out_lines + err
             if ln.strip().startswith(("FAILED", "ERROR"))][:5]
    tail = out_lines[-3:]
    return p.returncode, " / ".join([*named, *tail, *err[-2:]])[:400]


def main() -> int:
    console.setup_stdio()
    ap = argparse.ArgumentParser(description="守门链：一条命令回答「能不能交出去」")
    ap.add_argument("--list", action="store_true", help="只打印链条，不执行")
    ap.add_argument("--only", type=int, default=0,
                    help="只跑第 N 步（1 起算），用于定位问题")
    ap.add_argument("--snapshot", action="store_true",
                    help="只打印 reports/ 的聚合哈希，不执行任何步骤")
    args = ap.parse_args()

    if args.snapshot:
        # 用法：跑闸门前后来各一次，两次哈希相同 = 产物逐字节可复现。
        # 这条声明有分量，所以给它一个可重跑的命令，而不是让读的人相信我说的。
        digest, n = vk.artifacts_hash()
        print(f"reports/ 聚合哈希：{digest}（{n} 个文件）")
        return E_OK

    if args.list or not 1 <= args.only <= len(STEPS) or args.only == 0:
        for i, s in enumerate(STEPS, 1):
            print(f"  {i}. python {' '.join(s['cmd'])}")
            print(f"     {s['why']}")
            if s.get("pending_exit"):
                print(f"     退出码 {s['pending_exit']} 表示「未完成」：{s['pending_why']}")
        if args.list:
            return E_OK
        if args.only:
            print(f"\n[用法] --only 要在 1..{len(STEPS)} 之间", file=sys.stderr)
            return E_USAGE

    first_bad = 0
    n_fail = n_pending = 0
    for i, step in enumerate(STEPS, 1):
        if args.only and i != args.only:
            continue
        got, tail = _run(step["cmd"])
        if got == 0:
            verdict = "OK"
        elif got < 0:
            verdict = "ERROR"
            n_fail += 1
        elif step.get("pending_exit") == got:
            verdict = f"未完成（退出码 {got}：{step['pending_why']}）"
            n_pending += 1
        else:
            verdict = f"失败（退出码 {got}）"
            n_fail += 1
        print(f"[{i}/{len(STEPS)}] {' '.join(step['cmd'])}")
        print(f"        {verdict}")
        if tail:
            print(f"        输出尾部：{tail}")
        if verdict != "OK" and not first_bad:
            first_bad = i

    if not first_bad:
        print("\n[OK] 全部通过 —— 可以交出去")
        return E_OK
    print(f"\n[STOP] {n_fail} 步失败 / {n_pending} 步未完成；"
          f"卡在第 {first_bad} 步：{' '.join(STEPS[first_bad - 1]['cmd'])}")
    print("       退出码 = 卡住那一步的序号（1 起算）")
    return first_bad


if __name__ == "__main__":
    sys.exit(main())
