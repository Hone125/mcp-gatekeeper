"""用量账本：把每次 LLM 调用的 token 用量逐条追加落盘，再汇总成报告。

## 三条硬约束

**1. 绝不抛异常。** 这是**旁路记账** —— 服务端正在回答用户问题时，记账写不进去
不该让回答失败。整个 `record()` 包在 try 里，失败就静默放过。

**2. 只追加，不改主流程的返回值。** 记账是副作用，不参与业务逻辑。

**3. 金额允许为空，但 token 必须准。**

第 3 条值得展开。一条用量记录的字段里，**调用次数和 token 数是实测的**，
而**金额是算出来的**。算金额需要单价，单价需要一个可引用的出处（官方价目页 + 抓取日期）。
抓不到出处时，唯一诚实的做法是**留空并写明原因**，而不是：

- 拿别家同尺寸模型的单价顶上 —— 那是编造；
- 填 0 —— 看起来像「免费」，同样误导；
- 悄悄省略这一行 —— 读者会以为整个账本就是这些。

对应到代码：`money()` 在单价不全时返回 `(None, 原因)`，`None` 在报告里渲染成「—」。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from mcp_server import paths

LOCK = threading.Lock()

ENV_PRICES = "MCP_TOOLKIT_PRICES"


def ledger_path():
    return paths.reports_dir() / "usage_ledger.jsonl"


def prices_path():
    return paths.reports_dir() / "prices.json"


# ---------------------------------------------------------------- 单价表
# 每条单价的形状：
#   "<model id>": {"in": <每百万输入 token 的价格>, "out": <每百万输出 token 的价格>,
#                  "currency": "CNY", "unit": "per_1M_tokens",
#                  "source": "<价目页 URL>", "as_of": "<抓取日期>",
#                  "note": "<可选说明>"}
#
# **只有 source 与 as_of 都填了的条目才会被用于算金额。** 缺任一字段视为「无出处」，
# 金额留空。这条规则在 load_prices() 里强制，不靠自觉。
#
# 本仓库默认不带任何单价：价目随时会变，把某个日期的数字固化进源码，
# 比留空更容易误导人。想算金额的话，用 `MCP_TOOLKIT_PRICES` 指向一个 JSON 文件，
# 里面每个条目必须带 `source` 与 `as_of`（缺一个就报错，见 load_prices）。
#
# ★ 这里**刻意不写自动抓价**。抓一个价目页看着聪明，实际上：
# 页面结构随时会变、抓到的数字没人复核、而“用哪个模型”在写代码时还不知道。
# 一条带出处的、需要人填的单价，比一条自动抓来但没人看过的数字可信。
BUILTIN_PRICES: dict[str, dict] = {}


class PriceError(ValueError):
    pass


def load_prices() -> dict:
    """内置单价 + 环境变量覆盖。覆盖项的 `source` 缺失时直接报错。"""
    p = {k: dict(v) for k, v in BUILTIN_PRICES.items()}
    override = os.environ.get(ENV_PRICES)
    if override:
        with open(override, encoding="utf-8") as f:
            extra = json.load(f)
        for model, row in extra.items():
            if not row.get("source"):
                raise PriceError(f"单价覆盖项 {model!r} 缺 source —— 没有出处的单价不许用")
            p[model] = row
    return p


def record(source: str, model: str, usage, **extra) -> None:
    """把一次调用的 `usage` 追加进账本。**绝不抛异常、绝不阻塞主流程。**"""
    try:
        if usage is None:
            return
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        if pt is None and isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if pt is None and ct is None:
            return
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "model": model,
            "prompt_tokens": int(pt or 0),
            "completion_tokens": int(ct or 0),
        }
        rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with LOCK:
            # 一次 write 写入整行：并发时短行追加在多数平台上是原子的。
            # 进程间并发没有加锁 —— 这个账本是单机评测用的，不是计费系统。
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001 —— 记账失败绝不许冒泡到主链路
        pass


def read_ledger() -> list[dict]:
    """读全部账本行。文件不存在 → 空列表（**这是正常状态，不是错误**）。
    坏行跳过而不是崩掉：账本被中断的进程写坏一行，不该让整个报告生成不出来。"""
    path = ledger_path()
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def ledger_summary() -> list[dict]:
    """按 `(source, model)` 汇总：调用次数 + 两个方向的 token 数。按次数降序。"""
    agg: dict[tuple, dict] = {}
    for r in read_ledger():
        k = (r.get("source"), r.get("model"))
        d = agg.setdefault(k, {"src": k[0], "model": k[1], "calls": 0, "tok_in": 0, "tok_out": 0})
        d["calls"] += 1
        d["tok_in"] += int(r.get("prompt_tokens") or 0)
        d["tok_out"] += int(r.get("completion_tokens") or 0)
    return sorted(agg.values(), key=lambda x: (-x["calls"], str(x["src"])))


def money(model: str, tok_in: int, tok_out: int, prices: dict) -> tuple[float | None, str]:
    """算金额。返回 `(金额或 None, 原因)`。

    单价缺 `in`/`out`/`source`/`as_of` 任一项 → 返回 `(None, 原因)`。
    **绝不返回 0，绝不用别家单价兜底。**
    """
    row = prices.get(model)
    if not row:
        return None, "无单价条目"
    if row.get("in") is None or row.get("out") is None:
        return None, "单价不全"
    if not row.get("source") or not row.get("as_of"):
        return None, "缺价目出处（source / as_of）"
    amount = tok_in / 1_000_000 * float(row["in"]) + tok_out / 1_000_000 * float(row["out"])
    return amount, f"{row.get('currency', 'CNY')} @ {row['as_of']}"
