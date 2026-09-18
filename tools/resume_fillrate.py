#!/usr/bin/env python
"""简历排版的**填充率**：每页的文字到哪儿结束（pt），而不是只数页数。

## 为什么量这个

「两页」这个结论太粗。A4 两页、第二页只用了一半，和两页挤到只剩几个 pt，
是两种排版 —— 而改字的余地完全不一样。**加字之前必须先知道第二页还剩多少**，
否则改完就变三页，而「两页变三页」是硬伤（`DECISIONS.md` D-4x）。

交付级自检第 14 项（`experiments/28_delivery_selfcheck.py`）只查「页数 == 2」，
那是**门槛**；这里量的是**离门槛还有多远**。

## 口径

- **输入**：`DELIVERY_RESUME_DIR` 下唯一那个文件名带 `4.0` 的 PDF。用的是
  与交付级自检第 14 项**同一个**查找函数（`delivery_selfcheck.resume_pdf()`）——
  两边要是各找各的，报告里的数字就可以指着不同文件
- **量法**：PyMuPDF 取每页所有**文字块**的 bbox，取其中最大的 `y1`
  （页面左上角为原点，`y1` 越大越靠下）
- **底线**：A4 高 841.89pt − 下边距 10mm = **813.54pt**。正文越过这条线就顶进
  页边距里，再往上就是第三页
- **两个百分比都给**：占整页高（841.89，跟任务书里的「88% / 96%」同一口径）
  与占底线（813.54，这才是「还能不能再加字」的那个分母）。只给一个的话，
  两种口径的百分比看着都合理，对不上时说不清是谁算错了
- **不量页眉页脚**：导出时带 `--no-pdf-header-footer`（命令见 `--export`）
- 量的是**已经导出的那一份**，所以脚本会先把它的 sha256 打出来 ——
  「量的是哪一份」不许靠猜

## 用法

    python tools/resume_fillrate.py              # 量现有的 4.0 PDF
    python tools/resume_fillrate.py --export     # 先按固定命令重新导出，再量

`--export` 用的是与手工导出**逐字相同**的那条 Chrome 命令（`--headless
--disable-gpu --no-pdf-header-footer --print-to-pdf`），Chrome 路径可用环境变量
`RESUME_CHROME` 覆盖，默认 `C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe`。

退出码：0 量到了 / 2 量不了（没目录、没 PDF、没 Chrome、没 PyMuPDF）。
量不了时**不打表** —— 空表和「填充率 0%」长得一样，含义正相反。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server import console, delivery_selfcheck as dsc  # noqa: E402

A4_H_PT = 841.89
BOTTOM_MM = 10.0
BOTTOM_PT = BOTTOM_MM / 25.4 * 72.0
LIMIT_PT = A4_H_PT - BOTTOM_PT          # 813.54
CHROME_DEFAULT = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

E_OK, E_CANT = 0, 2


def export(html: Path) -> tuple[bool, str]:
    """按固定命令重新导出同目录下的 4.0 PDF。返回 `(成功, 说明)`。"""
    exe = os.environ.get("RESUME_CHROME", "").strip() or CHROME_DEFAULT
    if not Path(exe).is_file():
        return False, (f"找不到 Chrome（{Path(exe).name}）—— "
                       f"用环境变量 RESUME_CHROME 指一个，或者手工打印")
    out = html.with_suffix(".pdf")
    cmd = [exe, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={out}", html.as_uri()]
    # ★ 这里**不要** `text=True`：那会让 Python 按系统 ANSI 代码页（本机是 GBK）
    #   去解 Chrome 的 UTF-8 stderr，于是导出明明成功，终端里却先蹦一串解码报错，
    #   看着像导出坏了。收字节、要带话时自己按 utf-8 解，解不动就替换。
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0:
        line = (r.stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
        return False, f"Chrome 退出码 {r.returncode}：{line[:160]}"
    return True, "已重新导出"


def fills(pdf: Path) -> list[tuple[int, float, int]]:
    """每页 `(页码, 文字块最下沿 y1, 文字块数)`。"""
    import pymupdf  # PyMuPDF：装在跑本仓的同一个解释器里
    out: list[tuple[int, float, int]] = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, 1):
            ys = [b[3] for b in page.get_text("blocks")]
            out.append((i, max(ys) if ys else 0.0, len(ys)))
    return out


def main(argv: list[str]) -> int:
    console.setup_stdio()
    html, why = dsc.resume_html()
    if html is None:
        print(f"[无法测量] {why}")
        return E_CANT
    if "--export" in argv:
        ok, note = export(html)
        print(f"[导出] {note}")
        if not ok:
            return E_CANT
    pdf, why = dsc.resume_pdf()
    if pdf is None:
        print(f"[无法测量] {why}")
        return E_CANT

    data = pdf.read_bytes()
    print(f"文件  : {pdf.name}（{len(data)} 字节）")
    print(f"sha256: {hashlib.sha256(data).hexdigest()[:12]}")
    print(f"底线  : {LIMIT_PT:.2f} pt（A4 高 {A4_H_PT} − 下边距 {BOTTOM_MM:g}mm）")
    print()
    print("| 页 | 文字块数 | 最下沿 y1 (pt) | 占整页 | 占底线 | 余量 (pt) |")
    print("|---|---|---|---|---|---|")
    for n, y1, blocks in fills(pdf):
        print(f"| {n} | {blocks} | {y1:.1f} | {y1 / A4_H_PT:.0%} | "
              f"{y1 / LIMIT_PT:.0%} | {LIMIT_PT - y1:.1f} |")
    n_pages = len(fills(pdf))
    print()
    print(f"页数 = {n_pages}" + ("（门槛是 2）" if n_pages != 2 else ""))
    return E_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
