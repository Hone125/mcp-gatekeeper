"""读仓库根目录的 `.env`。

## 为什么单独一个模块

`.env.example` 第一行就写着「把这个文件复制成 .env 再填值」，
`requirements.txt` 也声明了 `python-dotenv` 并注明「只在需要 LLM 的链路上用到」——
但在这之前**没有任何一处代码真的读过 `.env`**。也就是说：照文档做的人会填一个
谁也不看的文件，然后拿到「缺少环境变量 LLM_API_KEY」，再回头怀疑是自己填错了。

这类坑的坏处不在于修起来难（就是下面这十几行），而在于**它把责任推给了照做的人**。

## 三条规矩

1. **不覆盖进程里已有的环境变量。** 显式 `export LLM_API_KEY=...` 永远赢过 `.env`，
   否则「临时换个 key 试一下」会静默失效 —— 那种失败最难查。
2. **文件不存在就返回空。** `.env` 是被 `.gitignore` 排除的，clone 下来本来就没有它。
3. **只读不写、不抛异常。** 这是启动路径上的一步，它不许把程序弄崩。

## 覆盖范围（刻意只覆盖 LLM 那条链路）

真正会调它的只有 `t2sql_core.real_llm()` —— 和 `requirements.txt` 里那句注释一致。
`MCP_JWT_SECRET` / `MCP_TOOLKIT_DATA_ROOT` 这些键请用真实环境变量设置，
它们不走 `.env`。理由：那些键影响的是「鉴权」和「读哪个目录」，
凭据文件能改它们反而是件危险的事（一条 `.env` 就能把数据根指到别处）。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def read(path: Path | None = None) -> dict[str, str]:
    """解析 `.env`，返回键值对。**只解析，不碰进程环境。**

    解析交给 `python-dotenv`（已声明依赖）：引号、转义、`export` 前缀、`#` 注释
    这些都是它踩过的坑，自己写一个正则版本只会在某一行上悄悄解析错。
    没装这个包时返回空字典 —— 环境变量可能本来就是用别的方式设好的，
    不该因为少一个可选依赖就让整条链路起不来。
    """
    p = Path(path) if path is not None else ENV_PATH
    if not p.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    try:
        values = dotenv_values(p)
    except Exception:  # noqa: BLE001 —— 解析失败不该让程序起不来
        return {}
    return {k: v for k, v in values.items() if k and v is not None and v != ""}


def apply(path: Path | None = None) -> dict[str, str]:
    """把 `.env` 里的键补进 `os.environ`，返回**实际补进去的**那些键。

    返回值只含真正生效的键：`.env` 里有、但进程环境里已经有同名变量的，
    不算补进去（它输给了显式设置）。这样调用方和测试能直接断言「谁赢了」。
    """
    applied: dict[str, str] = {}
    for k, v in read(path).items():
        if k not in os.environ:
            os.environ[k] = v
            applied[k] = v
    return applied
