# mcp-gatekeeper

一个基于 **MCP（Model Context Protocol）** 的本地工具服务：把一份示例数据库和一份公版文本库
开放成 6 个工具。其中「让模型自己写 SQL 查数据库」这条链路必须真的安全 ——
所以它配了**三层 SQL 护栏**、**工具级 scope 鉴权**、**有界重写状态机**和**可复核的数字口径**。

它的用途是**技术验证**：证明这套工程约束是可迁移的，而不是绑在某一份数据上的特例。

- 只用公开、可再分发的数据：示例库 + 公版中文文本（逐篇出处见 [NOTICE.md](NOTICE.md)）
- **不联网、不需要模型凭据**就能跑完全部测试与自检（自检里靠词表的那几项会判 `SKIP`，
  见「已知边界」——`SKIP` 是「没查」，不是通过）
- 报告里每个数字都能指出「脚本名 + 口径」，没有估计值
- 需要模型凭据的那条链路，在配好凭据之前一律报「未完成」，**不编数字**（见 [BLOCKERS.md](BLOCKERS.md)）

## 快速开始

```bash
# 1) 单元测试（零网络、零模型调用）
python -m pytest -q

# 2) 构建数据：示例库（.sql → .db）与文本库（切词 → 全文索引）
python experiments/01_build_db.py
python experiments/02_build_kb.py

# 3) 安全层与协议层：鉴权矩阵、护栏负例矩阵、真实 stdio 握手
python experiments/10_auth_test.py
python experiments/11_guardrail_test.py
python experiments/08_mcp_smoke.py

# 4) 一条命令回答「现在能不能交出去」
python tools/check.py
```

`python tools/check.py` 会依次跑单元测试、台账式自检、交付物核验、收尾自检，
**退出码 = 卡在第几步**（0 = 全部通过）。只想看它要跑什么：`python tools/check.py --list`。

### 起服务端

```bash
python -m mcp_server.server --check     # 只做启动自检：注册的工具与 scope 表是否一致
python -m mcp_server.server             # 真正拉起 stdio 服务端
```

stdio 服务端的 **stdout 是协议通道**，所以人话一律走 stderr —— 打印一行启动信息到
stdout 会让握手直接报 `Invalid JSON`（这个 bug 只有真实握手能抓到，见 `PROGRESS.md` 阶段 3）。

## 工具集

6 个工具、3 个 scope。scope 挂在**工具**上而不是「用户」上，所以换一个客户端、
换一次会话，权限判定都一样。

| 工具 | scope | 做什么 |
|---|---|---|
| `list_tables` | `db:read` | 列出所有表和视图 |
| `get_schema` | `db:read` | 看某张表的列与类型 |
| `run_sql` | `db:query` | 执行一条只读查询（过三层护栏） |
| `ask_database` | `db:query` | 自然语言提问 → 模型写 SQL → 护栏 → 执行 → 失败则重写 |
| `search_passages` | `kb:read` | 在公版文本库里检索段落，返回原文片段与偏移量 |
| `get_passage` | `kb:read` | 按偏移量取回原文 |

调用工具要带一个 JWT，令牌里带着 scope 列表：

```python
from mcp_server import auth
token = auth.make_token("demo-user", scopes=["db:read", "kb:read"])
```

`ask_database` 是唯一需要模型凭据的工具；其余 5 个不需要。

## 目录结构

```
mcp_server/     服务端与全部纯逻辑（可 import，因此可单测）
  auth.py          JWT 签发/校验/guard()，工具级 scope 表
  guardrails.py    三层 SQL 护栏
  db_tools.py      列表 / 结构 / 查询
  kb_tools.py      检索 / 取原文
  t2sql_core.py    自然语言转 SQL 的状态机（有界重写）
  cost.py          用量账本（JSONL 只追加，金额允许为空）
  selfcheck.py     仓库卫生扫描口径（词表、范围、掩码）
  verify_kit.py    退出码台账与判定
  deliverable_kit.py  交付物核验 G1~G8
experiments/    可重跑的入口脚本（全部幂等）
tests/          单元测试：零网络、零模型调用
tools/check.py  守门链
data/           公开数据（示例库 .sql + 公版文本 + 出处清单）
reports/        全部实测产物落盘
docs/           设计说明
```

