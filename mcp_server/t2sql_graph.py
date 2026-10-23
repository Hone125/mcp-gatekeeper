"""Text2SQL 有界重写状态机的 LangGraph 版 —— 与 `t2sql_core.run()` 并列的第二套实现。

## 为什么是「并列」而不是「替换」

手写版先写，并且**继续保留**：它是这条链路的行为基准，
`tests/test_t2sql_state_machine.py` 里每一条控制流断言最早都是对着它写的。
这一版**只把控制流换成图**，其余全部直接复用手写版：

| 复用的东西 | 来自 |
|---|---|
| 第一轮消息（系统规则 + 结构 + 问题 + 版本要求） | `t2sql_core.build_messages` |
| 从回复里取 SQL、分成三类 | `t2sql_core.extract_sql` |
| 三种失败各自的批注 | `t2sql_core.feedback_for` |
| 预算 | `t2sql_core.MAX_ATTEMPTS` |
| 护栏 + 执行 | `guardrails.run_query` |
| 返回体 | `t2sql_core._result` —— 直接复用而不是照抄：返回体字段只有一处定义，两版就不可能各长各的 |

于是两版的差别**只可能**出在控制流上 —— 而控制流恰好是同一批假模型测试能完整覆盖的部分。

## 图

    START → prepare → generate ──┬─ unsupported ─────────────────────→ END
                         ↑       ├─ empty ─┬─ 预算还有 → repair
                         │       │         └─ 预算用尽 ───────────────→ END
                         │       └─ sql → execute ─┬─ 成功 ──────────→ END
                         │                         ├─ 失败、预算还有 → repair
                         │                         └─ 失败、预算用尽 ─→ END
                         └──────────────────────────────────── repair

| 节点 / 路由 | 对应原 `t2sql_core.run()` |
|---|---|
| `prepare` | 第 227–228 行：取结构、拼第一轮消息 |
| `generate` | 第 234–249 行：调模型、累计用量、记账、取 SQL；外加第 251–253、257–260 行里 unsupported / empty 两种尝试的记录 |
| `execute` | 第 269–274 行：护栏 + 执行，记录这次尝试 |
| `repair` | 第 261–266 行（没给出 SQL 时的格式提醒）与第 282–285 行（按失败类型写的批注） |
| 两个路由函数 | `for` 循环本身（第 233 行），以及那几处 `return` / `continue` 往哪走的判断 |
| `_to_result()` | 第 254–255、276–280、287–290 行：把最后一次尝试翻译成返回体 |

## 四个设计决定

**1. 预算由路由函数判，`recursion_limit` 只是兜底。** 路由函数看「已经调了几次模型」决定还能不能
回到 repair。`recursion_limit` 是 LangGraph 自己的「最多走多少步」，它数的是**节点执行次数**，
不是模型调用次数。拿它当预算，就得把「3 次调用」换算成一个步数（实测是 10，见 `RECURSION_LIMIT`
的注释）写进一个和业务无关的数里 ——
哪天图上多加一个节点，预算就悄悄变了，而且没有任何一处代码写着「最多调 3 次模型」。

**2. `llm` / `db_path` / `record_cost` 放在 context 里，不放进 State。** State 是节点之间流转、
挂上 checkpointer 之后**每一步都要序列化存档**的数据；函数对象存不了档 —— 实测把一个函数放进
State 再挂 `InMemorySaver`，报 `TypeError: Type is not msgpack serializable: function`。
另外这三样是「这次怎么跑」的运行参数，不是「这道题算到哪了」的进度：从存档恢复一个会话时，
该用的是恢复时传进来的模型和库，而不是存档里的旧值。

**3. `history` 用 `Annotated[list, operator.add]`，`messages` 刻意不用。** history 是「逐次追加」的现场：
节点只交「这一次的那一条」，拼接由 State 定义统一完成，没有哪个节点能因为忘了带上旧的而把前几次的
现场冲掉。messages 正相反 —— `prepare` 要把它设成一份全新的对话，`repair` 交的是「旧对话 + 两条」，
和原代码 `messages = messages + [...]` 一模一样，用默认的覆盖规则最直接。

**4. 契约照搬。** 模型**输出**再乱也不抛异常（取 SQL、护栏、执行三处本来就都不抛）；
模型**调用本身**的异常照常冒泡给调用方 —— LangGraph 把节点里的异常原样抛出 `invoke()`。
库文件不存在时，`schema_doc` 给模型一句「读不到库结构」，`run_query` 在执行那一步报出
`FileNotFoundError` 和「先跑构建脚本」，和手写版走的是同一条路。

## 和手写版的差别（如实列出）

- **最后一轮失败后不再拼批注。** 手写版在最后一轮失败后仍会把批注拼进 `messages`（第 261、282 行），
  然后循环结束 —— 那两条消息不会再发给模型，也不进返回体。这里预算用尽时直接走 END，不经过 repair。
  返回体、模型调用次数、每一轮发给模型的消息都不受影响（`tests/test_t2sql_state_machine.py`
  末尾的两版对照用例逐字比对这三样）。
- **账本的 `source` 标签不同**：手写版记 `t2sql:<版本>`，这里记 `t2sql_graph:<版本>`。
  同一天跑两版评测时，账本能按实现分开对账。
- **「没给出 SQL」那句格式提醒是照抄的**（见 `EMPTY_NUDGE`）：手写版把它直接写在 `run()` 里面，
  没有提成常量，而这一轮的规矩是不改手写版。改任何一边都要同步另一边 —— 忘了同步时，
  上面说的两版对照用例会红（只查子串的那些用例不会）。

## 多轮追问：`ask()` + checkpointer + thread_id

`run()` 不挂 checkpointer，每次都是一张白纸。`ask()` 用的是 `build_graph(checkpointer)` 编出来的图：
同一个 `thread_id` 的几问共享一份存档，于是第二问「那只看前 5 个」能看到第一问问了什么、最后用的哪条 SQL。

**★ 坑：带 reducer 的字段在同一个 thread 里会跨问题一直累加。** 存档里的 State 是接着用的，
`history` 的合并规则是「旧 + 新」，第二问开始时调用方传 `history: []` 也没用 —— 那只是「旧 + 空」。
实测：同一个 thread 里先问一题（1 次尝试）再问一题，不做处理时第二问的返回体 `attempts` 是 2，
`history` 里混着第一问的现场。所以字段要按寿命分两类：

| 寿命 | 字段 | 怎么处理 |
|---|---|---|
| 每问独立 | `history`、`attempt`、`usage`、`messages`、`reply`、`extracted`、`query_result` | 每问开头重置：`history` 用 `Overwrite([])` 绕过 reducer 直接清空；覆盖式字段由输入或 `prepare` 整个换掉 |
| 跨轮保留 | `turns`：之前每一问的问题、最终 SQL、结局 | 带 `operator.add`，**就是要它累加**；一问结束后由 `ask()` 用 `update_state` 追加一条 |

`prepare` 把最近 `MAX_TURNS_IN_PROMPT` 问按「问 → 最终 SQL」插在系统规则和这一问之间 ——
`turns` 本身随会话一直长，但塞进提示词的只有一个固定窗口，提示词不会越问越长。
`run()` 的 `turns` 永远是空的，发给模型的消息与加这一节之前逐字相同（两版对照用例守着）。
"""
from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

