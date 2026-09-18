# MCP 协议层冒烟测试（实测结果）

本文件由 `experiments/08_mcp_smoke.py` 生成，**不要手改**；重跑该脚本即可复现。

## 口径

- 本实验**真的把服务端当子进程拉起来**，通过 stdio 用 MCP 协议通信 ——不是 `import` 之后直接调函数。前面几节的实验证明了「函数是对的」，这一节证明「跨进程还能用」。
- 命令：`python -B -X utf8 -m mcp_server.server`，工作目录为仓库根。
- 超时：会话级读超时 `read_timeout_seconds=20s`，每个动作外面再套 `asyncio.wait_for(30s)`。两层保护范围不重合，所以两层都挂。
- 不联网、不调用模型。是否已构建 `data/` 下的产物不影响本实验的通过与否。
- 运行方式：`python experiments/08_mcp_smoke.py`（退出码 0 = 全过）

## 总览

| 项 | 通过 / 总数 |
|---|---|
| 会话内检查 | 12 / 12 |
| 超时与失败路径 | 3 / 3 |
| **合计** | **15 / 15** |

## 一、会话内检查

| # | 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|---|
| 1 | 握手成功，拿到服务端标识 | server_info.name == 'guarded-toolkit' | 'guarded-toolkit'，协议版本 '2025-11-25' | ✅ |
| 2 | 线上工具数与 TOOL_SCOPES 一致 | 6 个 | 6 个：ask_database, get_passage, get_schema, list_tables, run_sql, search_passages | ✅ |
| 3 | 线上工具数与本地注册表一致 | 6 个 | 6 个 | ✅ |
| 4 | **每个**工具的入参 schema 都把 token 列为必填 | 6 / 6 个工具要求 token | 全部符合 | ✅ |
| 5 | **每个**工具都有描述 | 6 / 6 | 6 / 6 | ✅ |
| 6 | 无令牌调用：协议层不算错误，返回体里标记拒绝 | is_error=False 且 denied=True 且 code=NO_TOKEN | is_error=False，denied=True，code=NO_TOKEN | ✅ |
| 7 | 伪造签名的令牌被拒 | code=BAD_SIGNATURE | code=BAD_SIGNATURE | ✅ |
| 8 | 令牌有效但 scope 不足，被工具级 scope 拒掉 | code=INSUFFICIENT_SCOPE | code=INSUFFICIENT_SCOPE | ✅ |
| 9 | 被拒时返回体里没有查询结果 | 不含 rows / sql 字段 | 不含 | ✅ |
| 10 | 令牌齐全时放行（★ 反证：不是一刀切全拒） | denied 不为 True | denied=None，业务层 code=ok | ✅ |
| 11 | 参数没传够：这次是**协议错误** | is_error=True | is_error=True | ✅ |
| 12 | 调用不存在的工具 | is_error=True，或客户端直接抛错 | is_error=True，文本 'Unknown tool: no_such_tool' | ✅ |

## 二、超时与失败路径

这一节的用意是：**只声明「我们设了超时」而不跑一次超时，等于没设。** 下面用两个假服务端把两条失败路径真的走一遍 —— 一个「连得上但永不回话」，一个「根本起不来」。

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 服务端 stdout 里一行人话都没有 | stdout 全是 JSON-RPC；启动信息出现在 stderr | stdout 0 行，其中 0 行不是 JSON；stderr 里有启动信息 | ✅ |
| 服务端启动成功但永不回应时，客户端不会挂死 | 等到声明的硬超时（5s）之后、15s 之内失败并给出原因 | 等到声明的硬超时之后才失败：WouldBlock:  | ✅ |
| 服务端根本起不来时，客户端给出可读原因 | 立刻报错，且原因指向子进程退出 | 没等到硬超时（30s）就失败了：MCPError: Connection closed | ✅ |

## 三、每个工具在线上暴露的入参

下面这张表是**客户端从协议里真实收到的 `input_schema`**，不是照着函数签名抄的。

客户端是照着 schema 决定传不传令牌的。schema 里不把 `token` 列为必填，就等于在**接口契约**里没提鉴权这回事 —— 函数体里检查得再严，从契约角度看也是「这个工具不需要身份」。

| 工具 | required（必填） | properties（全部入参） | 需要的 scope |
|---|---|---|---|
| `ask_database` | `question`、`token` | `question`、`token`、`version` | `db:query` |
| `get_passage` | `doc_id`、`token` | `doc_id`、`length`、`offset`、`token` | `kb:read` |
| `get_schema` | `token` | `table`、`token` | `db:read` |
| `list_tables` | `token` | `token` | `db:read` |
| `run_sql` | `sql`、`token` | `sql`、`token` | `db:query` |
| `search_passages` | `query`、`token` | `query`、`token`、`top_k` | `kb:read` |

## 四、值得记下来的实测行为

- **鉴权失败不是协议错误**：令牌缺失/不对/scope 不足都返回 `is_error=False` 加一个 `denied: True` 的正常结果。做成协议错误的话，客户端会把它读成「工具调用失败」而去重试，而不是「我没有权限」。缺必填参数才是 `is_error=True`。
- **`struct` 与文本都给**：工具返回的是结构化对象，SDK 同时把它序列化进 `content`。本实验读的是 `content[0].text` 并自己 `json.loads`，这样即使将来 SDK 改变结构化通道的形态，断言也不受影响。
- **任何一个失败都会被包成嵌套的 `ExceptionGroup`**：`stdio_client` 把所有异常收进 anyio 的任务组，`repr()` 出来是十几层 `+-+`。本脚本用 `root_cause()` 剥到最里层再记进报告 —— 否则报告里出现的是一屏缩进，真正的原因（比如「模块名拼错了」）反而看不见。
