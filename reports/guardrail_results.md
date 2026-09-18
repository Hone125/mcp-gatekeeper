# SQL 护栏负例矩阵（实测结果）

本文件由 `experiments/11_guardrail_test.py` 生成，**不要手改**；重跑该脚本即可复现。

## 口径

- **一条用例** = 一次真实子进程调用 `python -m mcp_server.guardrails "<SQL>"`。
- **退出码由操作系统给出**，不是我在进程内打印的一句话。取值：`0` 放行且执行成功 / `1` 放行但执行失败 / `2` 被护栏拦下 / `3` 数据未就绪 / `4` 用法错误。
- **通过** = 退出码与错误码**两个都对**。不接受「反正拒了」：拦下必须报出**期望的那条规则号**，否则「L1 拦的」和「L3 拦的」就分不开了。
- 证据强度：子进程退出码 > 进程内返回值。后者是我自己写的字符串。
- 运行方式：`python experiments/11_guardrail_test.py`（退出码 0 = 全过）

## 总览

| 项 | 通过 / 总数 |
|---|---|
| L1 静态校验（含应拒与应放行） | 34 / 34 |
| L3 引擎级 + 执行三态 | 4 / 4 |
| 逐层隔离（每层单独有效的证据） | 3 / 3 |
| **合计** | **41 / 41** |

## 一、L1 静态校验：应拒用例

| # | 用例 | SQL | 期望 | 实测 | 退出码 | 结果 |
|---|---|---|---|---|---|---|
| 1 | 只有注释，没有语句 | `-- 只留注释` | 拦下（R1_EMPTY） | 拦下（R1_EMPTY） | `2` | ✅ |
| 2 | 只有两个连字符 | `--` | 拦下（R1_EMPTY） | 拦下（R1_EMPTY） | `2` | ✅ |
| 3 | 两条 SELECT 用分号连起来 | `SELECT 1; SELECT 2` | 拦下（R2_MULTI_STATEMENT） | 拦下（R2_MULTI_STATEMENT） | `2` | ✅ |
| 4 | 结尾多写一个分号 | `SELECT 1;;` | 拦下（R2_MULTI_STATEMENT） | 拦下（R2_MULTI_STATEMENT） | `2` | ✅ |
| 5 | DROP TABLE | `DROP TABLE Genre` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 6 | INSERT | `INSERT INTO Genre (GenreId, Name) VALUES (99, 'x')` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 7 | UPDATE | `UPDATE Album SET Title = 'x'` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 8 | DELETE | `DELETE FROM Album` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 9 | PRAGMA 语句 | `PRAGMA table_info(Track)` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 10 | ATTACH 挂载别的库 | `ATTACH DATABASE 'evil.db' AS e` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 11 | CREATE TABLE | `CREATE TABLE t (a)` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 12 | ALTER TABLE | `ALTER TABLE Album ADD COLUMN z` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 13 | VACUUM | `VACUUM` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 14 | REINDEX | `REINDEX` | 拦下（R4_FORBIDDEN_KEYWORD） | 拦下（R4_FORBIDDEN_KEYWORD） | `2` | ✅ |
| 15 | 读 sqlite_master | `SELECT name FROM sqlite_master` | 拦下（R5_INTERNAL_TABLE） | 拦下（R5_INTERNAL_TABLE） | `2` | ✅ |
| 16 | 读 sqlite_schema | `SELECT * FROM sqlite_schema` | 拦下（R5_INTERNAL_TABLE） | 拦下（R5_INTERNAL_TABLE） | `2` | ✅ |
| 17 | 内部表名写成大写 | `SELECT name FROM SQLITE_MASTER` | 拦下（R5_INTERNAL_TABLE） | 拦下（R5_INTERNAL_TABLE） | `2` | ✅ |
| 18 | 以 VALUES 开头 | `VALUES (1)` | 拦下（R3_NOT_SELECT） | 拦下（R3_NOT_SELECT） | `2` | ✅ |
| 19 | 以 EXPLAIN 开头 | `EXPLAIN QUERY PLAN SELECT 1 FROM Album` | 拦下（R3_NOT_SELECT） | 拦下（R3_NOT_SELECT） | `2` | ✅ |
| 20 | LIMIT 超过上限 | `SELECT * FROM Album LIMIT 201` | 拦下（R6_LIMIT_TOO_LARGE） | 拦下（R6_LIMIT_TOO_LARGE） | `2` | ✅ |
| 21 | LIMIT 大得离谱 | `SELECT * FROM Album LIMIT 5000` | 拦下（R6_LIMIT_TOO_LARGE） | 拦下（R6_LIMIT_TOO_LARGE） | `2` | ✅ |
| 22 | LIMIT a,b 形式，第二个数超限 | `SELECT * FROM Album LIMIT 10, 5000` | 拦下（R6_LIMIT_TOO_LARGE） | 拦下（R6_LIMIT_TOO_LARGE） | `2` | ✅ |
| 23 | LIMIT 后面是子查询 | `SELECT * FROM Album LIMIT (SELECT 1)` | 拦下（R7_LIMIT_NOT_LITERAL） | 拦下（R7_LIMIT_NOT_LITERAL） | `2` | ✅ |
| 24 | LIMIT 后面是占位符 | `SELECT * FROM Album LIMIT ?` | 拦下（R7_LIMIT_NOT_LITERAL） | 拦下（R7_LIMIT_NOT_LITERAL） | `2` | ✅ |

