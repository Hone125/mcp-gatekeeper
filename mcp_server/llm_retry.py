"""带限流退避的 LLM 调用封装。

## 为什么需要单独一个模块

限流不是「偶发小概率事件」，而是**跑批量评测时的常态**：模型服务的配额通常按分钟窗口计，
一轮几十道题的评测很容易在跑到中途撞上 429，而此时前面已经花掉的时间和 token 全都白费。
所以退避重试必须是**所有调用点共用一份实现**，而不是各写各的 —— 各写各的必然会出现
「评测脚本有重试、另一个脚本没有」这种不均匀。

## 策略

    429 / 超时 / 连接错误 / 5xx   → 指数退避重试（默认 8s → 16s → 32s → 64s → 90s 封顶）
    其它 4xx（400 参数错、401 鉴权错、402 余额不足）→ **立刻抛出，不重试**

第二行是刻意的：这些错误重试一万次结果也一样，只会把一个「配置写错了」的小问题
拖成一次一小时的无效空跑。

环境变量：`LLM_RETRY`（默认 6）、`LLM_BACKOFF`（默认 8）。
"""
from __future__ import annotations

import os
import time

MAX_WAIT = 90.0


def retry_times() -> int:
    return int(os.environ.get("LLM_RETRY") or "6")


def backoff_base() -> float:
    return float(os.environ.get("LLM_BACKOFF") or "8")


def _sleep(seconds: float) -> None:
    """单独抽出来是为了测试时能打桩，不必真的睡 8 秒。"""
    time.sleep(seconds)


def chat(cli, model: str, messages: list[dict], max_tokens: int = 400,
         temperature: float = 0, label: str = ""):
    """发一次 chat 调用。返回 `(content, usage)`；`content` 已 strip，可能为空串。

    重试耗尽后抛**最后一个**异常，而不是返回 None —— 静默返回空串会让上游
    把「调用失败」误当成「模型回答了空内容」，那是更难查的一类 bug。
    """
    from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    n = retry_times()
    last: Exception | None = None
    for i in range(n + 1):
        try:
            r = cli.chat.completions.create(model=model, temperature=temperature,
                                            max_tokens=max_tokens, messages=messages)
            return (r.choices[0].message.content or "").strip(), r.usage
        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            last = e
        except APIStatusError as e:
            if e.status_code < 500:
                raise
            last = e
        if i >= n:
            break
        wait = min(backoff_base() * (2 ** i), MAX_WAIT)
        print(f"    [限流/抖动{(' ' + label) if label else ''}] {type(last).__name__}"
              f" → {wait:.0f}s 后重试（第 {i + 1}/{n} 次）", flush=True)
        _sleep(wait)
    assert last is not None
    raise last
