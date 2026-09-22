# 实测结果

> 这份文件只放**真的跑出来**的数字，每一项都注明「脚本 + 口径」。
> 没有估计值，没有「大概齐」，也没有把「未完成」写成 0。

## 口径

- **怎么复现**：每条命令都在本仓库根目录下跑，用的是同一个解释器；
  改一个数字的分工是「重跑那条命令」，不是「改这个文件」。
- **不认识模型凭据的命令**：全部零网络、零费用，任何时候都能重跑。
- **需要模型凭据的命令**：数字来自**本机配好一份凭据之后**的那几次运行（第二节）。
  凭据不在仓库里，所以别人 clone 之后要自己配一份才能重跑同样的命令；
  第二节的表里每一项都写着是哪个脚本、什么口径。
- **对照产生的差异怎么措辞**：先与实测噪声带比；落在噪声带内只写
  「方向，未达可判定水平」，不用那四个被禁的词（词表见 `mcp_server/eval_grading.py`
  的 `BANNED_WORDS`）。这条纪律由 `eval_runner.write_report()` 在真实产物上强制。

## 一、不需要模型凭据的部分（已完成）

| 数字 | 值 | 脚本 / 口径 |
|---|---|---|
| 鉴权 应拒 | 36 / 36 | `experiments/10_auth_test.py`：每种失败模式一条用例，实测错误码与声明的期望**逐字相同**才算过 |
| 鉴权 应放行 | 13 / 13 | 同上：合法令牌必须真的走到业务代码。只有拒绝方向的测试是假安全 —— 一刀切全拒也能拿满分 |
| 鉴权 工具入口一致性 | 18 / 18 | 同上：经 `server.py` 的工具入口再跑一遍拒绝侧 |
| 护栏 负例与正例 | 41 / 41 | `experiments/11_guardrail_test.py`：退出码与**规则号**两个都对才算过（否则「L1 拦的」和「L3 拦的」分不开） |
| 护栏 写操作落地检查 | Genre 表 25 行 → 25 行 | 同上 `--raw` 路径：绕过 L1 直连只读连接试写，前后各数一次行数，证明**真的没写进去** |
| MCP 握手 | 15 / 15 | `experiments/08_mcp_smoke.py`：把服务端当子进程拉起，走真实 stdio；12 条协议用例 + 3 条超时用例 |
| 握手报出的工具数 | 6 | 同上：线上 `input_schema`、`TOOL_SCOPES`、本地注册表三处集合必须两两相等 |
| 协商到的协议版本 | `2025-11-25` | 同上，从 `initialize` 的结果里读 |
| 语料 篇数 / 作品数 | 40 篇 / 34 部 | `data/kb/manifest.json`；抓取与截断口径见 `experiments/00_fetch_corpus.py` |
| 语料 正文字数 | 694005 字符 | 同上（NFC 归一化、去页眉页脚、逐篇截断之后） |
| 语料 截断篇数 | 6 | 同上：`manifest.json` 的 `truncated` 字段计数 |
| 文本库 索引块 | 1339 块，平均 518 字符 | `experiments/02_build_kb.py`：公开文本切词后建全文索引，块目标长度 500 字符 |
| 文本库 偏移量抽检 | 20 组，问题 0 | 同上：按行上的偏移量回原文切片做往返比对（种子 20260918） |
| 示例库 行数合计 | 15607 行 | `experiments/01_build_db.py`：11 张表逐表计数；两次构建哈希一致 |
| 正式题集 参考解核验 | 32 / 32 | `python experiments/09_text2sql_eval.py --check-questions`：非空、不超过 200 行、连跑两次一致 |
| 口语题集 核验 | 15 / 15 | `python experiments/13_text2sql_colloquial.py --check-questions`：另加「参考 SQL 与正式题**逐字相同**」 |
| 假模型 oracle | 严格 32/32、投影 32/32、不可答命中 4/4，调用 36 次 | `python experiments/09_text2sql_eval.py --fake oracle`：满分模型必须拿满分，否则是评分有 bug |
| 假模型 naive | 严格 0/32、投影 0/32 | 同上 `--fake naive`：故意答错必须拿不到分，否则是评分太松 |
| 假模型 flaky | 严格 32/32，调用 68 次 | 同上 `--fake flaky`：让重写路径真的被走到 |
| 噪声带尺子 正控 | 量出 0 | `python experiments/21_noise_band.py --fake stable`：两遍完全一样时必须量出 0 |
| 噪声带尺子 负控 | 正好量出 3 | `python experiments/21_noise_band.py --fake jitter`：第二遍故意抖 3 题，必须正好量出 3 |
| 配对统计自检 | 7 组通过 | `python experiments/20_pair_test.py --selftest`：合成数据，含措辞禁用词检查 |
| HTTP 入口冒烟 | 20 / 20，退出码 0 | `python experiments/30_http_smoke.py`：真起子进程、真 TCP 端口；含 403 NO_TOKEN / 403 INSUFFICIENT_SCOPE / 200 三种状态码与「被护栏拦下 → 200」 |
| HTTP 路由与工具表一致 | 6 / 6 | 同上：`GET /tools` 报出来的工具数 == `auth.TOOL_SCOPES` 键数 == 6，两边名字逐字相同 |
| 并发实测峰值（三种跑法） | 串行 **1** / 天真 gather **1** / 正确并发 **6** | `python experiments/29_concurrency_bench.py`：24 条真调用，每条工具外面套 20 毫秒固定延迟当量具；峰值由带锁计数器**量**出来，不是按设定值写下的 |
| 并发三种跑法的成功数 | 各 24 / 24（且调用数相同） | 同上：三种跑法干的是同一份活 —— 提速只能靠并发，不能靠少干活 |
| 并发超时降级 | 1 条超时 + 23 条正常返回，退出码 0 | 同上：受控探针里塞一条必然超时的调用，`wait_for` 把它降级成一条结果记录，整批不塌 |
| 单元测试 | **671 passed** / 0 skipped，退出码 0 | `python -m pytest -q` |
| 产物可复现性 | 跑闸门前后的聚合哈希**相同**（两遍逐字节一致） | 见下面的「怎么自己验一遍」；哈希由 `tools/check.py --snapshot` 打印 |

