# 鉴权用例矩阵（实测结果）

本文件由 `experiments/10_auth_test.py` 生成，**不要手改**；重跑该脚本即可复现。

## 口径

- **一条用例** = 一次 `(工具, 令牌)` 的鉴权裁决。
- **通过** = 实测错误码与用例声明的期望**逐字相同**。不接受「反正拒了」这种宽松判定 ——签名错和 scope 不足被报成同一个码，调用方就没法据此做分支。
- **应拒**用例测的是安全性；**应放行**用例测的是可用性。只有拒绝的测试是一种假安全：一刀切全拒也能拿满分。
- 本实验**不联网、不调用模型、不需要构建 `data/` 下的产物**。
- 运行方式：`python experiments/10_auth_test.py`（退出码 0 = 全过）

## 总览

| 项 | 通过 / 总数 |
|---|---|
| 应拒用例 | 36 / 36 |
| 应放行用例 | 13 / 13 |
| 工具入口一致性 | 18 / 18 |
| **合计** | **49 / 49** |

## 一、应拒用例（安全性方向）

共 36 条，通过 36 条。

| # | 用例 | 工具 | 期望 | 实测 | 结果 |
|---|---|---|---|---|---|
| 1 | 完全没有令牌（空串） | `list_tables` | `NO_TOKEN` | `NO_TOKEN` | ✅ |
| 2 | 令牌为 None | `list_tables` | `NO_TOKEN` | `NO_TOKEN` | ✅ |
| 3 | 令牌为整数 0（假值） | `list_tables` | `NO_TOKEN` | `NO_TOKEN` | ✅ |
| 4 | 令牌为空列表（假值） | `list_tables` | `NO_TOKEN` | `NO_TOKEN` | ✅ |
| 5 | 令牌为整数 1（真值但非字符串） | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 6 | 令牌为字典（真值但非字符串） | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 7 | 普通字符串 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 8 | 三段垃圾 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 9 | 只有两个点，末尾签名缺失 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 10 | 超长垃圾串 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 11 | alg:none 无签名 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 12 | HS512 同密钥（算法白名单应拦下） | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 13 | 换一个密钥签名 | `list_tables` | `BAD_SIGNATURE` | `BAD_SIGNATURE` | ✅ |
| 14 | issuer 不符 | `list_tables` | `BAD_ISSUER` | `BAD_ISSUER` | ✅ |
| 15 | audience 不符 | `list_tables` | `BAD_AUDIENCE` | `BAD_AUDIENCE` | ✅ |
| 16 | 已过期 | `list_tables` | `TOKEN_EXPIRED` | `TOKEN_EXPIRED` | ✅ |
| 17 | ttl=0（签发即过期） | `list_tables` | `TOKEN_EXPIRED` | `TOKEN_EXPIRED` | ✅ |
| 18 | iat 在未来（客户端时钟快） | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 19 | 缺 exp | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 20 | 缺 iss | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 21 | 缺 aud | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 22 | 缺 sub | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 23 | 令牌没有 scope | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 24 | 令牌的 scope 键整个缺失 | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 25 | 只有 db:query，却要调 db:read 的工具 | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 26 | 只有 kb:read，却要调 db:read 的工具 | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 27 | 只有 db:read，却要调 db:query 的工具 | `run_sql` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 28 | 只有 db:read，却要调 kb:read 的工具 | `search_passages` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 29 | size 大小写不符 DB:READ | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 30 | 用通配符 db:* 冒充 db:read | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 31 | 把 scope 写在别的前缀下 read:db | `list_tables` | `INSUFFICIENT_SCOPE` | `INSUFFICIENT_SCOPE` | ✅ |
| 32 | scope claim 是数字而不是字符串 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 33 | scope claim 是列表而不是字符串 | `list_tables` | `INVALID_TOKEN` | `INVALID_TOKEN` | ✅ |
| 34 | 工具名未登记 | `no_such_tool` | `UNKNOWN_TOOL` | `UNKNOWN_TOOL` | ✅ |
| 35 | 工具名为空串 | `` | `UNKNOWN_TOOL` | `UNKNOWN_TOOL` | ✅ |
| 36 | 工具名大小写不符 Run_SQL | `Run_SQL` | `UNKNOWN_TOOL` | `UNKNOWN_TOOL` | ✅ |

## 二、应放行用例（可用性方向）

共 13 条，通过 13 条。

