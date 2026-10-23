# 手写版 vs LangGraph 版：真模型逐题对照（标签 `20261023`）

## 口径

- **脚本**：`experiments/33_impl_compare.py`（不调模型，只读下面两份逐题记录）
- **手写版 `t2sql_core.run()`**：`reports/text2sql_results.v3.20261023.json`，由 `python experiments/09_text2sql_eval.py --versions v3 --impl hand --tag 20261023` 产出
- **LangGraph 版 `t2sql_graph.run()`**：`reports/text2sql_results.v3.graph.20261023.json`，由 `python experiments/09_text2sql_eval.py --versions v3 --impl graph --tag 20261023` 产出
- **模型**：`deepseek-chat`；**端点**：`https://api.deepseek.com/v1`；两版同一天先后各跑一遍，提示词版本都是 v3
- **题集**：`data/eval/questions.json`，36 题 = 32 题可答 + 4 题本来就答不了
- **可答题完成数**：严格 EX 判对的可答题数 / 32（判分见 `mcp_server/eval_grading.py`）
- **不可答题拒答数**：4 道不可答题里模型老实说「答不了」的题数
- **调用次数 / token**：逐题 `usage` 累加，重写重试也算；「平均」= 总数 / 36 题。不含 09 开跑前那 1 次连通性检查
- **翻转**：同一题两版的判定不同（可答题看严格 EX，不可答题看是否说了答不了；与 `21_noise_band.py` 同一口径）
- **两版控制流等价**是测试结论（`tests/test_t2sql_state_machine.py` 的两版对照用例），所以这里的任何差异都只能归到模型输出的不确定性上
- **表里的 SQL**：成功的题是护栏处理后实际执行的那条 —— 模型没写 `LIMIT` 的，末尾的 `LIMIT 200` 是护栏补的，不是模型写的

## 汇总

| 实现 | 可答题完成（严格 EX） | 不可答题拒答 | 调用次数（总 / 平均每题） | prompt tokens（总 / 平均） | completion tokens（总 / 平均） | 调用层错误 |
|---|---|---|---|---|---|---|
| 手写版 `t2sql_core.run()` | 31/32 | 4/4 | 36 / 1.00 | 31296 / 869.33 | 673 / 18.69 | 0 |
| LangGraph 版 `t2sql_graph.run()` | 31/32 | 4/4 | 36 / 1.00 | 31296 / 869.33 | 679 / 18.86 | 0 |

## 两版逐题对照

- **判定翻转**：0 道
- **判定相同、但模型写出的 SQL 文本不同**：3 道（q05, q17, q21）
- **两版都没判对**：1 道（q16）

| 题号 | 类别 | 手写版：判定 / SQL | LangGraph 版：判定 / SQL | 判分说明（手写版 ‖ LangGraph 版） |
|---|---|---|---|---|
| q05 | 单表计数 | 对 / `SELECT COUNT(*) AS genre_count FROM Genre LIMIT 200` | 对 / `SELECT COUNT(*) FROM Genre LIMIT 200` | — ‖ — |
| q17 | 排序限量 | 对 / `SELECT GenreId, COUNT(*) AS track_count FROM Track WHERE GenreId IS NOT NULL GROUP BY GenreId ORDER BY track_count DESC, GenreId LIMIT 3` | 对 / `SELECT GenreId, COUNT(*) AS TrackCount FROM Track WHERE GenreId IS NOT NULL GROUP BY GenreId ORDER BY TrackCount DESC LIMIT 3` | — ‖ — |
| q21 | 连接 | 对 / `SELECT COUNT(*) AS AlbumCount FROM Album a JOIN Artist ar ON a.ArtistId = ar.ArtistId WHERE ar.Name = 'AC/DC' LIMIT 200` | 对 / `SELECT COUNT(*) AS AlbumCount FROM Album JOIN Artist ON Album.ArtistId = Artist.ArtistId WHERE Artist.Name = 'AC/DC' LIMIT 200` | — ‖ — |
| q16 | 排序限量 | 错 / `SELECT InvoiceId, Total FROM Invoice ORDER BY Total DESC LIMIT 5` | 错 / `SELECT InvoiceId, Total FROM Invoice ORDER BY Total DESC LIMIT 5` | 列数不同：模型 2 列，参考 1 列 ‖ 列数不同：模型 2 列，参考 1 列 |

## 和噪声带比

`reports/noise_band.json`（`experiments/21_noise_band.py`）：同一臂（v3）在 temperature=0 下连跑两遍，翻转 **1 题**。那一轮是早先测的，模型与本次不一定相同，只作参照。

本次两版之间翻转 **0 题**，落在噪声带之内。

结论的写法：两版完成率之差若落在噪声带之内，只能写「方向为 X，未达可判定水平」；两版控制流等价，所以这份对照**不能**被读成「哪一版更强」，它验证的是迁移没有改变行为。
