# 收尾自检（发布前 9 项）

## 口径

- **脚本**：`experiments/26_final_selfcheck.py`；扫描口径全部来自 `mcp_server/selfcheck.py`（**单一口径**：`19_verify.py` 与 `tests/` 用的是同一批函数）
- **每一行的「命中数」都是实测的**：第 1~7 项数的是扫描出来的命中**行**数；第 8、9 项数的是子进程退出码（0 = 通过）
- **PASS 的含义**：命中数为 0，或退出码为 0。`ERROR` 表示这一项**自己**出错了（比如命令没跑起来）—— **不算通过**
- **`SKIP` 的含义**：这一项**没有实测依据**，既不是通过也不是失败。目前只有一种来源 —— 第 1~3 项靠的三张词表不在仓库里（见 `mcp_server/selfcheck.py` 文件头），本机读不到词表时无从比对。**把「没查」写成「0 命中」是这一整套检查最想防的那种谎**，所以这里单列一个判定，并把退出码从 0 改成 5（见文件头）
- **命中行是掩码之后才写进来的**：这份报告会被仓库卫生用例扫到，逐字引用一处违规会让报告自己成为新的载体（见 `DECISIONS.md` D-30）

**合计**：9 项 —— PASS 9 / FAIL 0 / SKIP 0 / ERROR 0

## 明细

| # | 检查项 | 判定 | 命中数 | 命令 |
|---|---|---|---|---|
| 1 | 领域敏感语义 | PASS | 0 | `selfcheck.grep(MEDICAL_TERMS, SCOPE_AUTHORED)` |
| 2 | 识别性名称 | PASS | 0 | `selfcheck.grep(FORBIDDEN_NAMES, SCOPE_ALL)` |
| 3 | 不该出现的具体数字 | PASS | 0 | `selfcheck.grep(FORBIDDEN_NUMBERS, SCOPE_NO_VERBATIM, word_boundary=True) → split_coincidences()` |
| 4 | 夸大措辞 | PASS | 0 | `selfcheck.grep_prose(OVERSTATED)` |
| 5 | 密钥形态 | PASS | 0 | `selfcheck.grep_secrets()` |
| 6 | 报数字的报告都有口径小节 | PASS | 0 | `selfcheck.audit_report_numbers()` |
| 7 | 工作区里没有密钥文件 | PASS | 0 | `selfcheck.iter_secret_files()` |
| 8 | 单元测试 | PASS | 0 | `python -m pytest -q` |
| 9 | 台账式自检 | PASS | 0 | `python experiments/19_verify.py` |

## 每项的判据与实测

### 1 领域敏感语义 —— PASS（命中 0）

- **判据**：词表见 `mcp_server/selfcheck.py` 的 `MEDICAL_TERMS`（这里不逐字列出）；只扫自己写的文件 —— 公版正文里出现的那些字是文本本身，不算本仓语义。词表不在仓库里，本机读不到时本项判 `SKIP`
- **命令**：`selfcheck.grep(MEDICAL_TERMS, SCOPE_AUTHORED)`
- **命中行**：无

### 2 识别性名称 —— PASS（命中 0）

- **判据**：词表见 `selfcheck.py` 的 `FORBIDDEN_NAMES`；全仓扫描（含公版正文）：出现在哪里都是污染。词表不在仓库里，本机读不到时本项判 `SKIP`
- **命令**：`selfcheck.grep(FORBIDDEN_NAMES, SCOPE_ALL)`
- **命中行**：无

### 3 不该出现的具体数字 —— PASS（命中 0）

- **判据**：词表见 `selfcheck.py` 的 `FORBIDDEN_NUMBERS`；数字必须**整体独立**才算命中 —— 某个更长数字里的一段（比如年份中的几位）不算。自己算出来的统计量恰好等于某个被禁数字时，按 `selfcheck.VERIFIED_COINCIDENCES` 登记后不计违规，但**照旧列出来**，且单独报一个数。词表不在仓库里，本机读不到时本项判 `SKIP`
- **命令**：`selfcheck.grep(FORBIDDEN_NUMBERS, SCOPE_NO_VERBATIM, word_boundary=True) → split_coincidences()`
- **备注**：另有 3 处已核验巧合，不计违规
- **命中行**：
  - `[已核验巧合] reports\pair_test.json:64 "sentence": "v1 与 v3 的不一致对数为 2 题（v1 独对 2 题、v3 独对 0 题），净差 +2 题，精确双尾 p = 〔已隐去〕，噪声带 1 题。净差方向为正，**超出噪声带**。p 值仍需照实报出。",`
  - `[已核验巧合] reports\pair_test.md:19 | v1 | v3 | 2 | 2 | 0 | +2 | 〔已隐去〕 | 是 |`
  - `[已核验巧合] reports\pair_test.md:27 - v1 与 v3 的不一致对数为 2 题（v1 独对 2 题、v3 独对 0 题），净差 +2 题，精确双尾 p = 〔已隐去〕，噪声带 1 题。净差方向为正，**超出噪声带**。p 值仍需照实报出。`

### 4 夸大措辞 —— PASS（命中 0）

- **判据**：词表见 `selfcheck.py` 的 `OVERSTATED`（本报告自身也受该表约束，所以这里不逐字列出那几个词）；只扫**给人读的成品**，程序生成的报告另有运行时自查（`eval_runner.write_report`）
- **命令**：`selfcheck.grep_prose(OVERSTATED)`
- **命中行**：无

### 5 密钥形态 —— PASS（命中 0）

