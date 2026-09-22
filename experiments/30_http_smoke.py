"""HTTP 入口冒烟测试：**真的起一个服务端进程**，然后从 TCP 打它。

## 为什么不直接用 `fastapi.testclient`

`TestClient` 走的是进程内的 ASGI 调用，它绕过了两件事：

1. **`main()` 里的那段启动逻辑** —— 自己 bind socket、`--port 0` 拿内核分配的
   端口、把真实端口打到 stderr。这段逻辑恰恰是冒烟脚本自己要依赖的，
   用 `TestClient` 就永远测不到它。
2. **进程边界**：起不来、端口被占、stdout 混进人话 —— 这些只在真进程上出现。

所以这里与 `08_mcp_smoke.py` 同一条路子：拉子进程，走真传输（那边是 stdio，
这边是 TCP），并且**把服务端的 stdout 整条抓下来**断言里面没有人类可读输出。

## 数据从哪来：临时目录里现造，不碰 `data/`

服务端子进程的 `MCP_TOOLKIT_DATA_ROOT` 指向本次运行现造的一个临时数据根，
里面是一张小库（两张表）和一个两篇文档的小知识库。于是：

- `git clone` 之后**不跑任何构建脚本**也能跑这个冒烟（与 `tests/conftest.py`
  里的 `tiny_db` / `tiny_kb` 同一个理由）；
- 它跑的是**真的** SQL、真的 FTS 检索、真的返回行 —— 不是打桩；
- 不需要网络、不需要模型凭据。

`ask_database` 是唯一的例外：它必须调模型，而本实验不带凭据，所以它只走
**拒绝路径**（无令牌 / scope 不足）。这不是漏测 —— 拒绝本来就应该发生在
碰模型之前，而这条断言恰好证明了这一点：真去调了模型的话，缺凭据会变成
500，而不是 403。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from experiments import _tiny_data  # noqa: E402
from mcp_server import auth, console, dispatch, paths  # noqa: E402

REPORTS = paths.reports_dir()

SERVER_CMD = [sys.executable, "-B", "-X", "utf8", "-m", "mcp_server.http_api",
              "--port", "0"]

# 起进程 + 等我方探针连上。**只用于判据**，耗时读数不写进报告（见文件末的「口径」）。
START_TIMEOUT = 30.0
REQ_TIMEOUT = 15.0

PORT_RE = re.compile(r"\[http\]\s*监听\s+(\S+):(\d+)")


def child_env(**overrides) -> dict:
    """拉起服务端时用的环境：钉死 stdio 编码，并指向临时数据根。

    `PYTHONIOENCODING` 必须显式设：它的优先级高于 `-X utf8`，外层环境里只要
    有人设了 gbk，子进程的 stderr 就是 GBK，这边按 UTF-8 解出一片乱码，
    「端口那一行读到了没有」这条检查随之误报。父进程假定了一个自己没设定的
    编码 —— 那是父进程的错。08 那边踩过同一个坑，注释更详细。
    """
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("MCP_JWT_STRICT", None)   # 别让外层环境意外打开严格模式
    env.update(overrides)
    return env


# ---------------------------------------------------------------- 服务端进程
class Server:
    """把服务端拉起来，读到真实端口，退出时收尸。"""

    def __init__(self, data_root: Path) -> None:
        self.proc = subprocess.Popen(
            SERVER_CMD, cwd=str(ROOT), env=child_env(MCP_TOOLKIT_DATA_ROOT=str(data_root)),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.err_lines: list[str] = []
        self.out_lines: list[str] = []
        self.port: int | None = None
        self.host: str | None = None
        self._seen = threading.Event()
        for pipe, sink, on_line in ((self.proc.stderr, self.err_lines, self._note_port),
                                    (self.proc.stdout, self.out_lines, None)):
            threading.Thread(target=self._pump, args=(pipe, sink, on_line),
                             daemon=True).start()

    def _pump(self, pipe, sink: list[str], on_line) -> None:
        try:
            for line in pipe:
                sink.append(line.rstrip("\n"))
                if on_line:
                    on_line(line)
        except Exception:  # noqa: BLE001 —— 收尸阶段管道被关掉是正常的
            return

    def _note_port(self, line: str) -> None:
        m = PORT_RE.search(line)
        if m and self.port is None:
            self.host, self.port = m.group(1), int(m.group(2))
            self._seen.set()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def wait_ready(self) -> str | None:
        """等端口那一行出现，再用 `/healthz` 确认真的能服务。返回失败原因或 None。"""
        if not self._seen.wait(START_TIMEOUT):
            return f"等不到「监听」那一行；stderr 末尾：{self.err_lines[-3:]}"
        client = httpx.Client(timeout=REQ_TIMEOUT)
        deadline = time.monotonic() + START_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    return f"进程退出了（退出码 {self.proc.returncode}）"
                try:
                    if client.get(f"{self.base_url}/healthz").status_code == 200:
                        return None
                except httpx.HTTPError:
                    time.sleep(0.05)
            return "端口报出来了，但 /healthz 一直没通"
        finally:
            client.close()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        for p in (self.proc.stdout, self.proc.stderr):
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 检查收集
class Rows:
    """收集检查行，同时记下结果 —— 避免「检查了但没记录」。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, label: str, expect: str, got: str, ok: bool, note: str = "") -> None:
        self.rows.append({"label": label, "expect": expect, "got": got,
                          "ok": bool(ok), "note": note})

    def fail_rest(self, labels: list[tuple[str, str]], reason: str) -> None:
        for label, expect in labels:
            self.add(label, expect, f"未跑到（{reason}）", False, "服务端在这一步之前就断了")

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.rows)