from mcp_server import cost, db_tools, guardrails
from mcp_server.t2sql_core import (DEFAULT_VERSION, MAX_ATTEMPTS, _result, build_messages,
                                   extract_sql, feedback_for, real_llm)

# 走满预算一共执行几个节点：prepare 1 个 + 每轮 generate / execute / repair 3 个，
# 最后一轮没有 repair。MAX_ATTEMPTS = 3 时是 1 + 3×3 − 1 = 9。
STEPS_AT_FULL_BUDGET = 3 * MAX_ATTEMPTS

# 兜底，不是预算（见文件头第 1 条）。正常运行永远碰不到它，只有路由写错、图转不出来时才会撞上 ——
# 那时 LangGraph 抛 GraphRecursionError，**就让它抛**：那是程序 bug，不是模型输出的问题，
# 包装成一次普通的 failed 反而把 bug 藏起来了。
#
# 取值是实测定的（langgraph 1.2.16）：走满预算的 9 个节点，`recursion_limit=9` 会抛
# GraphRecursionError，`=10` 才跑完 —— LangGraph 的步数比节点执行次数多 1。这里再多留两步余量。
# ★ 必须显式设：这个版本的默认上限是 10007 步（`langgraph/_internal/_config.py`），
# 死循环时要转上万步才停，而每三步就是一次真实的模型调用。
RECURSION_LIMIT = STEPS_AT_FULL_BUDGET + 3