> **这一行的读数变过，旧值照实留在这里。** 本表最初写的是 `530 passed`，
> 那是**阶段 5 那一版**的记录。此后每加一条用例它就变一次
> （543 → 570 → 598+3 skipped → 604），而这张表没有跟着改 ——
> 表里别的数字都由脚本产出，唯独这一行是手抄的，**手抄的那一格最容易过期**。
> 2026-09-19 按实测校准为 **604**（`python -m pytest -q`，收集 604 条）。
> 2026-09-22 又按实测校准为 **671**：本轮新增了 `tests/test_dispatch.py`（派发器）、
> `tests/test_http_api.py`（HTTP 路由与状态码映射）、`tests/test_concurrency.py`（15 条，
> 含「实测峰值 == limit」与「`offload=False` 时峰值退化成 1」），一个用例都没删。
> 逐次变化的条数说明见 `PROGRESS.md`「单元测试条数」那几节。
> ★ 本行只报**当前**读数；历史读数属于哪一版，以 `PROGRESS.md` 与
> `BLOCKERS.md` 的补记为准（那里同样保留 543 这类旧值）。
| 台账式自检 | 35 条：PASS 31 / FAIL 0 / SKIP 4 / ERROR 0，退出码 0 | `python experiments/19_verify.py`；4 条 SKIP 全部是同一个理由「本轮未重跑，盘上已有实测产物」（需要模型凭据的那几条，见 `DECISIONS.md` D-44），**不是通过**。旧读数是 32 条（PASS 29 / SKIP 3），本轮涨到 35 条是因为台账里**追加**了 29 / 30（各 PASS）与 31（SKIP）三条，一条旧条目都没改 |
| 交付物核验 | 8 条：PASS 8 / FAIL 0 / SKIP 0，退出码 0 | `python experiments/25_deliverable_check.py`：文档里写的和仓库里对不对得上 |
| 收尾自检 | 9 项全部通过，退出码 0 | `python experiments/26_final_selfcheck.py`，产物见 [reports/final_selfcheck.md](reports/final_selfcheck.md) |
| 守门链 | 4 步全过，退出码 0 | `python tools/check.py`：退出码 = 卡在第一步 |

**怎么自己验一遍「产物可复现」**（这就是上表最后那一行的口径）：

```powershell
python tools/check.py --snapshot   # 记下哈希
python tools/check.py              # 跑闸门，它会重写 reports/ 下的产物
python tools/check.py --snapshot   # 应当还是同一个哈希
```

两次相同，意思是**同一份代码跑两遍，产物逐字节一样** —— 也就是跑完闸门
`git status` 还是干净的。这不是自动成立的：产物里混进一个时钟读数
（`pytest` 尾巴里的耗时就是一种）就会每跑一次都不一样，而那样一来
`git status` 就不再是「有没有变化」的信号。这条由
`tests/test_repo_hygiene.py::test_the_committed_artifacts_carry_no_wall_clock_reading`
静态守着（理由见 `DECISIONS.md` D-38）。