## 数据与许可

- 示例数据库：Chinook（MIT），随仓库分发 `.sql` 源文件，由脚本构建成数据库
- 文本库：40 篇公版中文文本，逐篇的标题、作者、原文地址、许可与 sha256 见 [NOTICE.md](NOTICE.md)
- 构建产物（数据库文件、全文索引）不入库，由 `experiments/01_build_db.py` 与
  `experiments/02_build_kb.py` 确定性重建

## 怎么证明它是对的

四层，从细到粗：

| 谁 | 管什么 |
|---|---|
| `python -m pytest` | 某个函数对不对 |
| [reports/verify.md](reports/verify.md) | 每个入口脚本的**实际退出码**对不对得上声明 |
| [reports/deliverable_check.md](reports/deliverable_check.md) | 文档里写的和仓库里**对不对得上** |
| [reports/final_selfcheck.md](reports/final_selfcheck.md) | 发布前 9 项红线，逐项报命中数 |

三条原则写在 `docs/design.md` 里，这里只列结论：

- **每一项检查都要能红。** 只证明「会绿」的检查，一个永远返回 `PASS` 的假实现也能通过。
- **「未完成」必须交证据。** 需要模型凭据的脚本以退出码 5 结束，同时留下写清卡在哪一步的
  「未完成」说明和 `BLOCKERS.md` 里对应的最小动作。三件缺一，判失败 ——
  没有落盘、没有解法的「未完成」，和「忘了跑」长得一模一样。
- **报告不能成为它自己报告的污染。** 自检报告要写出「在哪里命中了什么」，
  而写出来的那一刻，报告自己就带上了那个词。处置方式见 `docs/design.md` 第 4.4 节。

## 当前的完成状态

| 部分 | 状态 |
|---|---|
| 数据构建、鉴权、三层护栏、MCP 握手、知识库检索 | 已完成，有实测报告 |
| 自然语言转 SQL（`ask_database`） | **未完成：本机没有模型凭据**。代码与自检全部就绪，`--fake` 假模型自检通过 |
| 噪声带与配对统计 | 同上：尺子有正负控，但真实对照要模型凭据 |

真实跑出来的数字见 [RESULTS.md](RESULTS.md)；每个数字都注明了脚本与口径。

## 已知边界

- **卫生扫描用的词表按设计不在仓库里**：那张「不许出现的名字 / 数字 / 术语」表
  放在仓库**外面**（`MCP_TOOLKIT_WORDLIST` 指向的 `.py`，没设就找仓库同级目录的
  `mcp-guarded-toolkit-wordlist.py`）。把它放进仓库等于在仓库里再造一份那些字面量 ——
  那正是这个仓库修掉的那座「明文桥」。**代价写清楚**：clone 下来跑自检时，
  靠词表的那几项判 `SKIP`（**没查，不是通过**），所以 `python tools/check.py`
  在没有词表的机器上停在「未完成」，**不会全绿**。这不是失败，但也不是通过。
- **第一次跑之前要构建数据**：`data/` 下的示例库与全文索引是构建产物、不入库，
  所以 clone 下来直接跑闸门会停在「示例库还没构建」，先跑「快速开始」第 2 步
  那两条命令（不需要联网）。
- **不联网**：数据随仓库分发；只有「重新抓语料」这一个脚本需要网络，且它是可选的。
- **护栏不是形式化证明**：三层护栏（文本校验 / 只读连接 / 引擎级 authorizer）
  挡的是「写不进数据库」，不是「SQL 一定正确」。`experiments/11_guardrail_test.py`
  列了 41 条负例，那 41 条之外没测过的写法是未知的。
- **交付物核验 G4 的强度有限**：它查「文档里的 `--flag` 在源码里出现过」，
  不保证 `argparse` 真的注册了那个参数。
- **本机没有模型凭据**，所以 `ask_database` 的实测数字目前不存在 ——
  报告里写的是「未完成」，不是 0，也不是任何估计值。

设计取舍与代价逐条记在 [DECISIONS.md](DECISIONS.md)，整体思路见 [docs/design.md](docs/design.md)。