- **判据**：词表见 `selfcheck.py` 的 `SECRET_PATTERNS`；除第三方原样数据外全扫（含报告与文档），命中的串只显示前 24 个字符
- **命令**：`selfcheck.grep_secrets()`
- **命中行**：无

### 6 报数字的报告都有口径小节 —— PASS（命中 0）

- **判据**：逐份报告实测数字个数；有数字（≥1 个）却没有 `## 口径` 标题行的判不合格。没有数字的报告天然豁免 —— 豁免是按内容算出来的，不是按文件名开的白名单
- **命令**：`selfcheck.audit_report_numbers()`
- **备注**：14 份报告，其中 1 份无数字（天然豁免）
- **命中行**：无

### 7 工作区里没有密钥文件 —— PASS（命中 0）

- **判据**：全仓查找 `.env` / `*.key` / `*.pem` 等；**即使被 `.gitignore` 排除**，也不该躺在工作区里（被排除只说明它不会进 git，不说明它不存在）
- **命令**：`selfcheck.iter_secret_files()`
- **命中行**：无

### 8 单元测试 —— PASS（命中 0）

- **判据**：`python -m pytest` 的退出码：0 = 全过，非 0 = 有失败。命令跑不起来（-1）判 ERROR 而不是 FAIL —— 该修的地方不同
- **命令**：`python -m pytest -q`
- **备注**：退出码 0
- **命中行**：无

### 9 台账式自检 —— PASS（命中 0）

- **判据**：`experiments/19_verify.py` 的退出码：0 = FAIL 0 / ERROR 0。它自己会把每条结果的判定与口径写进 `reports/verify.md`
- **命令**：`python experiments/19_verify.py`
- **备注**：退出码 0
- **命中行**：无

## 这张表**不能**说明什么

- 它不判断代码写得好不好、文档够不够清楚 —— 那件事没有机械判据，硬编一个只会得到一条永远绿的假检查。它只查「说好的红线有没有被越过」。
- **静态项是词表驱动的**：词表里没有的说法扫不出来。「0 命中」的确切含义是「词表里的东西一处都没有」，不是「绝对干净」。
- 第 8、9 项的结论来自子进程退出码。命令**跑不起来**时会是 `ERROR` 而不是 `FAIL` —— 两者要修的地方不同：一个是环境，一个是仓库内容。

## 交付级自检（15 项：仓库 + 交付物 + 远端）

> 上面 9 项问的是「**这个仓库**有没有越过自己划的红线」，只看仓库。
> 这 15 项问的是「**这一批交付物整体**是否成立」—— 它的检查对象跨出了仓库：
> 交付物在仓库外面、远端在网络上、还有一项要问 git 历史。
> 两层用的是**同一批**扫描函数（单一口径），所以扫仓库的那几项必然同结论。

- **脚本**：`mcp_server/delivery_selfcheck.py`（本节的判定就是它渲染的）
- **`SKIP` 的含义**：这一项**没有实测依据** —— 交付物不在本机（没设 `DELIVERY_RESUME_DIR`）、或者本项要联网。**「没查」不许写成「0 命中」**
- **第 4、13 项要联网**，默认不跑（闸门必须能在断网机器上跑完）。带 `--online` 跑 `experiments/28_delivery_selfcheck.py` 会就地跑这两项，公网那一项的常驻实测在 `reports/leak_fix_verify.md`
- **第 11 项不重新跑闸门**（本模块被闸门调用，跑自己会套娃），判据是由四步退出码推得，推论链写在那 15 项报告的「判据」栏里
- **第 15 项的判据是「命中数 > 0 才算过」** —— 它守的是「没有在没人同意的情况下重写历史」，不是「历史很干净」。理由写在那 15 项报告里，值得读一遍

**合计**：15 项 —— PASS 13 / FAIL 0 / SKIP 2 / ERROR 0

| # | 检查项 | 判定 | 违规数 |
|---|---|---|---|
| 1 | 仓库里没有识别性名称 | PASS | 0 |
| 2 | 仓库里没有那些不该出现的数字 | PASS | 0 |
| 3 | 自己写的文件里没有领域术语 | PASS | 0 |
| 4 | 公网 raw 上的同一份文件同样干净 | SKIP | 0 |
| 5 | 交付物里没有仓库标识与旧数字 | PASS | 0 |
| 6 | 工具数是 6 个（不是 8） | PASS | 0 |
| 7 | 时间线不矛盾：实习那一节里没有 MCP | PASS | 0 |
| 8 | MCP 条的 facts 链接指向本仓 | PASS | 0 |
| 9 | 交付物里没有金额字样 | PASS | 0 |
| 10 | 交付物里没有夸大措辞 | PASS | 0 |
| 11 | 守门链四项全过 | PASS | 0 |
| 12 | 单元测试退出码 0 | PASS | 0 |
| 13 | 远端 main 的 sha == 本地 HEAD | SKIP | 0 |
| 14 | PDF 是 2 页 | PASS | 0 |
| 15 | 历史残留已实测、且未执行任何重写 | PASS | 0 |

命中行、每项的判据与命令、以及 `SKIP` 的具体理由，**将由** `experiments/28_delivery_selfcheck.py` 逐条写进 `reports/delivery_selfcheck.md` —— 那一份**生成后**才有内容（它跑得到联网那两项，闸门里这两项一定是 SKIP）。

★ 本节与那一份的**第 11 项措辞不同，不是不一致**：闸门里这一份写的是「由四步退出码推得」（本模块被闸门调用，自己跑闸门会套娃），独立那一份写的是「本项自己跑出来的」。判据栏把这件事写明了，两边合起来看才是完整的。