# 多轮追问时，最多把最近几问塞进提示词（见文件头「多轮追问」）。
MAX_TURNS_IN_PROMPT = 5

# 原 `t2sql_core.run()` 第 264–265 行的那句格式提醒，逐字照抄（理由见文件头「差别」第 3 条）。
EMPTY_NUDGE = ("你的回复里没有可执行的 SQL。请只输出一条 SELECT 或 WITH 语句，"
               "不要写解释文字。")


# ---------------------------------------------------------------- State 与 context
class T2SQLState(TypedDict):
    """节点之间流转的数据。**只放「这道题算到哪了」**；「这次怎么跑」放 `T2SQLContext`。

    没标 reducer 的字段走默认的覆盖规则：节点交什么，这个字段就变成什么。
    """

    # 输入：run() 调 invoke 时给
    question: str
    version: str
    # 进度
    attempt: int            # 已经调了几次模型 —— 两个路由函数靠它判预算
    messages: list[dict]    # 下一次要发给模型的完整对话
    reply: str              # 最近一次模型的原话
    extracted: dict         # 最近一次 extract_sql 的结果：{kind, sql, reason}
    query_result: dict      # 最近一次 run_query 的结果；成功时 _to_result 从这里取行和列
    usage: dict             # 累计用量。generate 算好新的总数整个交上来
    # 逐次现场：追加式。节点交的是「只含这一次那一条的列表」，旧列表 + 新列表由 operator.add 完成。
    # 交 dict 而不是 list 会当场报 TypeError（list 只能和 list 相加）。
    # ★ 它的寿命是「一问」：多轮时每问开头由 ask() 用 Overwrite([]) 清空（见文件头「多轮追问」）。
    history: Annotated[list[dict], operator.add]
    # 跨轮记忆：之前每一问的 {question, sql, status}。寿命是「整个会话」，就是要它累加。
    # 只有 ask() 会写它；run() 里它永远是空列表。
    turns: Annotated[list[dict], operator.add]


@dataclass(frozen=True)
class T2SQLContext:
    """一次运行的运行参数。节点通过 `runtime.context` 读取；frozen = 造出来之后谁也改不了。"""

    llm: Callable[[list[dict], str], dict]
    db_path: Path | None = None
    record_cost: bool = True


# ---------------------------------------------------------------- 节点
def prepare(state: T2SQLState, runtime: Runtime[T2SQLContext]) -> dict:
    """原第 227–228 行：取结构、拼第一轮消息。只在入口跑一次。

    未知的提示词版本在这里由 `build_messages` 抛 ValueError —— 和手写版一样，
    发生在第一次调模型之前，而且是一个响亮的错误，不是一次 failed。

    多轮时（`turns` 非空），把最近几问按「问 → 最终 SQL」插在系统规则和这一问之间。
    结构说明仍然只在这一问的 user 消息里出现一次：之前几问只带问题和 SQL，不重复带结构。
    """
    schema = db_tools.schema_doc(runtime.context.db_path)
    first = build_messages(state["question"], schema, state["version"])
    turns = state.get("turns") or []
    if not turns:
        return {"messages": first}
    earlier: list[dict] = []
    for t in turns[-MAX_TURNS_IN_PROMPT:]:
        earlier += [{"role": "user", "content": f"（之前的问题）{t['question']}"},
                    {"role": "assistant",
                     "content": t["sql"] or f"-- 这一问没有得到可执行的 SQL（{t['status']}）"}]
    return {"messages": [first[0], *earlier, first[1]]}


