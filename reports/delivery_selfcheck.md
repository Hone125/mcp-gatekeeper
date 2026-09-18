# 交付级自检（15 项：仓库 + 交付物 + 远端）

## 口径

- **脚本**：`mcp_server/delivery_selfcheck.py`；本次由 `experiments/28_delivery_selfcheck.py` 驱动（联网模式）
- **每行的「违规数」都是实测的**：大部分数的是扫描出来的命中行数；第 6、8、14 项数的是**违规条数**（判据见各节）；第 11、12 项看的是子进程退出码；第 15 项看的是历史版本里的命中条数 —— **那一项 > 0 才算过**，理由见该节
- **PASS 的含义**：违规数为 0，或退出码为 0。`ERROR` 表示这一项**自己**出错了（命令没跑起来、没取到）—— **不算通过**
- **`SKIP` 的含义**：这一项**没有实测依据**，既不是通过也不是失败。来源只有两种：交付物不在本机（没设 `DELIVERY_RESUME_DIR`），或者断网
- **命中行是掩码之后才写进来的**：逐字引用一处命中会让报告自己成为新的载体（`DECISIONS.md` D-30）
- **为什么交付物那一半要判 SKIP 而不是干脆不写**：不写的话，「交付物干净」这句话在报告上**没有任何痕迹**；判 SKIP 才是实话 —— 本机确实没查。交出报告的人自己清楚差哪一项

**合计**：15 项 —— PASS 15 / FAIL 0 / SKIP 0 / ERROR 0

## 明细

