# 这个目录里是**假数据**渲染出来的报告模板

`colloquial_vs_formal.md` 由 `experiments/13_text2sql_colloquial.py --fake oracle`
渲染，用的是**假模型**的成绩，**不是任何真实评测的结果**。

## 为什么要留着它

报告模板（markdown 的表格、章节）只有在真跑完之后才会第一次被执行。
那时它要是有一个 `KeyError` 或者缺一个章节，丢的是一整轮**已经花钱跑出来的**结果 ——
而且是在最不该出问题的时候出问题。

所以自检会把模板**两个分支都渲染一遍**（有噪声带 / 无噪声带），
检查章节齐全、且没有禁用词。渲染的产物留在这里，是为了让"模板跑过"这件事
有据可查，而不是只留在某次终端输出里。

## 为什么放在 `reports/` 里而不是别处

放在 `cache/`（不入库）的话，`git clone` 之后这份证据就没了；
放在仓库外的话，它就不在"所有产物落盘"的范围内。
放这里，名字和这张说明牌负责让人一眼看出它是假的。

## 真的报告在哪

- `reports/colloquial_vs_formal.md` —— 真跑之后才会有
- `reports/text2sql_NOT_RUN.md` —— 没跑成时的说明（**故意不叫 results**）

同类产物：`reports/text2sql_selftest.*.json`、`reports/noise_band_selftest.*.json`、
`reports/colloquial_selftest.*.json`，它们的 `note` 字段里都写明了是假模型自检。