逐条明细（每条含期望 / 实测 / 退出码）：

- 鉴权：[reports/auth_results.md](reports/auth_results.md)
- 护栏：[reports/guardrail_results.md](reports/guardrail_results.md)
- 握手：[reports/mcp_smoke.md](reports/mcp_smoke.md)
- 台账式自检：[reports/verify.md](reports/verify.md)
- 交付物核验：[reports/deliverable_check.md](reports/deliverable_check.md)

## 二、需要模型凭据的部分（已跑完）

**这一节每个数字都能指出「脚本 + 口径」。** 它们在**本机配好一份模型凭据之后**
的几次运行里跑出来；凭据本身不在仓库里（见 [BLOCKERS.md](BLOCKERS.md)），
所以别人 clone 之后要自己配一份才能重跑同样的命令。

| 数字 | 值 | 脚本 / 口径 |
|---|---|---|
| 正式题集 严格 EX（v1 自由投影） | 29 / 32 | `experiments/09_text2sql_eval.py --versions v1`：分母只数 32 道可答题；判定是「结果集与参考解逐行逐列相同」 |
| 正式题集 严格 EX（v2 加最小投影约束） | 30 / 32 | 同上 `--versions v2` |
| 正式题集 严格 EX（v3 加列名指引 + 「答不了」自救） | 27 / 32 | 同上 `--versions v3` |
| 投影容忍口径 | 与严格口径三版完全相同（29 / 30 / 27） | 同上：这批题里「投影过度」的情况一处都没发生，两种口径因此同分。**这不代表两种口径等价**，只代表这批题区分不出它们 |
| 「答不了」的 4 题判定 | v1 0/4、v2 0/4、**v3 4/4** | 同上：这 4 题按设计无解，「说答不了」才算对；它们**不进**上面的分母 |
| 实测噪声带 | **1 题** | `experiments/21_noise_band.py`：同一臂（v3）在 temperature=0 下连跑两遍，逐题比对严格判定 —— 36 题里翻转 1 道可答题（q26） |
| 配对检验 v1↔v2 | 不一致 **1** 题，净差 **−1**，**未超出**噪声带 | `experiments/20_pair_test.py`：McNemar 精确双尾；p 值与逐对措辞见 [reports/pair_test.md](reports/pair_test.md) |
| 配对检验 v1↔v3 | 不一致 **2** 题，净差 **+2**（v1 独对 2、v3 独对 0），**超出**噪声带 | 同上 |
| 配对检验 v2↔v3 | 不一致 **3** 题，净差 **+3**（v2 独对 3、v3 独对 0），**超出**噪声带 | 同上 |
| 口语问法 vs 正式问法 | 13 对：双对 11、双错 2、**不一致 0** —— 未观测到差异 | `experiments/13_text2sql_colloquial.py`：同一版（v3）下两种问法逐题配对 |
| 模型调用次数 | **298** 次 | `experiments/12_cost_report.py`：读 `reports/usage_ledger.jsonl`，一行 = 一次调用，重写重试各算一次 |
| 双向 token | 输入 **191196** / 输出 **5749** | 同上：取自模型返回的 `usage` 字段 |
| 金额 | 留空（`—`） | 同上：没有可引用的单价出处（要价目页 URL + 抓取日期）。**不用别家单价兜底，也不填 0** |
| 并发下的账本对账 | 并发批 12 题、串行批 12 题；两批**各自**「账本新增行数 == `usage.calls` 之和 == 12」 | `python experiments/31_ledger_under_concurrency.py`：12 题 × 2 批（`limit=6` 与 `limit=1`），走 `dispatch` → `ask_database`。口径只认**行数**：一行账本 = 一次模型调用，重写重试各算一次 |
| 并发批 vs 串行批的 token | 输入 3017 / 3017，输出 203 / 201 | 同上：输入侧逐字相同（同一批题、同一份 schema 提示）；输出侧差 2 —— 模型本身有不确定性，**这一项不要求相等**。真正的不变量是上一行的行数对账，不是 token 相等 |

> **上面那两行的读数也变过，旧值照实留在这里。** 本轮之前是
> 「197 次 / 输入 165820 / 输出 4097」，那是 2026-09-18 那次评测的账。
> 2026-09-22 为了做并发下的账本对账又调了模型（连同开发期间的重跑），
> 账本按设计只追加，于是总数涨到 **298**。`reports/usage_ledger.jsonl`
> 里 2026-09-22 那一批共 101 行，每一行都对应一次真实调用，**没有补写、没有编造**；
> 对账用的报告只统计**单批新增**的 12 行，与总数无关。旧值属于哪一版见
> `PROGRESS.md` 的阶段记录。

