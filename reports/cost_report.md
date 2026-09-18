# 用量与成本

## 口径

- **脚本**：`experiments/12_cost_report.py`（只读账本，**不调用模型**）
- **账本**：`reports/usage_ledger.jsonl`，JSONL，一行 = 一次模型调用
- **调用次数 / token**：实测值，来自模型返回的 usage；**重写重试的各占一行、各算一次**
- **金额**：算出来的，需要单价。单价必须有出处（价目页 URL + 抓取日期），否则金额栏留 `—` 并写明原因 —— **不用别家单价兜底，也不填 0**
- **单价来源**：未配置 —— **金额留空**（本仓库不带内置单价）
- **`source` 是什么**：账本按 (`source`, 模型) 汇总，而 `source` 记的是**提示词版本标签**（`v1` / `v2` / `v3`），**不是链路名**。
  ★ 这是**已知边界**，照实写在这里：口语对照的那一轮与噪声带的两轮，调用都记在 `v3` 名下 —— 账本自己分不出它们，只能看出「v3 这个名字下花了多少次」。原因见 `RESULTS.md` 第四节。
- **不在账本里的**：假模型（`--fake`）的用量。评测脚本传 `record_cost=False`，自检不产生成本

## 汇总

| 来源标签 | 模型 | 调用次数 | 输入 token | 输出 token | 金额 | 单价口径 |
|---|---|---|---|---|---|---|
| t2sql:v3 | deepseek-ai/DeepSeek-V3 | 125 | 108448 | 2239 | — | 无单价条目 |
| t2sql:v1 | deepseek-ai/DeepSeek-V3 | 36 | 28344 | 947 | — | 无单价条目 |
| t2sql:v2 | deepseek-ai/DeepSeek-V3 | 36 | 29028 | 911 | — | 无单价条目 |
| **合计** | — | **197** | **165820** | **4097** | — | — |

> 金额全部留空：没有可引用的单价。**这不等于免费** —— 要算金额，把 `MCP_TOOLKIT_PRICES` 指向一个带 `source` / `as_of` 的单价 JSON。

## 按来源标签分布

| 来源标签 | 调用次数 |
|---|---|
| t2sql:v3 | 125 |
| t2sql:v1 | 36 |
| t2sql:v2 | 36 |

## 逐条明细

