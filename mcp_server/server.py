"""MCP 服务端：注册 6 个工具，每个工具在入口做一次鉴权。

## 鉴权做在**工具入口**，不做在传输层

这是这套东西里最主要的一个设计选择。做法是：每个工具函数的**第一个动作**是

    d = _deny("工具名", token)
    return d or <真正干活>

也就是说，**没有令牌就走不到业务代码**，而不是「先执行、再判断结果能不能返回」。
差别在于信息泄露面：后者即使拒绝了，SQL 也已经跑了、数据也已经读了，
拒绝只是没把结果给出去；前者的业务代码根本没有被执行的机会。

## ⚠️ stdout 是协议通道，人话一律走 stderr

这个坑踩过一次，代价是一次完整的调试：启动时打印的「已注册 N 个工具」原本走
`print()`（也就是 stdout），手动 `python -m mcp_server.server` 看着一切正常。
但 stdio 传输下 **stdout 就是 JSON-RPC 的通道** —— 客户端收到一行
`已注册 6 个工具：...`，解析器直接抛 `Invalid JSON`，整个会话崩掉。

所以这里的规矩是：**只有 `--check` 这个给人看的模式才往 stdout 写**；
真正的服务端模式下，所有人类可读的输出一律走 stderr。
`experiments/08_mcp_smoke.py` 会把这条钉住 —— 它是唯一能发现这类问题的实验，
因为直接 `import` 调函数根本不经过管道。

## 为什么要有启动自检

`TOOL_SCOPES` 里登记的工具名，和 `@mcp.tool()` 实际注册的工具名，是**两处独立维护的字符串**。
少登记一个的后果不是报错，而是**静默降级**：那个工具对任何令牌都返回
`UNKNOWN_TOOL`，看起来像「权限不够」，实际上是漏登记了 —— 这种问题能藏很久。

所以启动时断言「实际注册的工具名集合 == `TOOL_SCOPES` 的键集合」，不一致直接退出。
`mcp.list_tools()` 读的是**框架的真实注册表**，不是我再抄一遍的清单。

退出码：0 = 通过；1 = 自检不通过；2 = 数据没准备好。
"""
from __future__ import annotations

import asyncio
import sys

from mcp.server.mcpserver import MCPServer

from mcp_server import auth, db_tools, guardrails, kb_tools, t2sql_core

mcp = MCPServer(
    name="guarded-toolkit",
    instructions=(
        "一个带工具级鉴权的示例服务。每个工具都需要一个 JWT 令牌，"
        "并且只认它自己那个 scope。工具分三组：看库结构（db:read）、"
        "查数据（db:query）、查文本（kb:read）。"
    ),
)


def _deny(tool: str, token: str | None) -> dict | None:
    """鉴权。放行返回 `None`，拒绝返回一个**带 denied 标记**的字典。

    返回值带 `denied: True` 是刻意的：它让「被拒绝」和「查了但没有结果」
    在返回体层面就分得开。少了这个标记，调用方只能靠「ok 是不是 False」去猜，
    而空结果集和拒绝长得一模一样。
    """
    ok, info = auth.guard(tool, token)
    return None if ok else {"ok": False, "denied": True, "tool": tool, **info}


# ---------------------------------------------------------------- db:read
@mcp.tool()
def list_tables(token: str) -> dict:
    """列出数据库里所有的表以及每张表的行数。用来先摸清楚有哪些数据可用。

    Args:
        token: 访问令牌，需要 db:read 权限
    """
    return _deny("list_tables", token) or db_tools.list_tables()


@mcp.tool()
def get_schema(token: str, table: str = "") -> dict:
    """查看表结构：每张表有哪些列、什么类型、主键和外键是什么。

    不传 table 就返回所有表的结构。列名必须与这里给出的**逐字一致**，
    写 SQL 之前应该先看一眼。

    Args:
        token: 访问令牌，需要 db:read 权限
        table: 表名；留空表示返回全部表
    """
    return _deny("get_schema", token) or db_tools.get_schema(table or None)


# ---------------------------------------------------------------- db:query
@mcp.tool()
def run_sql(sql: str, token: str) -> dict:
    """执行一条只读查询。只接受单条 SELECT / WITH 语句。

    没有写 LIMIT 会自动补一个；显式写了但超过 200 会被拒绝。
    被安全规则拦下时返回体里 `blocked` 为真且带 `rule` 编号，
    与「SQL 本身写错了」（`ok` 为假但 `blocked` 为假）是两种不同的失败。

    Args:
        sql: 一条 SELECT 或 WITH 查询
        token: 访问令牌，需要 db:query 权限
    """
    return _deny("run_sql", token) or guardrails.run_query(sql).as_dict()


