# NOTICE —— 第三方数据出处与许可

这个仓库里的**代码**是自己写的（MIT，见 `LICENSE`）；**数据**不是，来源逐项列在下面。

分成两部分，因为它们的性质不同：

1. **示例数据库**：一份公开发布的示例库，用来演示 SQL 相关的能力；
2. **公版文本语料**：Project Gutenberg 上的中国古典文献，用来演示检索相关的能力。

两份数据都**随仓库分发**，所以 clone 下来不联网就能跑通。每一份都给了校验和，
你可以自己算一遍，确认拿到的东西和这里描述的是同一个东西。

## 一、示例数据库

| 项 | 值 |
|---|---|
| 项目 | [Chinook Database](https://github.com/lerocha/chinook-database) |
| 版本 | 1.4.5 |
| 文件 | `Chinook_Sqlite.sql` |
| 作者 | Luis Rocha |
| 许可 | **MIT** —— 全文见 [LICENSE.md](https://github.com/lerocha/chinook-database/blob/master/LICENSE.md) |
| 下载地址 | `https://raw.githubusercontent.com/lerocha/chinook-database/master/ChinookDatabase/DataSources/Chinook_Sqlite.sql` |
| 大小 | 595545 字节 |
| sha256 | `caf31d698a4a79c628215b552dfe6575e71be052ae02b8f18e763498f55f5d44` |

**「下载地址」这一行是核验过的**，不是凭印象写的：`experiments/03_write_notice.py
--verify-source` 会把该 URL 取下来算 sha256，与仓库内的文件逐字节比对。
本文件生成时该检查通过。

### 它是什么

一张虚构的乐器商店销售库：顾客、员工、曲目、专辑、播放列表、发票。选它是因为

- **公开且许可宽松**（MIT），可以随仓库分发；
- **结构足够真实**：11 张表，有外键、有连接、有聚合，够写出一批有意义的查询题；
- **规模适中**：一万五千行左右，随手就能看完全貌。

### 它不是什么

**不是业务数据。** 里面的顾客、地址、销售额全是编造的示例值。
仓库里所有涉及它的演示、指标、结论，都只对这份示例数据成立，不能外推到任何真实系统。

### 怎么重建

`Chinook_Sqlite.sql` 是**源文件**（随仓库分发）；`chinook.db` 是**构建产物**（不入库）。
重建就是拿 SQLite 把这个脚本执行一遍：

```bash
python experiments/01_build_db.py
```

这一步会顺便核验：11 张表是否齐全、有没有空表、行数是多少，并把 `chinook.db`
的 sha256 打出来。连续构建两次哈希相同，所以「我构建出来的库」和「你构建出来的库」
可以互相比对 —— 这也正是 `.db` 不入库的原因。

## 二、公版文本语料

| 项 | 值 |
|---|---|
| 来源 | [Project Gutenberg](https://www.gutenberg.org) 中文书目 |
| 书目页 | `https://www.gutenberg.org/browse/languages/zh` |
| 许可 | Public domain in the United States —— 全文见 [Project Gutenberg 许可说明](https://www.gutenberg.org/policy/license.html) |
| 篇数 | 40 篇，覆盖 34 部作品 |
| 正文合计 | 694,005 字符 |
| 截断篇数 | 6 篇（其余为全文） |
| 逐篇校验和 | 见 `data/kb/manifest.json` |

### 做了什么处理

1. 移除 Project Gutenberg 页眉页脚（其中含品牌与版权声明，逐篇内容几乎相同）
2. Unicode NFC 归一化，换行统一为 \n
3. 从正文开头截断到每篇 30000 字符
4. 同一作品最多取 2 卷，保证篇目覆盖足够多的不同作品

**关于截断**：公开语料里的长篇动辄几十万字，全量分发会让仓库膨胀到几十 MB。
所以每篇从正文开头截断到 30,000 字符。
截断是**逐篇标注**的 —— `manifest.json` 里每篇都记了 `orig_chars`（原始长度）
和 `truncated`（是否截断），上表也标了。**不隐瞒，也不假装是全文。**

**关于页眉页脚**：Project Gutenberg 的电子书正文前后各有一段品牌与版权声明。
不剥掉的话，「检索」这件事会退化成检索那段几乎每篇都一样的声明 —— 会造成大量假命中。
剥离后出处信息并没有丢：它逐篇记在 `manifest.json` 里（标题、作者、发布日期、原文链接）。

### 逐篇清单

| 编号 | 标题 | 作者 | 首发 | 字符 | 截断 | 来源 |
|---|---|---|---|---|---|---|
| 2090 | Peach Blossom Shangri-la: Tao Hua Yuan Ji | Qian Tao | February 1, 2000 | 4,624 | — | [原文](https://www.gutenberg.org/ebooks/2090) |
| 3100 | The Chinese Classics: with a translation, critical and exegetical notes, prolegomena and copious indexes | James Legge | February 1, 2002 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/3100) |
| 4094 | The Chinese Classics — Volume 1: Confucian Analects | James Legge | May 1, 2003 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/4094) |
| 4572 | 粉妝樓1-10回 | Guanzhong Luo | October 1, 2003 | 25,939 | — | [原文](https://www.gutenberg.org/ebooks/4572) |
| 4573 | 粉妝樓11-20回 | Guanzhong Luo | October 1, 2003 | 27,660 | — | [原文](https://www.gutenberg.org/ebooks/4573) |
| 7209 | 鬼谷子 | active 4th century B.C. Guiguzi | January 1, 2005 | 10,686 | — | [原文](https://www.gutenberg.org/ebooks/7209) |
| 7215 | 鄧析子 | Xi Deng | January 1, 2005 | 4,134 | — | [原文](https://www.gutenberg.org/ebooks/7215) |
| 7216 | 公孫龍子 | active 3rd century B.C. Long Gongsun | January 1, 2005 | 4,362 | — | [原文](https://www.gutenberg.org/ebooks/7216) |
| 7217 | 人物志 | active 3rd century Shao Liu | January 1, 2005 | 14,838 | — | [原文](https://www.gutenberg.org/ebooks/7217) |
| 7218 | 三略 | active 3rd century B.C. Shigong Huang | January 1, 2005 | 5,190 | — | [原文](https://www.gutenberg.org/ebooks/7218) |
| 7219 | 尉繚子 | active 4th century B.C. Liao Wei | January 1, 2005 | 11,950 | — | [原文](https://www.gutenberg.org/ebooks/7219) |
| 7220 | 竹齋集 | Mian Wang | January 1, 2005 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/7220) |
| 7221 | 文淵閣四庫全書 | Various | January 1, 2005 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/7221) |
| 7260 | 搜神記 volume 1-3 | active 317-322 Bao Gan | January 1, 2005 | 14,382 | — | [原文](https://www.gutenberg.org/ebooks/7260) |
| 7266 | 搜神後記 | Qian Tao | January 1, 2005 | 19,933 | — | [原文](https://www.gutenberg.org/ebooks/7266) |
| 7270 | 搜神記 volume 4-10 | active 317-322 Bao Gan | January 1, 2005 | 25,553 | — | [原文](https://www.gutenberg.org/ebooks/7270) |
| 7285 | 韓詩外傳, Vol. 1-2 | active 150 B.C. Ying Han | January 1, 2005 | 12,549 | — | [原文](https://www.gutenberg.org/ebooks/7285) |
| 7286 | 韓詩外傳, Vol. 3-4 | active 150 B.C. Ying Han | January 1, 2005 | 15,561 | — | [原文](https://www.gutenberg.org/ebooks/7286) |
| 7312 | 夢溪筆談, Volume 01-06 | Kuo Shen | January 1, 2005 | 23,734 | — | [原文](https://www.gutenberg.org/ebooks/7312) |
| 7313 | 夢溪筆談, Volume 07-10 | Kuo Shen | January 1, 2005 | 20,524 | — | [原文](https://www.gutenberg.org/ebooks/7313) |
| 7327 | 說苑, Volume 1-4 | Xiang Liu | January 1, 2005 | 24,797 | — | [原文](https://www.gutenberg.org/ebooks/7327) |
| 7328 | 說苑, Volume 5-8 | Xiang Liu | January 1, 2005 | 28,608 | — | [原文](https://www.gutenberg.org/ebooks/7328) |
| 7337 | 道德經 | Laozi | January 1, 2005 | 7,431 | — | [原文](https://www.gutenberg.org/ebooks/7337) |
| 7340 | 六韜 | Shang Lü | January 1, 2005 | 21,521 | — | [原文](https://www.gutenberg.org/ebooks/7340) |
| 7341 | 列子 | active 4th century B.C. Liezi | January 1, 2005 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/7341) |
| 7342 | 詩品 | active 502-519 Rong Zhong | January 1, 2005 | 7,469 | — | [原文](https://www.gutenberg.org/ebooks/7342) |
| 7349 | 孫子兵法道家新註解 | Jingwu Tang | January 1, 2005 | 8,044 | — | [原文](https://www.gutenberg.org/ebooks/7349) |
| 7367 | 管子 | Zhong Guan | January 1, 2005 | 30,000 | 是 | [原文](https://www.gutenberg.org/ebooks/7367) |
| 7375 | 大學 章句 | Xi Zhu | January 1, 2005 | 8,676 | — | [原文](https://www.gutenberg.org/ebooks/7375) |
| 7376 | 中庸 章句 | Xi Zhu | January 1, 2005 | 18,383 | — | [原文](https://www.gutenberg.org/ebooks/7376) |
| 7383 | 商子 | Yang Shang | January 1, 2005 | 25,603 | — | [原文](https://www.gutenberg.org/ebooks/7383) |
| 7406 | 茶經 | Yu Lu | February 1, 2005 | 8,060 | — | [原文](https://www.gutenberg.org/ebooks/7406) |
| 7408 | 申鑒 | Yue Xun | February 1, 2005 | 15,235 | — | [原文](https://www.gutenberg.org/ebooks/7408) |
| 7418 | 幽夢影 — Part 1 | Chao Zhang | February 1, 2005 | 13,543 | — | [原文](https://www.gutenberg.org/ebooks/7418) |
| 7419 | 幽夢影 — Part 2 | Chao Zhang | February 1, 2005 | 13,353 | — | [原文](https://www.gutenberg.org/ebooks/7419) |
| 7420 | 幽夢影 | Chao Zhang | February 1, 2005 | 26,877 | — | [原文](https://www.gutenberg.org/ebooks/7420) |
| 7454 | 顔氏家訓 — Volume 01 and 02 | Zhitui Yan | February 1, 2005 | 11,396 | — | [原文](https://www.gutenberg.org/ebooks/7454) |
| 7455 | 顔氏家訓 — Volume 03 and 04 | Zhitui Yan | February 1, 2005 | 13,166 | — | [原文](https://www.gutenberg.org/ebooks/7455) |
| 7456 | 顔氏家訓 — Volume 05 and 06 | Zhitui Yan | February 1, 2005 | 15,244 | — | [原文](https://www.gutenberg.org/ebooks/7456) |
| 7457 | 顔氏家訓 — Volume 07 | Zhitui Yan | February 1, 2005 | 4,980 | — | [原文](https://www.gutenberg.org/ebooks/7457) |

> 「首发」是 Project Gutenberg 记录的电子书发布日期，不是作品的成书年代。

### 怎么重新获取

语料已经随仓库分发，**正常使用不需要联网**。想从源头重取一遍：

```bash
python experiments/00_fetch_corpus.py --limit 40 --max-per-work 2
```

脚本会逐篇重新下载、按同样的规则清洗，并把清单重写一遍。
`data/kb/raw/` 下每个文件的 sha256 都在清单里，可以对一下是不是同一份。

### 这给检索评测带来的限制（照实说）

- **语料是古典文献，不是现代口语。** 拿它演示检索，提问风格也得偏书面；
  用它去推断「现代用户口语提问」的检索效果是不成立的。
- **只有 40 篇、694,005 字符。**
  这是一个演示规模的语料，不是评测基准。所有检索相关的数字都只在
  这个规模上成立，不能当通用结论。

---

## 本仓库自己的代码

MIT，全文见 `LICENSE`。代码没有从任何其他项目复制 —— 参考过同类实现的设计思路，
但每一行都是重写的，这一点在 `DECISIONS.md` 里有说明。

★ **这条许可的边界在哪**：`LICENSE` 里那份 MIT 覆盖的是**本仓库的代码**。
仓库里分发的**数据**不在这条许可之下 —— 它们各自有自己的出处和许可，逐项列在上面：

- `data/chinook/Chinook_Sqlite.sql` —— Chinook Database 项目，MIT
- `data/kb/raw/*.txt`、`data/kb/manifest.json` —— Project Gutenberg，美国境内公有领域

> 这段话原先接在 `LICENSE` 文件的末尾。挪到这里的原因很实际：MIT 全文后面挂一段
> 中文补充，托管站点就**认不出那是 MIT**（仓库页面上显示成 "Other"）。
> 挪走之后 `LICENSE` 是干净的 MIT 全文，这段话也没丢，只是换了个更该在的位置。

## 没有收录什么

明确列一下，省得有人去找：

- **没有任何私有数据、内部文档或业务语料。** 语料全部来自上面这两个公开来源。
- **没有任何密钥。** `.env` 被 `.gitignore` 排除；仓库里只有 `.env.example`，
  里面是占位符，不含任何真实值。
- **没有构建产物。** `chinook.db` 和 `kb.db` 都不入库 —— 它们由仓库内的脚本从
  上面这些源文件重建。二进制文件没法 review，也没法确认两边的构建结果一致。

## 重建与核验

```bash
python experiments/01_build_db.py --verify-against <官方的 chinook.db>
python experiments/01_build_db.py --check-determinism   # 连建两次比哈希
python experiments/02_build_kb.py                       # 幂等，已建好就只核验
python experiments/03_write_notice.py --verify-source   # 重新确认 Chinook 来源
```