| # | 检查项 | 判定 | 违规数 | 命令 |
|---|---|---|---|---|
| 1 | 仓库里没有识别性名称 | PASS | 0 | `sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL)` |
| 2 | 仓库里没有那些不该出现的数字 | PASS | 0 | `sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, word_boundary=True)` |
| 3 | 自己写的文件里没有领域术语 | PASS | 0 | `sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED)` |
| 4 | 公网 raw 上的同一份文件同样干净 | PASS | 0 | `curl.exe -s <raw url>/mcp_server/selfcheck.py` |
| 5 | 交付物里没有仓库标识与旧数字 | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 6 | 工具数是 6 个（不是 8） | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 7 | 时间线不矛盾：实习那一节里没有 MCP | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 8 | MCP 条的 facts 链接指向本仓 | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 9 | 交付物里没有金额字样 | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 10 | 交付物里没有夸大措辞 | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 HTML` |
| 11 | 守门链四项全过 | PASS | 0 | `python tools/check.py` |
| 12 | 单元测试退出码 0 | PASS | 0 | `python -m pytest -q` |
| 13 | 远端 main 的 sha == 本地 HEAD | PASS | 0 | `GET api.github.com/repos/.../commits/main` |
| 14 | PDF 是 2 页 | PASS | 0 | `扫 DELIVERY_RESUME_DIR 下的 PDF` |
| 15 | 历史残留已实测、且未执行任何重写 | PASS | 0 | `喂一段已知含词的样本给同一把尺子 + git show <仓外留档里的那个编号>:mcp_server/selfcheck.py` |

## 每项的判据与实测

### 1 仓库里没有识别性名称 —— PASS（违规 0）

- **判据**：扫全仓所有文本文件，比对词表第一张表（`selfcheck.FORBIDDEN_NAMES`）；命中 0 才算过。**含**本次修复前的自检脚本自己 —— 它已不在豁免区
- **命令**：`sc.grep(sc.FORBIDDEN_NAMES, sc.SCOPE_ALL)`
- **违规明细**：无

### 2 仓库里没有那些不该出现的数字 —— PASS（违规 0）

- **判据**：扫全仓比对词表第三张表（`FORBIDDEN_NUMBERS`），带词边界（`(?<![0-9A-Za-z_.])数字(?![0-9A-Za-z_])`），**已核验巧合除外**（那些是真实统计值，逐条列在报告里而不是抹掉）
- **命令**：`sc.grep(sc.FORBIDDEN_NUMBERS, sc.SCOPE_NO_VERBATIM, word_boundary=True)`
- **备注**：另有 3 处已核验巧合（登记在 `VERIFIED_COINCIDENCES`，只放行到具体路径）：`reports\pair_test.json:64`；`reports\pair_test.md:19`；`reports\pair_test.md:27`
- **违规明细**：无

### 3 自己写的文件里没有领域术语 —— PASS（违规 0）

- **判据**：只扫**本仓作者写的**文件（`sc.SCOPE_AUTHORED`，第三方原文与语料不算），比对词表第二张表（`MEDICAL_TERMS`）
- **命令**：`sc.grep(sc.MEDICAL_TERMS, sc.SCOPE_AUTHORED)`
- **违规明细**：无

### 4 公网 raw 上的同一份文件同样干净 —— PASS（违规 0）

- **判据**：把远端 `main` 上的 `mcp_server/selfcheck.py` 原样拉下来，用**同一套**词表扫描 —— 本地干净不等于公网干净，这一项就是那两者的差值
- **命令**：`curl.exe -s <raw url>/mcp_server/selfcheck.py`
- **备注**：curl.exe（HTTP 200）
- **违规明细**：无

### 5 交付物里没有仓库标识与旧数字 —— PASS（违规 0）

- **判据**：扫交付物 HTML，比对**同一份词表**里划给交付物的那张名字表（仓库标识）与全部旧数字（子串匹配，不额外开词边界），外加两个只在交付物里出现过的说法
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **备注**：数字那一类不单独开词边界之上的豁免：交付物**一处都不该有**，所以这里比仓库那一项更严（仓库那边有已核验巧合）；雇主名与内部系统名那几条不在名单里 —— 简历不写雇主就没法核实，那几条属于「仓库里不许有」，不适用于交付物
- **违规明细**：无

### 6 工具数是 6 个（不是 8） —— PASS（违规 0）

- **判据**：正则抓出交付物里所有 `<数字> 个工具` 的说法，**每一个**都必须是 6 —— 出现别的数字、或者一个都没有，都算违规
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **备注**：抓到的写法：['6']
- **违规明细**：无

### 7 时间线不矛盾：实习那一节里没有 MCP —— PASS（违规 0）

- **判据**：交付物「实习经历」一节（含技术栈行）里一处 `MCP` 都不许有 —— 实习 2026.04-05、MCP 条 2026.06，写了就是时间线自相矛盾
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **违规明细**：无

### 8 MCP 条的 facts 链接指向本仓 —— PASS（违规 0）

- **判据**：MCP 那一条里必须出现**本仓地址**（**带不带协议头都算**），且**那一条**里不许出现指向另一个仓库的 github 地址（别的条目的仓库链接不管 —— 那是另一个项目）。本仓地址由 `mcp_server/paths.py` 现算（环境变量 → `origin` → 仓外留档），**这里不写死**：写死的话换地址要改两处，漏一处的表现是「检查照跑、只是查的是另一个仓库」
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **备注**：https://github.com/Hone125/mcp-gatekeeper 在 MCP 条里出现 1 次
- **违规明细**：无

### 9 交付物里没有金额字样 —— PASS（违规 0）

- **判据**：扫交付物 HTML 里的 `成本` / `¥` / `花费` —— 这一批交付的数字全都不含单价，所以一个字都不该出现（**金额栏是空的，不等于免费**，见 D-25）
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **违规明细**：无

### 10 交付物里没有夸大措辞 —— PASS（违规 0）

- **判据**：扫交付物 HTML 比对 `selfcheck.OVERSTATED`（6 个词，与仓库那一项**同一份**）+ 本次另外拉黑的 4 个（掉点 / 完全 / 彻底 / 零风险 —— 它们不是仓库红线，是这一批的措辞纪律）
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 HTML`
- **备注**：这条**放宽不了**：可用措辞是「方向为 X，未达可判定水平」，写在 `DECISIONS.md` D-24
- **违规明细**：无