## 二、L1 静态校验：应放行用例（不能误杀）

只测「该拒的都拒了」是一种假安全 —— 把关键字表调到拒掉一切，那组用例照样全绿。下面这些**必须放行**。

| # | 用例 | SQL | 期望 | 实测 | 退出码 | 结果 |
|---|---|---|---|---|---|---|
| 1 | 最普通的查询，缺 LIMIT | `SELECT Title FROM Album` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 2 | 常量查询 | `SELECT 1` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 3 | WITH 开头 | `WITH x AS (SELECT 1 AS n) SELECT n FROM x` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 4 | LIMIT 在上限之内 | `SELECT * FROM Album LIMIT 50` | 放行 | 放行，50 行 | `0` | ✅ |
| 5 | LIMIT a,b 两个数都在上限内 | `SELECT * FROM Album LIMIT 10, 50` | 放行 | 放行，50 行 | `0` | ✅ |
| 6 | 分行写、带块注释 | `SELECT /* 只取标题 */ Title FROM Album LIMIT 5` | 放行 | 放行，5 行 | `0` | ✅ |
| 7 | 字符串字面量里有分号 | `SELECT 'a;b'` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 8 | 字符串字面量里有 -- | `SELECT * FROM Album WHERE Title = 'a--b'` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 9 | 字符串字面量里有 DROP | `SELECT 'DROP TABLE x'` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |
| 10 | 调用名为 replace 的标量函数 | `SELECT replace(Title, 'a', 'b') FROM Album` | 改写（补 LIMIT，非拒绝） | 放行，补 LIMIT=True | `0` | ✅ |

## 三、L3 引擎级，以及「三态」

`blocked` 与 `error` 是两回事：前者是护栏拦下的，后者是护栏放行、SQL 自己执行失败。状态机对两者的处理不同 —— 被拦下要改**写法**，执行失败要改**语义**。混成一个「失败」会让上游只能瞎猜。

