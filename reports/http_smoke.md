# HTTP 入口冒烟测试（实测结果）

本文件由 `experiments/30_http_smoke.py` 生成，**不要手改**；重跑该脚本即可复现。

## 口径

- **真的起进程、真的走 TCP**：`python -B -X utf8 -m mcp_server.http_api --port 0`，然后用 HTTP 客户端打它。不是进程内的 `TestClient` —— 那个绕过 `main()` 里的启动逻辑（自己 bind socket、从 stderr 报真实端口），而这段逻辑恰恰是本脚本自己要依赖的。
- **端口由内核分配**（`--port 0`），脚本从服务端 stderr 的「监听 host:port」那一行读到真实端口。端口是随机值，**不写进本报告**。
- **数据现造**：服务端子进程的 `MCP_TOOLKIT_DATA_ROOT` 指向本次运行临时目录里的数据根，内含一张两张表的小库（`artist` / `album`）与一个两篇文档的小知识库。所以本实验**不依赖 `data/` 是否已构建**，`git clone` 之后不跑任何构建脚本也能跑。
- **不联网、不调模型**。`ask_database` 必须调模型，因此它只走拒绝路径（无令牌 / scope 不足）—— 这恰好证明了拒绝发生在碰模型之前。
- **不记耗时**：报告要能逐字节复现（同一份代码跑两遍，`git status` 应当干净）。时钟读数与随机端口都会变，所以一个都不进产物。
- 运行方式：`python experiments/30_http_smoke.py`（退出码 0 = 全过）

## 总览

| 项 | 通过 / 总数 |
|---|---|
| HTTP 检查 | 19 / 19 |
| 进程边界（stdout 纯净） | 1 / 1 |
| **合计** | **20 / 20** |

## 一、HTTP 检查

| # | 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|---|
| 1 | 存活探针 GET /healthz | 200 且 ok=True | 200 且 ok=True | ✅ |
| 2 | GET /tools 的工具数与 TOOL_SCOPES 一致 | 6 个 | 6 个：list_tables, get_schema, run_sql, ask_database, search_passages, get_passage | ✅ |
| 3 | GET /tools 里每个工具的 scope 与 TOOL_SCOPES 逐条一致 | {'list_tables': 'db:read', 'get_schema': 'db:read', 'run_sql': 'db:query', 'ask_database': 'db:query', 'search_passages': 'kb:read', 'get_passage': 'kb:read'} | {'list_tables': 'db:read', 'get_schema': 'db:read', 'run_sql': 'db:query', 'ask_database': 'db:query', 'search_passages': 'kb:read', 'get_passage': 'kb:read'} | ✅ |
| 4 | OpenAPI 文档里 6 条工具路径都在（`/docs` 能点着调） | 6 条 POST /tools/<工具名> | 6 条：/tools/ask_database, /tools/get_passage, /tools/get_schema, /tools/list_tables, /tools/run_sql, /tools/search_passages | ✅ |
| 5 | 无令牌（空请求体）：403 + NO_TOKEN | 403 且 code=NO_TOKEN | 403 且 code=NO_TOKEN | ✅ |
| 6 | 伪造签名：403 + BAD_SIGNATURE | 403 且 code=BAD_SIGNATURE | 403 且 code=BAD_SIGNATURE | ✅ |
| 7 | 令牌有效但 scope 不足：403 + INSUFFICIENT_SCOPE | 403 且 code=INSUFFICIENT_SCOPE | 403 且 code=INSUFFICIENT_SCOPE | ✅ |
| 8 | 被拒时响应体里没有业务字段 | 不含 rows / sql | ['code', 'denied', 'detail', 'ok', 'tool'] | ✅ |
| 9 | 未知工具：404 + UNKNOWN_TOOL（而不是默认的 {"detail":"Not Found"}） | 404 且 code=UNKNOWN_TOOL | 404 且 code=UNKNOWN_TOOL，detail="未登记的工具 'no_such_tool'" | ✅ |
| 10 | 缺必填参数：400 + BAD_ARGUMENTS（不是 403） | 400 且 code=BAD_ARGUMENTS | 400 且 code=BAD_ARGUMENTS | ✅ |
| 11 | 请求体不是合法 JSON：400 + BAD_ARGUMENTS | 400 且 code=BAD_ARGUMENTS | 400 且 code=BAD_ARGUMENTS | ✅ |
| 12 | 令牌齐全：200 且列出真实表名（★ 反证） | 200 且表名 {album, artist} | 200 且 ok=True，表名 ['album', 'artist'] | ✅ |
| 13 | GET 结构：200 且列名与库里一致 | 200 且含 artist_id / title / year | 200 且列名 ['artist_id', 'id', 'title', 'year'] | ✅ |
| 14 | 真 SQL：200 且返回真行 | 200 且 rows=[[A],[B]] | 200 且 rows=[['A'], ['B']] | ✅ |
| 15 | ★ 被 SQL 护栏拦下：**200** 且 blocked=True | 200 且 blocked=True（业务结论，不是传输失败） | 200 且 blocked=True | ✅ |
| 16 | 护栏拦下时返回体里没有 rows（说明真没执行） | 不含 rows 或 rows 为空 | blocked=True，有 rows=False | ✅ |
| 17 | 知识库检索：200 且只命中该命中的那篇 | 200 且命中含 d2（茶經），不含 d1（兵法） | 200 且命中 ['d2'] | ✅ |
| 18 | 取原文片段：200 且正文非空 | 200 且 text 里有原文 | 200 且正文 28 字：'茶之為飲，發乎神農氏。其水，用山水上，江水中，井' | ✅ |
| 19 | ★ 需要模型的工具在 scope 不足时同样 403（证明拒绝发生在碰模型之前） | 403 且 code=INSUFFICIENT_SCOPE | 403 且 code=INSUFFICIENT_SCOPE | ✅ |

## 二、进程边界

这一节守的是那个已经踩过一次的坑：**服务端的人话不许出现在 stdout 上**。`08_mcp_smoke.py` 是在 stdio 传输下踩的（stdout 就是 JSON-RPC 通道，一行「已注册 6 个工具」让客户端解析器直接崩）；HTTP 模式下 stdout 不是协议通道，但脚本仍然靠 stderr 拿端口 —— 规矩一致，守卫也一致。

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 服务端 stdout 里一行人话都没有（端口那行在 stderr） | stdout 无内容；stderr 里有「监听 host:port」 | stdout 0 行；stderr 有端口那一行 | ✅ |

## 三、值得记下来的实测行为

- **未知工具走的是真 404**：`POST /tools/nope` 匹配不到任何路由，Starlette 抛 404，被一个只在 `/tools/` 前缀下生效的异常处理器改写成与 stdio 侧同一个拒绝体。其他路径的 404 行为原样保留。
- **被护栏拦下是 200**：这是本层唯一一个「看起来该报错、实际报成功」的映射。判定依据是「请求被正确处理并得出结论了吗」—— 是，所以是成功；它和 403 的区别是「你的 SQL 写得不对」与「你没有权限」，调用方对这两件事的处置完全不同。
- **`DEV_SECRET_REFUSED` 映射成 500 而不是 403**：这个码的含义是「服务端自己在严格模式下还在用内置开发密钥」，是服务端的配置问题。报 403 等于把运维的锅甩给调用方。
- **缺参数与没权限是两个码**：少给 `sql` 是 400 `BAD_ARGUMENTS`，不带令牌是 403 `NO_TOKEN`。混成一个的话，排错的人会查错方向。