| 时间 | 来源标签 | 模型 | 输入 token | 输出 token |
|---|---|---|---|---|
| 2026-09-18T15:39:02 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:39:51 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 782 | 6 |
| 2026-09-18T15:39:52 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 781 | 6 |
| 2026-09-18T15:39:54 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 782 | 6 |
| 2026-09-18T15:39:56 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 781 | 6 |
| 2026-09-18T15:40:04 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 780 | 6 |
| 2026-09-18T15:40:05 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 781 | 5 |
| 2026-09-18T15:40:07 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 786 | 9 |
| 2026-09-18T15:40:09 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 783 | 9 |
| 2026-09-18T15:40:12 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 792 | 45 |
| 2026-09-18T15:40:13 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 783 | 9 |
| 2026-09-18T15:40:15 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 783 | 8 |
| 2026-09-18T15:40:17 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 790 | 18 |
| 2026-09-18T15:40:19 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 794 | 20 |
| 2026-09-18T15:40:21 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 784 | 16 |
| 2026-09-18T15:40:22 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 789 | 13 |
| 2026-09-18T15:40:24 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 788 | 16 |
| 2026-09-18T15:40:27 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 796 | 56 |
| 2026-09-18T15:40:29 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 797 | 14 |
| 2026-09-18T15:40:31 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 792 | 15 |
| 2026-09-18T15:40:33 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 788 | 30 |
| 2026-09-18T15:40:36 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 788 | 23 |
| 2026-09-18T15:40:39 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 794 | 53 |
| 2026-09-18T15:40:42 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 791 | 33 |
| 2026-09-18T15:40:44 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 789 | 14 |
| 2026-09-18T15:40:46 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 793 | 27 |
| 2026-09-18T15:40:49 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 791 | 43 |
| 2026-09-18T15:40:51 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 788 | 15 |
| 2026-09-18T15:40:53 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 791 | 19 |
| 2026-09-18T15:40:55 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 789 | 20 |
| 2026-09-18T15:40:56 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 786 | 11 |
| 2026-09-18T15:40:58 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 789 | 19 |
| 2026-09-18T15:41:00 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 789 | 18 |
| 2026-09-18T15:41:04 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 785 | 83 |
| 2026-09-18T15:41:13 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 782 | 95 |
| 2026-09-18T15:41:16 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 785 | 45 |
| 2026-09-18T15:41:22 | t2sql:v1 | deepseek-ai/DeepSeek-V3 | 782 | 116 |
| 2026-09-18T15:41:23 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 801 | 6 |
| 2026-09-18T15:41:25 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 800 | 6 |
| 2026-09-18T15:41:27 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 801 | 6 |
| 2026-09-18T15:41:28 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 800 | 6 |
| 2026-09-18T15:41:29 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 799 | 6 |
| 2026-09-18T15:41:31 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 800 | 5 |
| 2026-09-18T15:41:32 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 805 | 9 |
| 2026-09-18T15:41:34 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 802 | 9 |
| 2026-09-18T15:41:36 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 811 | 45 |
| 2026-09-18T15:41:38 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 802 | 9 |
| 2026-09-18T15:41:39 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 802 | 8 |
| 2026-09-18T15:41:41 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 809 | 16 |
| 2026-09-18T15:41:44 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 813 | 18 |
| 2026-09-18T15:41:46 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 803 | 11 |
| 2026-09-18T15:41:48 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 808 | 13 |
| 2026-09-18T15:41:49 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 807 | 13 |
| 2026-09-18T15:41:53 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 815 | 56 |
| 2026-09-18T15:41:55 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 816 | 18 |
| 2026-09-18T15:41:57 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 811 | 15 |
| 2026-09-18T15:41:59 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 807 | 30 |
| 2026-09-18T15:42:01 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 807 | 23 |
| 2026-09-18T15:42:04 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 813 | 52 |
| 2026-09-18T15:42:06 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 810 | 33 |
| 2026-09-18T15:42:08 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 808 | 14 |
| 2026-09-18T15:42:09 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 812 | 27 |
| 2026-09-18T15:42:12 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 810 | 43 |
| 2026-09-18T15:42:14 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 807 | 15 |
| 2026-09-18T15:42:16 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 810 | 19 |
| 2026-09-18T15:42:24 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 808 | 20 |
| 2026-09-18T15:42:26 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 805 | 11 |
| 2026-09-18T15:42:28 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 808 | 19 |
| 2026-09-18T15:42:29 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 808 | 18 |
| 2026-09-18T15:42:32 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 804 | 76 |
| 2026-09-18T15:42:37 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 801 | 75 |
| 2026-09-18T15:42:40 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 804 | 45 |
| 2026-09-18T15:42:47 | t2sql:v2 | deepseek-ai/DeepSeek-V3 | 801 | 116 |
| 2026-09-18T15:42:49 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:42:50 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 6 |
| 2026-09-18T15:42:51 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:42:52 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 6 |
| 2026-09-18T15:42:54 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 860 | 6 |
| 2026-09-18T15:42:55 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:42:57 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 9 |
| 2026-09-18T15:42:58 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 9 |
| 2026-09-18T15:43:01 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 41 |
| 2026-09-18T15:43:03 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:43:04 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:43:06 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 870 | 15 |
| 2026-09-18T15:43:08 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 45 |
| 2026-09-18T15:43:10 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 864 | 15 |
| 2026-09-18T15:43:11 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 13 |
| 2026-09-18T15:43:13 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 11 |
| 2026-09-18T15:43:16 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 876 | 56 |
| 2026-09-18T15:43:17 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 877 | 14 |
| 2026-09-18T15:43:19 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 15 |
| 2026-09-18T15:43:22 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 30 |
| 2026-09-18T15:43:24 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 23 |
| 2026-09-18T15:43:27 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 46 |
| 2026-09-18T15:43:30 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 32 |
| 2026-09-18T15:43:32 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 14 |
| 2026-09-18T15:43:34 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 873 | 21 |
| 2026-09-18T15:43:36 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 36 |
| 2026-09-18T15:43:38 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 15 |
| 2026-09-18T15:43:40 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 19 |
| 2026-09-18T15:43:42 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 20 |
| 2026-09-18T15:43:44 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 27 |
| 2026-09-18T15:43:47 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 21 |
| 2026-09-18T15:43:49 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 18 |
| 2026-09-18T15:43:51 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:43:52 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 12 |
| 2026-09-18T15:43:57 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:43:59 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 14 |
| 2026-09-18T15:47:49 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 864 | 8 |
| 2026-09-18T15:47:51 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 6 |
| 2026-09-18T15:47:52 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 864 | 5 |
| 2026-09-18T15:47:54 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 870 | 9 |
| 2026-09-18T15:47:56 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 13 |
| 2026-09-18T15:47:57 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 867 | 9 |
| 2026-09-18T15:47:59 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 26 |
| 2026-09-18T15:48:01 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 14 |
| 2026-09-18T15:48:03 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 21 |
| 2026-09-18T15:48:04 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 19 |
| 2026-09-18T15:48:07 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 24 |
| 2026-09-18T15:48:09 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 15 |
| 2026-09-18T15:48:11 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 870 | 21 |
| 2026-09-18T15:48:13 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 14 |
| 2026-09-18T15:48:15 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 13 |
| 2026-09-18T15:48:34 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:48:36 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:48:37 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:48:38 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:48:39 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 860 | 6 |
| 2026-09-18T15:48:41 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:48:42 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 9 |
| 2026-09-18T15:48:44 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 9 |
| 2026-09-18T15:48:46 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 41 |
| 2026-09-18T15:48:47 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:48:48 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:48:50 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 870 | 15 |
| 2026-09-18T15:48:52 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 45 |
| 2026-09-18T15:48:53 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 864 | 15 |
| 2026-09-18T15:48:55 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 13 |
| 2026-09-18T15:48:57 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 16 |
| 2026-09-18T15:49:00 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 876 | 56 |
| 2026-09-18T15:49:01 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 877 | 15 |
| 2026-09-18T15:49:03 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 15 |
| 2026-09-18T15:49:06 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 30 |
| 2026-09-18T15:49:07 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 23 |
| 2026-09-18T15:49:13 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 46 |
| 2026-09-18T15:49:15 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 32 |
| 2026-09-18T15:49:17 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 14 |
| 2026-09-18T15:49:18 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 873 | 21 |
| 2026-09-18T15:49:21 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 19 |
| 2026-09-18T15:49:23 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 929 | 20 |
| 2026-09-18T15:49:24 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 15 |
| 2026-09-18T15:49:27 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 36 |
| 2026-09-18T15:49:29 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 20 |
| 2026-09-18T15:49:31 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 27 |
| 2026-09-18T15:49:32 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 21 |
| 2026-09-18T15:49:34 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 18 |
| 2026-09-18T15:49:38 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:49:42 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 12 |
| 2026-09-18T15:49:44 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:49:46 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 12 |
| 2026-09-18T15:49:47 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:49:48 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:49:50 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 6 |
| 2026-09-18T15:49:51 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 6 |
| 2026-09-18T15:49:53 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 860 | 6 |
| 2026-09-18T15:49:55 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 861 | 5 |
| 2026-09-18T15:49:56 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 8 |
| 2026-09-18T15:49:58 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 9 |
| 2026-09-18T15:50:01 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 41 |
| 2026-09-18T15:50:02 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:50:04 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 863 | 8 |
| 2026-09-18T15:50:06 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 870 | 15 |
| 2026-09-18T15:50:08 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 45 |
| 2026-09-18T15:50:10 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 864 | 11 |
| 2026-09-18T15:50:11 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 13 |
| 2026-09-18T15:50:13 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 16 |
| 2026-09-18T15:50:17 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 876 | 56 |
| 2026-09-18T15:50:19 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 877 | 18 |
| 2026-09-18T15:50:23 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 872 | 15 |
| 2026-09-18T15:50:25 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 30 |
| 2026-09-18T15:50:27 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 23 |
| 2026-09-18T15:50:29 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 874 | 46 |
| 2026-09-18T15:50:32 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 32 |
| 2026-09-18T15:50:34 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 14 |
| 2026-09-18T15:50:36 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 873 | 18 |
| 2026-09-18T15:50:38 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 36 |
| 2026-09-18T15:50:40 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 868 | 15 |
| 2026-09-18T15:50:42 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 871 | 21 |
| 2026-09-18T15:50:44 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 20 |
| 2026-09-18T15:50:46 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 866 | 27 |
| 2026-09-18T15:50:48 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 21 |
| 2026-09-18T15:50:50 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 869 | 18 |
| 2026-09-18T15:50:53 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:50:54 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 12 |
| 2026-09-18T15:50:56 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 865 | 11 |
| 2026-09-18T15:51:12 | t2sql:v3 | deepseek-ai/DeepSeek-V3 | 862 | 14 |
