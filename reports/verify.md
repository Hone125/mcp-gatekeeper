# 自检台账

## 口径

- **脚本**：`experiments/19_verify.py`，一条命令跑完下面每一行
- **判定**：`PASS` / `FAIL` / `SKIP` / `ERROR` 四选一。`SKIP` 不等于 `PASS` —— 它表示「这项现在没资格下结论」
- **S1~S4**：四类红线的实际命中数，词表与扫描范围定义在 `mcp_server/selfcheck.py` 的文件头
- **S5/S6**：密钥文件与密钥形态。后者扫全文（含报告）；命中的串在报告里只显示前 24 个字符
- **S7**：作者机器绝对路径，只扫自己写的文件
- **S8**：逐份报告实测数字个数，有数字却没有 `## 口径` 标题行的判不合格。「数字」定义为不贴着字母/下划线/点/斜杠/连字符的数字串，比值（`32/32`）整体算一个
- **S9**：本报告**自己**再扫一遍全部红线词表与形态。理由是个会自我放大的回路：报告要列出命中的位置，若逐字抄下来，报告自己就成了新的载体，下一轮会在报告里再次命中它。所以命中内容一律掩码（见 `mcp_server/selfcheck.py` 的 `mask_all`），并由这一项机械地确认掩码没漏 —— 不靠「写的时候小心一点」
- **A\***：产物互斥检查 —— 「实测结果」与「未完成声明」不许同时存在
- **D\***：根目录必需文档；阶段 6 的两份未创建时判 `SKIP`
- **X\***：每个入口脚本的**实际退出码**与它声明的期望码比对。需要模型凭据的脚本以 5 结束是**合法**的，但必须同时满足：留下 `*_NOT_RUN.md` + `BLOCKERS.md` 里有对应动作
- **P1**：`python -m pytest` 的退出码
- **P2**：服务端注册表自检（注册的工具名集合 == `TOOL_SCOPES` 的键集合）
- **本表数字的性质**：判定计数、命中数、退出码**全部是实测的**；没有任何一个数字是估计、写死或从别处抄来的

**合计**：32 条 —— PASS 29 / FAIL 0 / SKIP 3 / ERROR 0

## 明细

| # | 检查项 | 判定 | 说明 |
|---|---|---|---|
| S1 | 识别性名称（公司名 / 课程名 / 原项目名） | PASS | 0 命中 |
| S2 | 领域敏感语义（词表见 selfcheck.MEDICAL_TERMS） | PASS | 0 命中 |
| S3 | 原项目的具体数字 | PASS | 0 命中（另有 3 处已核验巧合，不算违规；见 selfcheck.VERIFIED_COINCIDENCES） |
| S4 | 夸大措辞 | PASS | 0 命中 |
| S5 | 密钥文件（.env / *.key / *.pem …） | PASS | 0 命中 |
| S6 | 密钥形态（长得像密钥的文本） | PASS | 0 命中 |
| S7 | 作者机器的绝对路径 | PASS | 0 命中 |
| S8 | 每个报了数字的报告都有「口径」小节 | PASS | 14 份报告，其中 1 份无数字（天然豁免） |
| A1 | 噪声带：实测结果 vs 未完成声明 | PASS | 有实测结果 |
| A2 | Text2SQL：逐题记录 vs 未完成声明 | PASS | 有实测结果 |
| A3 | 口语化对比：结果 vs 未完成声明 | PASS | 有实测结果 |
| D1 | 根目录必需文档 | PASS | 7/7 存在 |
| D2 | 阶段 6 文档（README / RESULTS） | PASS | 已创建 |
| X10_ | experiments/10_auth_test.py | PASS | 退出码 0 |
| X11_ | experiments/11_guardrail_test.py | PASS | 退出码 0 |
| X08_ | experiments/08_mcp_smoke.py | PASS | 退出码 0 |
| X09_--check-qu | experiments/09_text2sql_eval.py --check-questions | PASS | 退出码 0 |
| X13_--check-qu | experiments/13_text2sql_colloquial.py --check-questions | PASS | 退出码 0 |
| X09_--fake_ora | experiments/09_text2sql_eval.py --fake oracle | PASS | 退出码 0 |
| X09_--fake_nai | experiments/09_text2sql_eval.py --fake naive | PASS | 退出码 0 |
| X13_--fake_ora | experiments/13_text2sql_colloquial.py --fake oracle | PASS | 退出码 0 |
| X21_--fake_sta | experiments/21_noise_band.py --fake stable | PASS | 退出码 0 |
| X21_--fake_jit | experiments/21_noise_band.py --fake jitter | PASS | 退出码 0 |
| X20_--selftest | experiments/20_pair_test.py --selftest | PASS | 退出码 0 |
| X09_ | experiments/09_text2sql_eval.py | SKIP | 本轮未重跑；盘上已有实测产物 reports/text2sql_results.v3.json |
| X13_ | experiments/13_text2sql_colloquial.py | SKIP | 本轮未重跑；盘上已有实测产物 reports/colloquial_vs_formal.json |
| X21_ | experiments/21_noise_band.py | SKIP | 本轮未重跑；盘上已有实测产物 reports/noise_band.json |
| X12_ | experiments/12_cost_report.py | PASS | 退出码 0 |
| X25_ | experiments/25_deliverable_check.py | PASS | 退出码 0 |
| P1 | python -m pytest | PASS | 退出码 0 | ............................                                             [100%] / 604 passed 〔耗时已隐去〕 〔耗时已隐去〕（引用前已隐去敏感形态与耗时读数） |
| P2 | 服务端工具注册表自检 | PASS | 一致 |
| S9 | 报告自身不带污染 | PASS | 0 命中 |

## 命中的行（若有）

命中内容已掩码，按 `文件:行号` 打开原文件看原文。

### S3 原项目的具体数字 —— 3 处

- `reports\pair_test.json:64` 命中「〔已隐去〕」
  `"sentence": "v1 与 v3 的不一致对数为 2 题（v1 独对 2 题、v3 独对 0 题），净差 +2 题，精确双尾 p = 〔已隐去〕，噪声带 1 题。净差方向为正，**超出噪声带**。p 值仍需照实报出。",`
- `reports\pair_test.md:19` 命中「〔已隐去〕」
  `| v1 | v3 | 2 | 2 | 0 | +2 | 〔已隐去〕 | 是 |`
- `reports\pair_test.md:27` 命中「〔已隐去〕」
  `- v1 与 v3 的不一致对数为 2 题（v1 独对 2 题、v3 独对 0 题），净差 +2 题，精确双尾 p = 〔已隐去〕，噪声带 1 题。净差方向为正，**超出噪声带**。p 值仍需照实报出。`

## 这张表**不能**说明什么

- 它不检查业务正确性 —— 「SQL 护栏挡住了该挡的」由 `11_guardrail_test.py` 自己的用例说话，本表只记录那条命令的退出码。
- `SKIP` 不等于 `PASS`。需要模型凭据的那几项在凭据配好之前一直是 `SKIP`，它们的实测数字**不存在**（见 `BLOCKERS.md`）。
- 静态扫描证明的是「没找到」，不是「不存在」—— 词表之外的说法它看不见。所以词表本身要有人维护，见 `DECISIONS.md` D-24。