**p 值为什么不在这里再抄一遍**：`v1↔v3` 那一对的精确 p 值与
`selfcheck.VERIFIED_COINCIDENCES` 里登记的一个「巧合数字」逐字相同，
而本仓只允许那个字面量出现在 `reports/pair_test.md` / `reports/pair_test.json` 里
（登记项由 `tests/test_repo_hygiene.py` 钉着，路径不在表里就不放行）。
数字的唯一住所是那份报告；这里只报不一致对数与「是否超出噪声带」。

### 这一节里最该看的一条：v3 不是「更好的版本」

把它单列，是因为它与「提示词越改越好」的直觉相反，而且这是**实测**的：

- **可答题上 v3 更低**：严格 EX 27/32，低于 v1 的 29/32、v2 的 30/32。
  与 v1 差 2 题、与 v2 差 3 题，两对**都超出**实测噪声带（1 题）。
- **不可答题上 v3 全对**：v1 与 v2 都是 0/4，v3 是 4/4。
- **代价是怎么付的**：v3 丢掉的 3 道题（q16、q22、q25）在 v3 的记录里
  `status` 都是 `ok`、`attempts` 都是 1 —— 也就是说它**不是拒答，是答了但答错**。
  所以这不是「模型变保守了」，而是换提示词之后生成了不同的 SQL：
  v3 买到了不可答题的 4/4，代价是可答题上的 2~3 题。
- **结论到此为止**：可答题只有 32 道、不一致对数是 1~3，
  这批题分辨不出更细的差别。方向照实报，不往更大的说法上写。

汇总口径与逐对措辞：[reports/pair_test.md](reports/pair_test.md)；
逐题记录：[reports/text2sql_results.v1.json](reports/text2sql_results.v1.json)、
[.v2.json](reports/text2sql_results.v2.json)、[.v3.json](reports/text2sql_results.v3.json)；
噪声带：[reports/noise_band.json](reports/noise_band.json)；
口语对照：[reports/colloquial_vs_formal.md](reports/colloquial_vs_formal.md)；
成本：[reports/cost_report.md](reports/cost_report.md)。

**为什么这些数字别人不能直接照抄**：它们来自本机的 `reports/usage_ledger.jsonl`
（按设计不进仓库 —— 它是本机的用量流水）。换一台机器、换一个模型版本，
数字都会不同；能照抄的是**命令与口径**，不是数值。

## 三、这些数字是在什么状态下的仓库上跑出来的

- 全部命令在本仓库根目录执行，解释器为 Python 3.12；
- 每条数字对应的产物都已落盘（`reports/` 下），可以对着报告逐条核；
- **跑完之后工作区应当是干净的**：`reports/` 下的产物是同一份代码的可复现输出，
  重跑不会改变它们的字节。所以 `git status` 显示的改动 = 真的有人改了东西。
- **例外：本机有一条会拦住批量删除的安全钩子。** 它拦下 pytest 清理临时目录时，
  pytest 会以非零退出码结束（**没有任何测试失败**），于是 `reports/verify.md`
  会记下一条 P1 失败，工作区也就不再干净。这不是仓库的性质，是环境的性质 ——
  判据是「尾巴里有没有 `FAILED` 行，没有就先怀疑环境」。实测证据见
  `DECISIONS.md` D-42；重跑一次即恢复。

## 四、这些数字**不能**说明什么

- **41 条负例之外没测过的写法是未知的。** 三层护栏挡的是「写不进数据库」，
  不是「SQL 一定正确」；它不是形式化证明。
- **`--fake` 的分数不是模型分数。** 它们证明的是「评分与状态机的逻辑对不对」，
  与模型表现无关。
- **噪声带是「一对重复对照」量出来的，不是置信区间。** 实测值 1 题只能当
  「低于 1 题就别当回事」的下限用，不能当统计意义上的界。
- **成本账本的 `source` 字段分不出链路，只分得出提示词版本。** 实测：
  账本里只出现 `t2sql:v1`(36) / `t2sql:v2`(36) / `t2sql:v3`(125) 三种取值 ——
  口语对照的 15 次与噪声带的两轮 72 次都记在 `v3` 名下，只能靠算术反推，
  账本本身分不开。这是已知的实现边界，照实记在这里。
- **SKIP 不是 PASS。** 需要模型凭据的项在**本轮没重跑**时是 SKIP，
  它在 `19_verify.py` 的报告里单独标注，并写明「盘上已有实测产物是哪一个」
  （`reports/verify.md`）。默认只读台账、不重跑，是有意的 ——
  理由见 `DECISIONS.md` D-44。