def _post(c: httpx.Client, name: str, body) -> httpx.Response:
    return c.post(f"/tools/{name}", json=body)


def run_checks(c: httpx.Client) -> Rows:
    r = Rows()

    # ---------------- 存活与能力清单
    resp = c.get("/healthz")
    d = resp.json()
    r.add("存活探针 GET /healthz", "200 且 ok=True",
          f"{resp.status_code} 且 ok={d.get('ok')}",
          resp.status_code == 200 and d.get("ok") is True)

    resp = c.get("/tools")
    d = resp.json()
    names = [t["name"] for t in d.get("tools", [])]
    declared = dispatch.tool_names()
    r.add("GET /tools 的工具数与 TOOL_SCOPES 一致",
          f"{len(declared)} 个", f"{len(names)} 个：{', '.join(names)}",
          names == declared,
          "名字清单只有 auth.TOOL_SCOPES 一个真值源，路由也是照着它现挂的")

    by_name = {t["name"]: t["scope"] for t in d.get("tools", [])}
    r.add("GET /tools 里每个工具的 scope 与 TOOL_SCOPES 逐条一致",
          str(dict(auth.TOOL_SCOPES)), str(by_name), by_name == dict(auth.TOOL_SCOPES))

    resp = c.get("/openapi.json")
    spec = resp.json() if resp.status_code == 200 else {}
    posted = sorted(p for p, m in (spec.get("paths") or {}).items() if "post" in m)
    want = sorted(f"/tools/{n}" for n in declared)
    r.add("OpenAPI 文档里 6 条工具路径都在（`/docs` 能点着调）",
          "6 条 POST /tools/<工具名>", f"{len(posted)} 条：{', '.join(posted)}",
          resp.status_code == 200 and posted == want,
          "文档是框架按真实路由生成的，不是另写一份")

    # ---------------- 拒绝路径
    resp = _post(c, "list_tables", {})
    d = resp.json()
    r.add("无令牌（空请求体）：403 + NO_TOKEN",
          f"403 且 code={auth.E_NO_TOKEN}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 403 and d.get("code") == auth.E_NO_TOKEN
          and d.get("denied") is True,
          "空请求体是「没带令牌」，不是「请求畸形」；`token` 缺席由派发器补 None "
          "交给 guard 判，于是这里得到 403 而不是 400")

    forged = auth.make_token("attacker", list(auth.SCOPES),
                             secret_="a-different-secret-of-sufficient-length-0000")
    resp = _post(c, "list_tables", {"token": forged})
    d = resp.json()
    r.add("伪造签名：403 + BAD_SIGNATURE",
          f"403 且 code={auth.E_BAD_SIGNATURE}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 403 and d.get("code") == auth.E_BAD_SIGNATURE)

    resp = _post(c, "run_sql", {"token": auth.make_token("u", ["db:read"]),
                               "sql": "SELECT 1"})
    d = resp.json()
    r.add("令牌有效但 scope 不足：403 + INSUFFICIENT_SCOPE",
          f"403 且 code={auth.E_SCOPE}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 403 and d.get("code") == auth.E_SCOPE,
          "同一把钥匙开不了所有的门 —— 工具级 scope 在 HTTP 这一侧同样生效")
    r.add("被拒时响应体里没有业务字段",
          "不含 rows / sql", f"{sorted(d)}",
          "rows" not in d and "sql" not in d,
          "鉴权挡在业务代码之前，SQL 根本没被执行")

    resp = _post(c, "no_such_tool", {"token": auth.make_token("u", list(auth.SCOPES))})
    d = resp.json()
    r.add("未知工具：404 + UNKNOWN_TOOL（而不是默认的 {\"detail\":\"Not Found\"}）",
          f"404 且 code={auth.E_UNKNOWN_TOOL}",
          f"{resp.status_code} 且 code={d.get('code')}，detail={str(d.get('detail'))[:40]!r}",
          resp.status_code == 404 and d.get("code") == auth.E_UNKNOWN_TOOL
          and "detail" in d and d["detail"] != "Not Found",
          "与 stdio 侧同一个拒绝体：码由 auth.guard 统一给出，两处不各说各话")

    tok_q = auth.make_token("smoke", ["db:query"])
    resp = _post(c, "run_sql", {"token": tok_q})
    d = resp.json()
    r.add("缺必填参数：400 + BAD_ARGUMENTS（不是 403）",
          f"400 且 code={dispatch.E_BAD_ARGUMENTS}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 400 and d.get("code") == dispatch.E_BAD_ARGUMENTS
          and d.get("denied") is False,
          "少给一个字段若报成鉴权失败，排错的人会跑去查令牌，而问题在请求体里")

    resp = c.post("/tools/run_sql", content=b"{not json",
                  headers={"content-type": "application/json"})
    d = resp.json()
    r.add("请求体不是合法 JSON：400 + BAD_ARGUMENTS",
          f"400 且 code={dispatch.E_BAD_ARGUMENTS}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 400 and d.get("code") == dispatch.E_BAD_ARGUMENTS)

    # ---------------- 放行路径（★ 反证：不是一刀切全拒）
    tok_read = auth.make_token("smoke", ["db:read"])
    resp = _post(c, "list_tables", {"token": tok_read})
    d = resp.json()
    got_tables = sorted(t["name"] for t in d.get("tables", []))
    r.add("令牌齐全：200 且列出真实表名（★ 反证）",
          "200 且表名 {album, artist}",
          f"{resp.status_code} 且 ok={d.get('ok')}，表名 {got_tables}",
          resp.status_code == 200 and d.get("ok") is True
          and got_tables == ["album", "artist"],
          "断言的是真的读到了库里那两张表，不是「没被拒」")

    resp = _post(c, "get_schema", {"token": tok_read, "table": "album"})
    d = resp.json()
    cols = sorted(col["name"] for t in d.get("tables", []) for col in t.get("columns", []))
    r.add("GET 结构：200 且列名与库里一致",
          "200 且含 artist_id / title / year",
          f"{resp.status_code} 且列名 {cols}",
          resp.status_code == 200 and d.get("ok") is True
          and {"artist_id", "title", "year"} <= set(cols))

    resp = _post(c, "run_sql", {"token": tok_q, "sql": "SELECT name FROM artist ORDER BY id"})
    d = resp.json()
    r.add("真 SQL：200 且返回真行",
          "200 且 rows=[[A],[B]]",
          f"{resp.status_code} 且 rows={d.get('rows')}",
          resp.status_code == 200 and d.get("ok") is True and d.get("rows") == [["A"], ["B"]])

    resp = _post(c, "run_sql", {"token": tok_q, "sql": "DROP TABLE artist"})
    d = resp.json()
    r.add("★ 被 SQL 护栏拦下：**200** 且 blocked=True",
          "200 且 blocked=True（业务结论，不是传输失败）",
          f"{resp.status_code} 且 blocked={d.get('blocked')}",
          resp.status_code == 200 and d.get("blocked") is True,
          "请求被正确地接收、执行、并得出了「这条 SQL 不许跑」的结论。"
          "报成 4xx/5xx 的话，调用方会去重试或查网络，而正确处置是看 SQL 写错了什么")

    resp = _post(c, "run_sql", {"token": tok_q, "sql": "DELETE FROM artist"})
    d2 = resp.json()
    r.add("护栏拦下时返回体里没有 rows（说明真没执行）",
          "不含 rows 或 rows 为空", f"blocked={d2.get('blocked')}，有 rows={bool(d2.get('rows'))}",
          d2.get("blocked") is True and not d2.get("rows"))

    tok_kb = auth.make_token("smoke", ["kb:read"])
    resp = _post(c, "search_passages", {"token": tok_kb, "query": "茶"})
    d = resp.json()
    hits = [h.get("doc_id") for h in d.get("hits", [])]
    r.add("知识库检索：200 且只命中该命中的那篇",
          "200 且命中含 d2（茶經），不含 d1（兵法）",
          f"{resp.status_code} 且命中 {hits}",
          resp.status_code == 200 and d.get("ok") is True and "d2" in hits and "d1" not in hits)

    resp = _post(c, "get_passage", {"token": tok_kb, "doc_id": "d2"})
    d = resp.json()
    text = str(d.get("text") or "")
    r.add("取原文片段：200 且正文非空",
          "200 且 text 里有原文", f"{resp.status_code} 且正文 {len(text)} 字：{text[:24]!r}",
          resp.status_code == 200 and d.get("ok") is True and "茶" in text)

    # ---------------- 必须调模型的工具：只走拒绝路径
    resp = _post(c, "ask_database", {"token": tok_read, "question": "一共有多少张专辑"})
    d = resp.json()
    r.add("★ 需要模型的工具在 scope 不足时同样 403（证明拒绝发生在碰模型之前）",
          f"403 且 code={auth.E_SCOPE}",
          f"{resp.status_code} 且 code={d.get('code')}",
          resp.status_code == 403 and d.get("code") == auth.E_SCOPE,
          "本实验不带模型凭据。真去调了模型的话，这里会是 500（缺少环境变量），"
          "而不是 403 —— 所以这条断言本身就是「鉴权挡在业务之前」的证据")

    return r


def stdout_purity_check(srv: Server) -> dict:
    """★ 服务端的 stdout 里**不许有人类可读输出**。

    这条不是洁癖：冒烟脚本自己就是靠 stderr 上那行「监听 host:port」拿到端口的。
    哪天有人把端口那行改成 `print()`（也就是 stdout），这里立刻红 ——
    而不是等到某个把 stdout 当数据通道的调用方踩上去。

    同时断言 stderr 里**确实有**那一行：否则「stdout 是空的」也可能只是因为
    进程根本没起来，那样的通过毫无意义。
    """
    out = [ln for ln in srv.out_lines if ln.strip()]
    return {
        "label": "服务端 stdout 里一行人话都没有（端口那行在 stderr）",
        "expect": "stdout 无内容；stderr 里有「监听 host:port」",
        "got": (f"stdout {len(out)} 行"
                + (f"（例如 {out[0][:60]!r}）" if out else "")
                + f"；stderr {'有' if srv.port else '**没有**'}端口那一行"),
        "ok": not out and srv.port is not None,
    }


# ---------------------------------------------------------------- 报告
def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ")
                                     for c in r) + " |")
    return "\n".join(out)