### 11 守门链四项全过 —— PASS（违规 0）

- **判据**：**不重新跑闸门**（本模块就是被闸门调用的，跑自己会无限套娃）。改为由四步的退出码推得：闸门返回什么，只看 `tools/check.py` 里那句 `first_bad` —— 四步全是 0 时 `first_bad` 恒为 0，所以闸门必然返回 0。四步各自的实测在第 12 项、本项上方第 8/9 项与 `reports/deliverable_check.md`
- **命令**：`python tools/check.py`
- **备注**：四步退出码：1=OK / 2=OK / 3=OK / 4=OK；pytest=0
- **违规明细**：无

### 12 单元测试退出码 0 —— PASS（违规 0）

- **判据**：跑 `python -m pytest -q`，只看退出码。**条数变化必须逐条说明**（新增用例写清为什么加、删的写清为什么删），所以条数也一并记在这里
- **命令**：`python -m pytest -q`
- **备注**：601 条通过；条数变化的逐条说明见 PROGRESS.md
- **违规明细**：无

### 13 远端 main 的 sha == 本地 HEAD —— PASS（违规 0）

- **判据**：用 GitHub API 读远端 `main` 的 sha，与本地 `git rev-parse HEAD` 比对 —— 「我推上去了」这句话的机械判据。**不是**比对时间戳：时钟不可复现（D-38）
- **命令**：`GET api.github.com/repos/.../commits/main`
- **备注**：本地指纹 511e067dcf8f / 远端指纹 511e067dcf8f（**为什么不写提交号**：见 `selfcheck.sha_fingerprint`）
- **违规明细**：无

### 14 PDF 是 2 页 —— PASS（违规 0）

- **判据**：在交付物 PDF 里数 `/Type /Page` 对象（`/Type /Pages` 是目录节点，用负向断言排除）。**页数是硬指标**：这一版要求 2 页，多出一页说明版式被撑破了
- **命令**：`扫 DELIVERY_RESUME_DIR 下的 PDF`
- **备注**：实测 2 页
- **违规明细**：无

### 15 历史残留已实测、且未执行任何重写 —— PASS（违规 0）

- **判据**：两条腿一起看：**甲**把一段已知含词的合成样本喂给同一把尺子（必须报红 —— 否则上面那几个 0 只说明尺子坏了）；**乙**用 `git show <仓外留档里的那个编号>:mcp_server/selfcheck.py` 取回历史版本，用同一套词表扫描，**命中数 > 0 才算过**（那正是残留仍在、历史未被重写的证据）。本机没有那段旧历史时乙判「没有实测依据」。**本项不执行任何重写**，只读
- **命令**：`喂一段已知含词的样本给同一把尺子 + git show <仓外留档里的那个编号>:mcp_server/selfcheck.py`
- **备注**：合成负控通过（S1（识别性名称）命中 3 条 / 出现 3 次；S2（领域术语）命中 1 条 / 出现 1 次；S3（原项目数字）命中 1 条 / 出现 1 次）；`旧编号·F` 那一版实测：**命中 50 条 / 出现 88 次** ——命中 > 0 才是「残留仍在、历史未被重写」的证据。本项**只读**，没有执行任何重写
- **违规明细**：无

## 这张表**不能**说明什么

- 它不评价交付物写得好不好、能不能过筛 —— 那件事没有机械判据。它只查「说好的红线有没有被越过」。
- **静态项是词表驱动的**：词表里没有的说法扫不出来。「0 命中」的确切含义是「词表里的东西一处都没有」，不是「绝对干净」。
- 判 `SKIP` 的项在这份报告里**不代表任何结论**。把 SKIP 读成通过，是这张表最容易被误用的方式，所以它单列一栏。