def generate(state: T2SQLState, runtime: Runtime[T2SQLContext]) -> dict:
    """原第 234–249 行：调一次模型、记用量和账、取 SQL；并把**当场就有结论**的尝试记进 history。

    「当场就有结论」指 unsupported 和 empty（原第 251–253、257–260 行）：这两种不会再经过
    execute，这一次的现场在这里就完整了。kind 为 sql 的那一次要等 execute 拿到护栏和执行结果
    才完整，所以由 execute 记 —— 和手写版一样，每次尝试恰好进 history 一次。
    """
    ctx = runtime.context
    n = state["attempt"] + 1
    out = ctx.llm(state["messages"], f"{state['version']} #{n}")
    text = out.get("text", "")

    # 复制一份再改：节点只通过返回值改 State，不原地动上一步留下的对象。
    usage = dict(state["usage"])
    usage["calls"] += 1
    u = out.get("usage")
    if u is not None:
        usage["prompt_tokens"] += _tokens(u, "prompt_tokens")
        usage["completion_tokens"] += _tokens(u, "completion_tokens")
    if ctx.record_cost:
        cost.record(source=f"t2sql_graph:{state['version']}", model=out.get("model", ""),
                    usage=u, question=state["question"], attempt=n)

    ex = extract_sql(text)
    update: dict = {"attempt": n, "reply": text, "extracted": ex, "usage": usage}
    if ex["kind"] == "unsupported":
        attempt = _new_attempt(n, ex, text)
        attempt.update(ok=False, blocked=False, error="", rule="", reason=ex["reason"])
        update["history"] = [attempt]
    elif ex["kind"] == "empty":
        attempt = _new_attempt(n, ex, text)
        attempt.update(ok=False, blocked=False, error=ex["reason"], rule="", reason="")
        update["history"] = [attempt]
    return update


def execute(state: T2SQLState, runtime: Runtime[T2SQLContext]) -> dict:
    """原第 269–274 行：护栏校验并执行，把这一次尝试记进 history。

    `run_query` 自己保证不抛异常 —— 被拦、执行报错、库文件不存在都降级成返回体字段 ——
    所以模型写了什么都不会让这个节点抛。
    """
    ex = state["extracted"]
    qr = guardrails.run_query(ex["sql"], runtime.context.db_path)
    attempt = _new_attempt(state["attempt"], ex, state["reply"])
    attempt.update(ok=qr.ok, blocked=qr.blocked, rule=qr.rule, reason=qr.reason,
                   error=qr.error, limit_injected=qr.limit_injected,
                   n_rows=qr.n_rows, truncated=qr.truncated)
    return {"history": [attempt], "query_result": qr.as_dict()}


def repair(state: T2SQLState) -> dict:
    """原第 261–266 行与第 282–285 行：把这次失败回灌进对话，交给下一轮 generate。

    两种回灌里 assistant 那条放的东西不一样，照搬手写版：
    - 没给出 SQL：放模型原话的前 400 个字符 —— 它压根没写出 SQL，只能给原话；
    - 被拦 / 执行报错：放取出来的那条 SQL，批注由 `feedback_for` 按失败类型生成。

    这个节点用不到运行参数，所以签名里没有 `runtime` —— LangGraph 按参数名决定往里注入什么。
    """
    last = state["history"][-1]
    if last["kind"] == "empty":
        tail = [{"role": "assistant", "content": state["reply"][:400]},
                {"role": "user", "content": EMPTY_NUDGE}]
    else:
        tail = [{"role": "assistant", "content": last["sql"]},
                {"role": "user", "content": feedback_for(last)}]
    return {"messages": state["messages"] + tail}


def _new_attempt(n: int, ex: dict, text: str) -> dict:
    """一次尝试记录的公共前半段（原第 248–249 行）。键的顺序与手写版一致，落盘的 JSON 才逐字相同。"""
    return {"attempt": n, "kind": ex["kind"], "sql": ex["sql"], "reply_head": text[:200]}


def _tokens(u, key: str) -> int:
    """取一个 token 数。表达式与原第 239–242 行相同：usage 可能是对象也可能是字典，缺了记 0。"""
    return int(getattr(u, key, None) or (u.get(key, 0) if isinstance(u, dict) else 0) or 0)


# ---------------------------------------------------------------- 路由
# 返回值的 Literal 列出这条条件边的**全部**去向。LangGraph 读这个标注来知道边能通向哪些节点 ——
# 画图时据此画出每一个分支。`END` 的值就是字符串 "__end__"，所以标注里写的是它。
def route_after_generate(state: T2SQLState) -> Literal["execute", "repair", "__end__"]:
    kind = state["extracted"]["kind"]
    if kind == "unsupported":      # 原第 254–255 行：说答不了是有效结论，立刻结束
        return END
    if kind == "empty":            # 原第 267 行的 continue —— 但先看预算还有没有
        return "repair" if state["attempt"] < MAX_ATTEMPTS else END
    return "execute"