| # | 用例 | SQL | 期望 | 实测 | 退出码 | 结果 |
|---|---|---|---|---|---|---|
| 1 | 用表值函数读 pragma（L1 放行、L3 拦下） | `SELECT * FROM pragma_table_info('Track')` | 拦下（R8_ENGINE_DENY） | 拦下（R8_ENGINE_DENY） | `2` | ✅ |
| 2 | 把 pragma 表值函数包进 CTE | `WITH x AS (SELECT * FROM pragma_table_info('Track')…` | 拦下（R8_ENGINE_DENY） | 拦下（R8_ENGINE_DENY） | `2` | ✅ |
| 3 | 正常查询不受 L3 影响 | `SELECT Name FROM Track LIMIT 5` | 放行 | 放行，5 行 | `0` | ✅ |
| 4 | 查询一个不存在的表 | `SELECT * FROM NoSuchTable` | 放行但执行失败 | 放行后执行失败（OperationalError: no such table: NoSuchTable） | `1` | ✅ |

## 四、逐层隔离：每一层单独有效的证据

只报「这些 SQL 都被拒了」是不够的：三层里只要有一层在干活，负例就全是绿的，另外两层是不是死的完全看不出来。下面三条为每一层单独构造了「只有这一层能拦住 / 只有这一层会放行」的场景。

| # | 用例 | SQL | 期望 | 实测 | 退出码 | 结果 |
|---|---|---|---|---|---|---|
| 1 | L1 确实放行了 pragma 那句（对照组） | `SELECT * FROM pragma_table_info('Track')` | 放行 | 放行，9 行 | `0` | ✅ |
| 2 | L2 单独挡住写（绕过 L1 与 L3） | `INSERT INTO Genre (GenreId, Name) VALUES (99, 'zzz')` | 放行但执行失败 | 放行后执行失败（OperationalError: attempt to write a readonly database） | `1` | ✅ |
| 3 | L2 仍允许读（--raw 不是一刀切全拒） | `SELECT COUNT(*) FROM Genre` | 放行 | 放行，1 行 | `0` | ✅ |

> **`--raw` 那次写有没有真的写进去**：全程开始前 `Genre` 有 25 行，跑完之后 25 行。`mode=ro` 连接下 SQLite 在文件层面就拒绝写，所以那条 INSERT 报的是 `attempt to write a readonly database`，磁盘上一个字节都没变。
> 这里**不拿「报错了」当证据**，拿的是前后行数对比 —— 报错信息也可能是别的原因。

## 五、每一条的取舍说明

下表是上面各用例的设计理由，主要是**已知的误杀**和**刻意不拦的东西**。两种情况都照实写：一个 SQL 护栏如果声称自己零误杀，那它多半是没测过。

