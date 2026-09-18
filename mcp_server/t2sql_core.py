"""把自然语言问题翻成 SQL 的有界重写状态机。

## 状态机长什么样

    取结构 ──→ 生成 SQL ──→ 护栏校验 ──→ 执行 ──→ 成功，结束
                  ↑                            │
                  └──── 把失败原因回灌 ─────────┘
                        （最多回灌 2 次，共 3 次执行机会）

「有界」是重点。一个没有次数上限的自我修复循环，在模型反复犯同一个错的时候
会一直烧 token 直到把配额烧完 —— 而它并不比三次之后就放弃更有可能成功。
`MAX_REPAIR = 2` 是硬上限，写在模块级常量里，不是某处的魔数。

## 为什么把 LLM 调用做成可注入的

`llm` 参数默认指向真实的模型调用，但**可以替换**。这不是为了「方便测试」这种泛泛的理由，
而是因为它把两件事切开了：

- **控制流**（重写几次、失败怎么回灌、耗尽后返回什么）—— 纯逻辑，不依赖模型，
  可以用一个假的模型驱动，被测得清清楚楚；
- **SQL 写得好不好** —— 只有真实模型能回答，那是评测要测的东西。

不切开的话，想验证「第一次被护栏拦下后会不会重写」就得真的调一次模型 ——
一条本可以 5 毫秒跑完的单元测试，变成一条要联网、要花钱、还会因为限流而随机失败的测试。
`tests/test_t2sql_state_machine.py` 用假模型把控制流全测了。

## 三个提示词版本

`v1` 自由投影 → `v2` 追加最小投影约束 → `v3` 追加 UNSUPPORTED 自救与列名指引。
三版都保留、都要跑、都进报告。`v1` 天然是投影过度的那一版，**删掉它报告就只剩一半事实**。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from mcp_server import cost, db_tools, env_file, guardrails, llm_retry

# 最多回灌几次。总执行机会 = MAX_REPAIR + 1。
MAX_REPAIR = 2
MAX_ATTEMPTS = MAX_REPAIR + 1

DEFAULT_VERSION = "v3"
VERSIONS = ("v1", "v2", "v3")

# 模型说「这个库答不了」时用的标记。放在 SQL 的第一行，形如：
#     -- UNSUPPORTED 库里没有记录作曲家的表
UNSUPPORTED_MARK = "-- UNSUPPORTED"

SYSTEM = (
    "你是一个把自然语言问题翻译成 SQLite 查询的助手。\n"
    "规则：\n"
    "1. 只输出一条 SELECT 或 WITH 查询，不要输出解释、不要输出多条语句。\n"
    "2. 只能使用下面给出的表和列，列名必须逐字一致。\n"
    "3. 不要访问 sqlite_master 等内部表。\n"
    "4. 查询会自动补上 LIMIT，你不必自己加；如果加了，不要超过 200。\n"
)

# 每版只差最后一段。这样三版的差异是**可见的、可 diff 的**，
# 而不是三份各自演化、看不出差别的提示词。
_SUFFIX = {
    "v1": "请写出能回答上述问题的 SQL。",
    "v2": (
        "请写出能回答上述问题的 SQL。\n"
        "额外要求：只 SELECT 问题里真正需要的那几列，不要用 SELECT *。"
    ),
    "v3": (
        "请写出能回答上述问题的 SQL。\n"
        "额外要求：\n"
        "- 只 SELECT 问题里真正需要的那几列，不要用 SELECT *。\n"
        "- 列名必须与上面给出的表结构逐字一致，不要凭猜测造列名。\n"
        "- 如果这个问题用给定的表结构**根本无法回答**，那么第一行输出 "
        f"`{UNSUPPORTED_MARK} <缺少什么>`，不要硬写一个看起来像答案的查询。"
    ),
}


def prompt_for(version: str) -> str:
    if version not in _SUFFIX:
        raise ValueError(f"未知的提示词版本 {version!r}，可选：{list(_SUFFIX)}")
    return _SUFFIX[version]


def build_messages(question: str, schema: str, version: str) -> list[dict]:
    """拼出第一轮的对话。结构放在用户消息里，因为它是**每次都可能变**的上下文
    （换一份数据就换一段结构），而系统提示词描述的是不变的规则。"""
    user = (
        f"数据库结构：\n\n{schema}\n\n"
        f"问题：{question}\n\n"
        f"{prompt_for(version)}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- 从回复里取 SQL
_RE_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)

# 「这看起来是一条语句的开头」。
#
# ★ 这里**故意不限于 SELECT/WITH**。早先只认 SELECT/WITH，结果是模型回一句
# `DROP TABLE artist` 时被判成 `kind="empty"`（「模型说了句废话」），
# 护栏**根本没机会看到它**，回灌给模型的也变成了「请只输出 SELECT 或 WITH」——
# 而模型真正犯的错是「你写了个写操作」，它一个字都没被告知。
#
# 直接后果有两个，都是坏的：
#   1. `R4_FORBIDDEN_KEYWORD` 这条规则在状态机这条路径上**永远走不到**，
#      拦下它的活儿实际上只有护栏的单元测试在做；
#   2. 模型收到的是误导性的提示，于是下一轮八成还是错的方向。
#
# 改成「认得出来的语句开头就送交护栏」之后，判定权回到护栏手里 ——
# 那本来就是它该干的事。extract_sql 只负责**取出来**，不负责判合不合法。
_RE_STMT = re.compile(
    r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|PRAGMA"
    r"|ATTACH|DETACH|VACUUM|REINDEX|ANALYZE|EXPLAIN|VALUES|TRUNCATE|GRANT|REVOKE)\b",
    re.I)


def extract_sql(reply: str) -> dict:
    """从模型回复里取出 SQL。返回 `{"kind": ..., "sql": ..., "reason": ...}`。

    `kind` 三种取值：

    - `"sql"` —— 取到了一条语句。**取到了不代表它合法**：是不是只读、
      有没有越权，由护栏判（`guardrails.run_query`），这里不做判断。
    - `"unsupported"` —— 模型明确说这个库答不了（v3 才引导它这么做）；
    - `"empty"` —— 回复里连一条语句都找不出来。

    后两种**必须分开**。它们在上游是完全不同的两件事：
    「模型说答不了」是一个有效结论，可以计入分母；
    「模型输出了废话」是一次失败，得单独报。混成一个「没拿到 SQL」会掩盖真实分布。
    """
    text = (reply or "").strip()
    if not text:
        return {"kind": "empty", "sql": "", "reason": "模型返回了空内容"}

    if UNSUPPORTED_MARK in text:
        # 标记可能在围栏里也可能在外面，取它所在的那一行
        for line in text.splitlines():
            if UNSUPPORTED_MARK in line:
                return {"kind": "unsupported", "sql": "",
                        "reason": line.split(UNSUPPORTED_MARK, 1)[1].strip() or "未说明原因"}
        return {"kind": "unsupported", "sql": "", "reason": "未说明原因"}

    # 优先取代码围栏里的内容：模型经常在围栏外写一段解释
    m = _RE_FENCE.search(text)
    candidates = [m.group(1)] if m else []
    # 再退化到「第一个以 SELECT/WITH 开头的行，一直到结尾」
    for i, line in enumerate(text.splitlines()):
        if _RE_STMT.match(line):
            candidates.append("\n".join(text.splitlines()[i:]))
            break

    for cand in candidates:
        sql = cand.strip()
        # 去掉可能被一起框进来的注释行与空行，只留语句本身
        lines = [ln for ln in sql.splitlines()
                 if ln.strip() and not ln.strip().startswith("--")]
        sql = "\n".join(lines).strip()
        if sql and _RE_STMT.match(sql):
            return {"kind": "sql", "sql": sql, "reason": ""}

    return {"kind": "empty", "sql": "",
            "reason": f"回复里没有以 SELECT/WITH 开头的语句：{text[:80]!r}"}


# ---------------------------------------------------------------- 失败回灌
def feedback_for(attempt: dict) -> str:
    """把一次失败翻译成给模型看的改写指令。

    三种失败的**改法不一样**，所以回灌的话也必须不一样：

    - 被静态校验拦下（`rule` 有值）：写法问题，要换一种写法；
    - 被引擎拦下（`R8`）：动作被禁，要换一种表达，不是改语法；
    - 执行报错（`error` 有值）：语义问题（列名写错、表不存在），要对照结构改。

    统一回一句「请重写」的话，模型只能瞎猜哪里错了。
    """
    if attempt.get("blocked"):
        if attempt.get("rule") == guardrails.R_ENGINE_DENY:
            return (f"这条 SQL 被数据库引擎级安全回调拒绝：{attempt.get('reason', '')}\n"
                    f"注意：不要使用 pragma_* 这类表值函数，也不要访问 sqlite_ 开头的内部表。"
                    f"请换一种写法，只查询上面给出的表。")
        return (f"这条 SQL 没有通过静态安全校验：{attempt.get('rule', '')} —— "
                f"{attempt.get('reason', '')}\n请改写，仍然只使用上面给出的表和列。")
    if attempt.get("error"):
        return (f"这条 SQL 执行失败了：{attempt['error']}\n"
                f"请对照上面的表结构检查表名和列名是否逐字一致，然后修正。")
    return "请重写这条 SQL。"


# ---------------------------------------------------------------- 真实模型
def real_llm(messages: list[dict], label: str = "") -> dict:
    """默认的模型调用。需要 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`。

    `.env` 就在**这里**读（而不是在各脚本的 `main()` 里）：这样一处代码同时覆盖
    「命令行脚本」和「长期运行的 MCP 服务端」两种入口，谁都不会漏。
    显式设过的环境变量优先，见 `env_file.apply()`。
    """
    from openai import OpenAI

    env_file.apply()

    key = os.environ.get("LLM_API_KEY") or ""
    base = os.environ.get("LLM_BASE_URL") or ""
    model = os.environ.get("LLM_MODEL") or ""
    missing = [k for k, v in (("LLM_API_KEY", key), ("LLM_BASE_URL", base),
                              ("LLM_MODEL", model)) if not v]
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}（见 .env.example）")

    cli = OpenAI(api_key=key, base_url=base, timeout=float(os.environ.get("LLM_TIMEOUT") or 120))
    text, usage = llm_retry.chat(cli, model, messages, max_tokens=400, temperature=0, label=label)
    return {"text": text, "model": model, "usage": usage}


# ---------------------------------------------------------------- 状态机
def run(question: str, version: str = DEFAULT_VERSION, db_path: Path | None = None,
        llm=None, record_cost: bool = True) -> dict:
    """走完整条链路。**不抛异常**（模型调用本身的异常除外 —— 那要冒泡给调用方决定）。

    返回值里 `history` 是**逐次尝试的完整现场**。耗尽后不返回一个干净的
    `EXHAUSTED` 错误码，而是把三次各自的 SQL、拦截规则、报错原文一起带回来：
    这些失败样本是台账里最值钱的部分，丢掉就没了。
    """
    llm = llm or real_llm
    schema = db_tools.schema_doc(db_path)
    messages = build_messages(question, schema, version)
    history: list[dict] = []
    usage_tot = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    last: dict = {}

    for i in range(MAX_ATTEMPTS):
        out = llm(messages, f"{version} #{i + 1}")
        text = out.get("text", "")
        usage_tot["calls"] += 1
        u = out.get("usage")
        if u is not None:
            usage_tot["prompt_tokens"] += int(
                getattr(u, "prompt_tokens", None) or (u.get("prompt_tokens", 0) if isinstance(u, dict) else 0) or 0)
            usage_tot["completion_tokens"] += int(
                getattr(u, "completion_tokens", None) or (u.get("completion_tokens", 0) if isinstance(u, dict) else 0) or 0)
        if record_cost:
            cost.record(source=f"t2sql:{version}", model=out.get("model", ""), usage=u,
                        question=question, attempt=i + 1)

        ex = extract_sql(text)
        attempt = {"attempt": i + 1, "kind": ex["kind"], "sql": ex["sql"],
                   "reply_head": text[:200]}

        if ex["kind"] == "unsupported":
            attempt.update(ok=False, blocked=False, error="", rule="", reason=ex["reason"])
            history.append(attempt)
            return _result(question, version, "unsupported", history, usage_tot,
                           error=ex["reason"])

        if ex["kind"] == "empty":
            attempt.update(ok=False, blocked=False, error=ex["reason"], rule="", reason="")
            history.append(attempt)
            last = attempt
            messages = messages + [
                {"role": "assistant", "content": text[:400]},
                {"role": "user", "content":
                    "你的回复里没有可执行的 SQL。请只输出一条 SELECT 或 WITH 语句，"
                    "不要写解释文字。"},
            ]
            continue

        qr = guardrails.run_query(ex["sql"], db_path)
        attempt.update(ok=qr.ok, blocked=qr.blocked, rule=qr.rule, reason=qr.reason,
                       error=qr.error, limit_injected=qr.limit_injected,
                       n_rows=qr.n_rows, truncated=qr.truncated)
        history.append(attempt)
        last = attempt

        if qr.ok:
            return _result(question, version, "ok", history, usage_tot,
                           columns=qr.columns, rows=qr.rows, n_rows=qr.n_rows,
                           truncated=qr.truncated, limit_injected=qr.limit_injected,
                           sql=qr.sql)

        messages = messages + [
            {"role": "assistant", "content": ex["sql"]},
            {"role": "user", "content": feedback_for(attempt)},
        ]

    status = "blocked" if last.get("blocked") else "failed"
    return _result(question, version, status, history, usage_tot,
                   sql=last.get("sql", ""),
                   error=last.get("error") or last.get("reason") or "重写次数用尽")


def _result(question: str, version: str, status: str, history: list[dict],
            usage: dict, **extra) -> dict:
    out = {
        "ok": status == "ok",
        "status": status,
        "question": question,
        "version": version,
        "attempts": len(history),
        "max_attempts": MAX_ATTEMPTS,
        "history": history,
        "usage": usage,
        "sql": "",
        "columns": [],
        "rows": [],
        "n_rows": 0,
        "truncated": False,
        "limit_injected": False,
        "error": "",
    }
    out.update(extra)
    return out