| # | 用例 | 工具 | 结果 | 备注 |
|---|---|---|---|---|
| 1 | db:read 调 list_tables | `list_tables` | ✅ | — |
| 2 | db:read 调 get_schema | `get_schema` | ✅ | — |
| 3 | db:query 调 run_sql | `run_sql` | ✅ | — |
| 4 | db:query 调 ask_database | `ask_database` | ✅ | — |
| 5 | kb:read 调 search_passages | `search_passages` | ✅ | — |
| 6 | kb:read 调 get_passage | `get_passage` | ✅ | — |
| 7 | 三种 scope 全给，调 list_tables | `list_tables` | ✅ | scope 是「持有集合」语义，多给不该变成拒绝 |
| 8 | 多给一个无关 scope，调 run_sql | `run_sql` | ✅ | 同上 |
| 9 | scope 重复写两遍，调 list_tables | `list_tables` | ✅ | 去重后仍然放行 |
| 10 | scope 前后有空格 | `list_tables` | ✅ | split() 会吃掉空白 |
| 11 | 令牌额外带了未知 claim（role=admin） | `list_tables` | ✅ | 未知 claim 被忽略，不影响裁决 |
| 12 | sub 是空字符串 | `list_tables` | ✅ | ⚠️ 值得注意：require 只查键存在、不查值非空，所以空 sub 会放行 |
| 13 | 列表元素里含空格（db:read db:query） | `list_tables` | ✅ | scope 在令牌里是空格分隔的（RFC 6749），所以带空格的元素**本来就等于两个 scope**，不是注入也不是漏检。这条原本被我写成应拒用例，跑出来发现是期望写错了，照实改成放行并留在这里。 |

## 三、工具入口一致性（真实工具函数）

第一节只证明了「决策函数是对的」。这一节调用**真实的工具函数**，证明「工具真的用了那个决策」。少了这一节，某个工具入口漏写鉴权是查不出来的。

共 18 条，通过 18 条。

| 工具 | 情形 | 期望 | 实测 | 结果 |
|---|---|---|---|---|
| `list_tables` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `list_tables` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `list_tables` | 令牌齐全 | 不拒绝 | 不拒绝；业务层：ok | ✅ |
| `get_schema` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `get_schema` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `get_schema` | 令牌齐全 | 不拒绝 | 不拒绝；业务层：ok | ✅ |
| `run_sql` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `run_sql` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `run_sql` | 令牌齐全 | 不拒绝 | 不拒绝；业务层：ok | ✅ |
| `search_passages` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `search_passages` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `search_passages` | 令牌齐全 | 不拒绝 | 不拒绝；业务层：ok | ✅ |
| `get_passage` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `get_passage` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `get_passage` | 令牌齐全 | 不拒绝 | 不拒绝；业务层：ok | ✅ |
| `ask_database` | 无令牌 | 拒绝 | 拒绝 | ✅ |
| `ask_database` | scope 不足 | 拒绝 | 拒绝 | ✅ |
| `ask_database` | 令牌齐全 | 不拒绝 | 未调用（放行后会去调模型，本实验不调模型） | ✅ |

> **「令牌齐全」那一行的口径**：断言的是**工具没有拒绝**，不是「业务执行成功」。本实验不要求数据已经构建，所以业务层返回「库不存在」之类的错误码是正常的，它们被原样写进「实测」列。
> 因此「实测」列的文字会**随机器而变**（数据构建过就是 `ok`，没构建过就是构建相关的错误码），但**「结果」列不会** —— 通过与否只取决于鉴权裁决。
> `ask_database` 的「令牌齐全」一行标为未调用：它一旦放行就会真的去调模型，而本实验不调模型。它在决策层（第一、二节）是被完整断言的。

## 四、实测到的边界行为

下面几条与直觉不同，但**跑出来就是这样**。照实记录，不做美化；本仓库不改它们，只把行为写清楚。

| 现象 | 实测 | 说明 |
|---|---|---|
| HS512 用同一个密钥签，报的是 INVALID_TOKEN 而不是 BAD_SIGNATURE | `INVALID_TOKEN` | 算法白名单在验签之前就拒了，连签名都没看。拒得对，只是错误码偏向「令牌不合法」而非「签名不对」。调用方若按 BAD_SIGNATURE 做分支要注意。 |
| iat 比服务端快 10 分钟就被拒 | `INVALID_TOKEN` | PyJWT 默认校验 iat。真实部署里客户端时钟快一点就会拿到这个错误。这是实测行为，不是猜测；本仓库不改它，只把它写清楚。 |
| sub 为空字符串仍然放行 | `（放行）` | `options={'require': [...]}` 只检查 claim 键存在，不检查值非空。本仓库接受这一点（sub 的取值语义属于调用方），但必须写明，免得被读成「sub 一定非空」的保证。 |
| scope 重复写不影响结果 | `（放行）` | scope 解析成集合，重复项被去重。 |

## 五、局限

- 全部用例都在**进程内**直接调用，没有经过 MCP 的传输层。「令牌经由 stdio 传进来之后还是不是这样」是 `08_mcp_smoke.py` 的范围。
- 密钥用的是仓库内置的开发用默认值。**换密钥不会改变本表任何一行**（裁决只看签验是否一致，与密钥取值无关），所以这里没有逐个密钥再跑一遍。
- 用例是**枚举**出来的，枚举得再全也不能证明「不存在更强的伪造手法」。本表证明的是「列出的这些都被挡住了」。