def render_md(d: dict) -> str:
    L: list[str] = []
    L.append("# HTTP 入口冒烟测试（实测结果）\n")
    L.append("本文件由 `experiments/30_http_smoke.py` 生成，**不要手改**；重跑该脚本即可复现。\n")

    L.append("## 口径\n")
    L.append("- **真的起进程、真的走 TCP**：`python -B -X utf8 -m mcp_server.http_api "
             "--port 0`，然后用 HTTP 客户端打它。不是进程内的 `TestClient` —— "
             "那个绕过 `main()` 里的启动逻辑（自己 bind socket、从 stderr 报真实端口），"
             "而这段逻辑恰恰是本脚本自己要依赖的。")
    L.append("- **端口由内核分配**（`--port 0`），脚本从服务端 stderr 的"
             "「监听 host:port」那一行读到真实端口。端口是随机值，**不写进本报告**。")
    L.append("- **数据现造**：服务端子进程的 `MCP_TOOLKIT_DATA_ROOT` 指向本次运行"
             "临时目录里的数据根，内含一张两张表的小库（`artist` / `album`）与一个"
             "两篇文档的小知识库。所以本实验**不依赖 `data/` 是否已构建**，"
             "`git clone` 之后不跑任何构建脚本也能跑。")
    L.append("- **不联网、不调模型**。`ask_database` 必须调模型，因此它只走拒绝路径"
             "（无令牌 / scope 不足）—— 这恰好证明了拒绝发生在碰模型之前。")
    L.append("- **不记耗时**：报告要能逐字节复现（同一份代码跑两遍，"
             "`git status` 应当干净）。时钟读数与随机端口都会变，所以一个都不进产物。")
    L.append("- 运行方式：`python experiments/30_http_smoke.py`（退出码 0 = 全过）\n")

    L.append("## 总览\n")
    L.append(_table([["HTTP 检查", f"{d['pass']} / {d['cases']}"],
                     ["进程边界（stdout 纯净）", f"{d['edge_pass']} / {d['edge_cases']}"],
                     ["**合计**", f"**{d['n_pass']} / {d['n_cases']}**"]],
                    ["项", "通过 / 总数"]))
    L.append("")

    L.append("## 一、HTTP 检查\n")
    L.append(_table([[i + 1, r["label"], r["expect"], r["got"], "✅" if r["ok"] else "❌"]
                     for i, r in enumerate(d["rows"])],
                    ["#", "检查项", "期望", "实测", "结果"]))
    L.append("")

    L.append("## 二、进程边界\n")
    L.append("这一节守的是那个已经踩过一次的坑：**服务端的人话不许出现在 stdout 上**。"
             "`08_mcp_smoke.py` 是在 stdio 传输下踩的（stdout 就是 JSON-RPC 通道，"
             "一行「已注册 6 个工具」让客户端解析器直接崩）；HTTP 模式下 stdout 不是"
             "协议通道，但脚本仍然靠 stderr 拿端口 —— 规矩一致，守卫也一致。\n")
    L.append(_table([[r["label"], r["expect"], r["got"], "✅" if r["ok"] else "❌"]
                     for r in d["edge_rows"]],
                    ["检查项", "期望", "实测", "结果"]))
    L.append("")

    if d["notes"]:
        L.append("## 三、值得记下来的实测行为\n")
        for n in d["notes"]:
            L.append(f"- {n}")
        L.append("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    # httpx 会把每一次请求打成一行 INFO 日志，每行都带着端口 —— 终端上一屏噪音，
    # 把真正的检查结果挤没了。它只是噪音，不是证据，压到 WARNING。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    REPORTS.mkdir(parents=True, exist_ok=True)

    rows = Rows()
    edge_rows: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="mcp-http-smoke-") as td:
        data_root = _tiny_data.build(Path(td))
        srv = Server(data_root)
        try:
            why = srv.wait_ready()
            if why is not None:
                print(f"[FAIL] 服务端起不来：{why}", file=sys.stderr)
                for ln in srv.err_lines[-10:]:
                    print(f"    stderr| {ln}", file=sys.stderr)
                return 1
            with httpx.Client(base_url=srv.base_url, timeout=REQ_TIMEOUT) as c:
                rows = run_checks(c)
        finally:
            srv.stop()
        edge_rows = [stdout_purity_check(srv)]

    all_rows = rows.rows + edge_rows
    data = {
        "rows": rows.rows, "edge_rows": edge_rows,
        "cases": len(rows.rows), "pass": sum(r["ok"] for r in rows.rows),
        "edge_cases": len(edge_rows), "edge_pass": sum(r["ok"] for r in edge_rows),
        "notes": [
            "**未知工具走的是真 404**：`POST /tools/nope` 匹配不到任何路由，"
            "Starlette 抛 404，被一个只在 `/tools/` 前缀下生效的异常处理器改写成"
            "与 stdio 侧同一个拒绝体。其他路径的 404 行为原样保留。",
            "**被护栏拦下是 200**：这是本层唯一一个「看起来该报错、实际报成功」的"
            "映射。判定依据是「请求被正确处理并得出结论了吗」—— 是，所以是成功；"
            "它和 403 的区别是「你的 SQL 写得不对」与「你没有权限」，"
            "调用方对这两件事的处置完全不同。",
            "**`DEV_SECRET_REFUSED` 映射成 500 而不是 403**：这个码的含义是"
            "「服务端自己在严格模式下还在用内置开发密钥」，是服务端的配置问题。"
            "报 403 等于把运维的锅甩给调用方。",
            "**缺参数与没权限是两个码**：少给 `sql` 是 400 `BAD_ARGUMENTS`，"
            "不带令牌是 403 `NO_TOKEN`。混成一个的话，排错的人会查错方向。",
        ],
    }
    data["n_cases"] = len(all_rows)
    data["n_pass"] = sum(r["ok"] for r in all_rows)

    (REPORTS / "http_smoke.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / "http_smoke.md").write_text(render_md(data), encoding="utf-8", newline="\n")

    print("=" * 72)
    print("HTTP 入口冒烟测试（真进程 + 真 TCP）")
    print("=" * 72)
    print(f"  HTTP 检查      {data['pass']} / {data['cases']}")
    print(f"  进程边界       {data['edge_pass']} / {data['edge_cases']}")
    print(f"  合计           {data['n_pass']} / {data['n_cases']}")
    print("-" * 72)
    for r in all_rows:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['label']}")
        if not r["ok"]:
            print(f"      期望 {r['expect']}")
            print(f"      实测 {r['got']}")
    print("\n已写入 reports/http_smoke.md 与 reports/http_smoke.json")
    return 0 if data["n_pass"] == data["n_cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