def route_after_execute(state: T2SQLState) -> Literal["repair", "__end__"]:
    if state["query_result"]["ok"]:  # 原第 276–280 行：成功就结束
        return END
    # 失败：预算还有就回灌重写；用尽就结束（原第 287–290 行在循环外判结局）
    return "repair" if state["attempt"] < MAX_ATTEMPTS else END


# ---------------------------------------------------------------- 组图
def build_graph(checkpointer=None):
    """组图并编译。`checkpointer` 只有多轮追问（`ask()`）才需要；`run()` 用的 `GRAPH` 不挂。"""
    g = StateGraph(T2SQLState, context_schema=T2SQLContext)
    g.add_node("prepare", prepare)
    g.add_node("generate", generate)
    g.add_node("execute", execute)
    g.add_node("repair", repair)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "generate")
    g.add_conditional_edges("generate", route_after_generate)
    g.add_conditional_edges("execute", route_after_execute)
    g.add_edge("repair", "generate")   # 这条回边就是原来的 for 循环
    return g.compile(checkpointer=checkpointer)


GRAPH = build_graph()


# ---------------------------------------------------------------- 对外入口
def run(question: str, version: str = DEFAULT_VERSION, db_path: Path | None = None,
        llm=None, record_cost: bool = True) -> dict:
    """签名、返回体、契约都与 `t2sql_core.run()` 一致；同一批控制流测试对两版各跑一遍。"""
    final = GRAPH.invoke(
        {"question": question, "version": version, "attempt": 0,
         "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}},
        context=T2SQLContext(llm=llm or real_llm, db_path=db_path, record_cost=record_cost),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    return _to_result(final)


def ask(graph, thread_id: str, question: str, version: str = DEFAULT_VERSION,
        db_path: Path | None = None, llm=None, record_cost: bool = True) -> dict:
    """同一会话里的一问。返回体与 `run()` 相同。

    `graph` 必须是 `build_graph(checkpointer)` 编出来的（挂着 checkpointer）；
    `thread_id` 相同的几问共享 `turns`，其余字段每问从头开始（见文件头「多轮追问」的那张表）。
    """
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    final = graph.invoke(
        {"question": question, "version": version, "attempt": 0,
         "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
         # ★ 绕过 operator.add、直接清空：传 [] 只会得到「旧 + 空」，上一问的现场照样留着
         "history": Overwrite([])},
        context=T2SQLContext(llm=llm or real_llm, db_path=db_path, record_cost=record_cost),
        config=config,
    )
    result = _to_result(final)
    # 这一问结束了，把它记进跨轮记忆。走 update_state 而不是在图里加节点：
    # 单轮的 run() 用的是同一张图，加节点会改掉它的形状和步数。
    graph.update_state(config, {"turns": [{"question": question, "sql": result["sql"],
                                           "status": result["status"]}]})
    return result


def _to_result(state: T2SQLState) -> dict:
    """把图跑完时的 State 翻译成和手写版一模一样的返回体。

    手写版在三处 return（第 254、277、288 行）；图里这三处都变成了「走到 END」，
    区别只在**最后一次尝试**长什么样 —— 看 history 的最后一条就能分出结局。
    """
    q, v = state["question"], state["version"]
    history, usage = state["history"], state["usage"]
    last = history[-1]
    if last["kind"] == "unsupported":
        return _result(q, v, "unsupported", history, usage, error=last["reason"])
    if last["ok"]:
        qr = state["query_result"]
        return _result(q, v, "ok", history, usage,
                       columns=qr["columns"], rows=qr["rows"], n_rows=qr["n_rows"],
                       truncated=qr["truncated"], limit_injected=qr["limit_injected"],
                       sql=qr["sql"])
    status = "blocked" if last.get("blocked") else "failed"
    return _result(q, v, status, history, usage, sql=last.get("sql", ""),
                   error=last.get("error") or last.get("reason") or "重写次数用尽")