@mcp.tool()
def ask_database(question: str, token: str, version: str = "v3") -> dict:
    """用自然语言提问，由模型翻译成 SQL 后执行。失败会自动改写重试。

    最多尝试 3 次（1 次初写 + 2 次按失败原因改写）。全部失败时返回体里
    带完整的 `history`，逐步记着每次的 SQL 和被拒/报错的原因。

    Args:
        question: 自然语言问题，例如「一共有多少张专辑」
        token: 访问令牌，需要 db:query 权限
        version: 提示词版本，可选 v1 / v2 / v3，默认 v3
    """
    return _deny("ask_database", token) or t2sql_core.run(question, version=version)


# ---------------------------------------------------------------- kb:read
@mcp.tool()
def search_passages(query: str, token: str, top_k: int = 5) -> dict:
    """在文本语料里检索相关段落。返回的每一段都带它在原文里的字符位置。

    Args:
        query: 检索词或一句话
        token: 访问令牌，需要 kb:read 权限
        top_k: 最多返回几段，默认 5，上限 20
    """
    return _deny("search_passages", token) or kb_tools.search_passages(query, top_k)


@mcp.tool()
def get_passage(doc_id: str, token: str, offset: int = 0, length: int = 800) -> dict:
    """按编号和字符位置取原文片段。位置来自 search_passages 返回的 char_start。

    Args:
        doc_id: 文档编号，来自 search_passages 的结果
        token: 访问令牌，需要 kb:read 权限
        offset: 起始字符位置，默认 0
        length: 取多少个字符，默认 800，上限 4000
    """
    return _deny("get_passage", token) or kb_tools.get_passage(doc_id, offset, length)


# ---------------------------------------------------------------- 启动自检
async def registered_tool_names_async() -> list[str]:
    """读框架的**真实注册表**。协程版 —— 已经在事件循环里的时候用这个。

    为什么要有两个版本：`mcp.list_tools()` 是协程，同步调用必须借 `asyncio.run()`
    起一个循环；而 `asyncio.run()` **不能在已有循环内部调用**。冒烟测试是一路
    async 走下来的，如果只有同步版，它一调就会拿到
    `RuntimeError: asyncio.run() cannot be called from a running event loop`。
    """
    return sorted(t.name for t in await mcp.list_tools())


def registered_tool_names() -> list[str]:
    """同步版。**不能**在事件循环内部调用 —— 那种情况请用协程版。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(registered_tool_names_async())
    raise RuntimeError(
        "registered_tool_names() 不能在事件循环内部调用（asyncio.run 不允许嵌套）。"
        "在 async 代码里请改用 await registered_tool_names_async()。"
    )


def selfcheck() -> list[str]:
    """返回问题清单，空列表 = 通过。"""
    problems = []
    registered = set(registered_tool_names())
    declared = set(auth.TOOL_SCOPES)

    for name in sorted(registered - declared):
        problems.append(f"工具 {name!r} 已注册但没在 TOOL_SCOPES 里登记 —— "
                        f"它会对任何令牌都返回 UNKNOWN_TOOL")
    for name in sorted(declared - registered):
        problems.append(f"TOOL_SCOPES 里登记了 {name!r} 但实际没有注册这个工具")
    for name, scope in sorted(auth.TOOL_SCOPES.items()):
        if scope not in auth.SCOPES:
            problems.append(f"工具 {name!r} 要的 scope {scope!r} 不在 SCOPES 里")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    check_only = "--check" in argv

    # `--check` 是给人看的自检模式，输出走 stdout；
    # 真正的服务端模式下 stdout 是 JSON-RPC 通道，一个字节的人话都不能进。
    out = sys.stdout if check_only else sys.stderr

    def say(msg: str = "") -> None:
        print(msg, file=out)

    warn = auth.warn_if_dev_secret()
    if warn:
        print(f"[警告] {warn}", file=sys.stderr)

    problems = selfcheck()
    names = registered_tool_names()
    say(f"已注册 {len(names)} 个工具：{', '.join(names)}")
    for name in names:
        say(f"  {name:<16} 需要 {auth.TOOL_SCOPES.get(name, '（未登记）')}")
    if problems:
        print("\n[FAIL] 启动自检未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    say("[OK] 启动自检通过：注册的工具与 TOOL_SCOPES 完全一致")

    if check_only:
        return 0

    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