| 用例 | 为什么这么设计 |
|---|---|
| 只有注释，没有语句 | 注释剥掉之后什么都不剩。空语句交给 SQLite 会得到一句含糊的错误，提前报更清楚。 |
| 两条 SELECT 用分号连起来 | 多语句是注入的经典载体：第一条合法、第二条干别的。 |
| 结尾多写一个分号 | 刻意的：只去掉**一个**结尾分号，所以 `;;` 还剩一个，被拦下。用 rstrip(';') 会把 `;;;;` 全吃掉，这条就混过去了。 |
| DELETE | 它同时满足 R3（不以 SELECT 开头）与 R4。报 R4 信息量更大，所以 R4 排在前面。 |
| ATTACH 挂载别的库 | 挂上另一个库是「把数据搬出去」的常见第一步，必须拦。 |
| 内部表名写成大写 | 大小写不敏感，`SQLITE_MASTER` 一样拦。 |
| 以 EXPLAIN 开头 | ⚠️ 这是**已知的误杀**：EXPLAIN 本身是只读的，被 R3 拦了。取舍是「宁可误杀不可放过」——放行 EXPLAIN 需要为它单开一条规则，而它在正常使用中不出现。照实记录，不假装它不存在。 |
| LIMIT a,b 形式，第二个数超限 | SQLite 里 `LIMIT a, b` 是「跳过 a 行、取 b 行」，有效上限是**第二个**数。只看第一个数会漏掉这条。 |
| LIMIT 后面是子查询 | ⚠️ 这是**已知的误杀**：`LIMIT (SELECT 1)` 其实是安全的，但它是动态上限，静态校验判不了。同上，保守优先。 |
| 最普通的查询，缺 LIMIT | 缺 LIMIT 是**改写**（补 `LIMIT 200` 并在返回体标 `limit_injected`），不是拒绝。这条规则防的是内存被打满，不是安全；拒绝会让上游为一句话反复重写、白烧调用次数。 |
| WITH 开头 | WITH 也放行，否则所有 CTE 写法都用不了。 |
| 字符串字面量里有分号 | ★ 分号在**字面量**里，不是语句分隔符。扫描阶段会给字面量内容打码，所以不触发 R2。用正则 `;` 直接查会把这条合法查询拦掉。 |
| 字符串字面量里有 -- | ★ 引号里的 `--` 不是注释。用 `re.sub(r'--[^\n]*')` 清洗会把这句话从 `'a` 处截断，变成一个语法错误的查询 —— 净化反而弄坏合法输入。 |
| 字符串字面量里有 DROP | ★ 字面量被打码，所以关键字扫描看不到它。 |
| 调用名为 replace 的标量函数 | ★ `REPLACE` **刻意不在**关键字表里：它同时是标量函数，加进去会让这条合法查询被误杀。而 `REPLACE INTO ...` 不以 SELECT 开头，已由 R3 拦下，所以去掉它不降低防护强度。 |
| 用表值函数读 pragma（L1 放行、L3 拦下） | ★ 这句话不含 `PRAGMA` 关键字（`pragma_table_info` 里后面跟的是下划线，`\bPRAGMA\b` 匹配不上），不以写关键字开头，也不含 `sqlite_` —— **L1 完全放行**。它被执行时才以 SQLITE_PRAGMA 动作回调 authorizer，被 L3 拒掉。这是「L3 不是装饰」的实证。下面的对照用例给出另一半证据。 |
| 把 pragma 表值函数包进 CTE | 换个写法一样拦 —— 拦的是动作，不是文本。 |
| 正常查询不受 L3 影响 | ★ 反向证据：L3 挂了回调之后普通查询照样跑。authorizer 最容易写错的地方就是顺手把 SQLITE_SELECT 也拒了，那样护栏会变成一堵不分敌我的墙。 |
| 查询一个不存在的表 | ★ 三态里的第三态：护栏**放行**了（`blocked=False`），是 SQL 本身执行失败。它必须和「被护栏拦下」分开报 —— 状态机对两者的处理不同：被拦下要改**写法**，执行失败要改**语义**（列名/表名写错了）。 |
| L1 确实放行了 pragma 那句（对照组） | ★ **上一条的对照组**。同一条 SQL，只把 L3 关掉，它就成功了 —— 这证明拦下它的确实是 L3，而不是「L1 其实也拦了、只是错误码写成了 R8」。没有这一条，L3 是不是在干活就无法判断。 |
| L2 单独挡住写（绕过 L1 与 L3） | ★ 用 `--raw` 同时跳过 L1 与 L3，只剩 `mode=ro` 只读连接。写操作被文件模式挡下（readonly database），证明 L2 自己就是一道独立防线，而不是「反正 L1 已经拦了」的摆设。 |
| L2 仍允许读（--raw 不是一刀切全拒） | 反向证据：`--raw` 下读操作正常。否则上一条的「被拒」可以解释成「--raw 这个开关本身把什么都拒了」。 |

## 六、局限

- 用例是**枚举**出来的。枚举得再全也不能证明「不存在能绕过的写法」，本表证明的是「列出的这些都被挡住了」。
- L1 是**保守**的文本规则，不是语义分析：上面第五节列出了两条已知误杀。设计取向是「宁可误杀，不可放过」—— 语义正确性交给 L3（引擎自己）兜底。
- L3 挡的是**动作**。如果将来 SQLite 新增一种越权动作而不在 `_DENY_ACTIONS` 里，它不会被拦。缓解手段是 L2 的只读连接不依赖这张动作表。
- 本表不涉及**跨库**场景（`ATTACH` 被 L1 拒，所以没有可测的跨库读）。
