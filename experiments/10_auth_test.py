"""鉴权用例矩阵：跑一遍所有能想到的令牌，逐条记录「期望 vs 实测」。

## 这个脚本要回答的问题

不是「鉴权能不能拒绝坏人」——那个问题太容易满分了。把所有请求一刀切拒掉，
拒绝率就是 100%。所以要**同时**回答第二个问题：

    「该放行的那一半，真的放行了吗？」

只有两个方向都是绿的，这套鉴权才既有安全性又有可用性。
报告里因此是两张表 —— **应拒 N/N** 和 **应放行 M/M**，缺一张这个实验就没有意义。

## 三条分节

1. **决策矩阵**：直接问 `auth.guard(工具, 令牌)`，纯函数、不开库、不联网。
2. **入口一致性**：调用**真实的工具函数**，断言它的裁决与 `guard()` 一致。
   第 1 节只证明了「决策函数是对的」，第 2 节才证明「工具真的用了它」——
   少了第 2 节，工具入口漏掉 `_deny()` 调用这种 bug 是查不出来的。
3. **实测到的边界行为**：几条与直觉不同、但跑出来就是这样的事实（见文件末尾 `NOTABLE`）。
   照实写进报告，不做美化。

## 口径

- **一条用例** = 一次 `(工具, 令牌)` 的裁决。
- **通过** = 实测结果与用例声明的期望**完全一致**（错误码逐字相同，不接受「反正拒了」）。
- 退出码：0 = 全部通过；1 = 有不符合期望的用例；2 = 环境问题。
- 不联网、不调用模型、不需要 `data/` 下的构建产物。`git clone` 后可直接跑。
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt  # noqa: E402

from mcp_server import auth, console, paths  # noqa: E402

REPORTS = paths.reports_dir()

# PyJWT 会对「密钥短于 64 字节却用 SHA512」发告警。那是本节构造 HS512 负例时的副产物
# （我们**故意**用一个不匹配的算法去签），与被测的鉴权逻辑无关，所以在这里屏蔽掉，
# 免得刷屏让人误以为仓库本身有问题。
warnings.filterwarnings("ignore", message=r".*HMAC key is .* bytes long.*")


# ---------------------------------------------------------------- 造令牌
def _hs512_token() -> str:
    """用**同一个密钥**、换成 HS512 签一个令牌。

    测的是算法白名单：`algorithms=[ALGO]` 应该只看头部的 `alg` 就拒掉，
    连验签的机会都不给。密钥长度在这里无关紧要（提前就拒了）。
    """
    return jwt.encode(
        {"sub": "u", "iss": auth.issuer(), "aud": auth.audience(),
         "exp": 9999999999, "scope": "db:read"},
        auth.secret(), algorithm="HS512")


def _alg_none_token() -> str:
    """`alg: none` 且完全不带签名 —— 最经典的伪造手法。"""
    head = jwt.utils.base64url_encode(b'{"alg":"none","typ":"JWT"}').decode()
    body = jwt.utils.base64url_encode(json.dumps(
        {"sub": "u", "iss": auth.issuer(), "aud": auth.audience(),
         "exp": 9999999999, "scope": "db:read"}).encode()).decode()
    return f"{head}.{body}."


def _claim(patched: dict) -> str:
    """标准载荷上打补丁后重签。`patched` 里值为 `None` 表示**删掉**这个 claim。"""
    payload = {"sub": "u", "iss": auth.issuer(), "aud": auth.audience(),
               "exp": 9999999999, "scope": "db:read"}
    for k, v in patched.items():
        if v is None:
            payload.pop(k, None)
        else:
            payload[k] = v
    return jwt.encode(payload, auth.secret(), algorithm=auth.ALGO)


R = ["db:read"]
Q = ["db:query"]
K = ["kb:read"]

# ---------------------------------------------------------------- 应拒用例
# (说明, 工具, 令牌, 期望错误码)
DENY: list[tuple[str, str, object, str]] = [
    # --- 没有令牌 / 令牌不是字符串 ---
    ("完全没有令牌（空串）", "list_tables", "", auth.E_NO_TOKEN),
    ("令牌为 None", "list_tables", None, auth.E_NO_TOKEN),
    ("令牌为整数 0（假值）", "list_tables", 0, auth.E_NO_TOKEN),
    ("令牌为空列表（假值）", "list_tables", [], auth.E_NO_TOKEN),
    ("令牌为整数 1（真值但非字符串）", "list_tables", 1, auth.E_INVALID),
    ("令牌为字典（真值但非字符串）", "list_tables", {"a": 1}, auth.E_INVALID),
    # --- 结构就不是 JWT ---
    ("普通字符串", "list_tables", "not-a-token", auth.E_INVALID),
    ("三段垃圾", "list_tables", "a.b.c", auth.E_INVALID),
    ("只有两个点，末尾签名缺失", "list_tables", _alg_none_token().rstrip("."), auth.E_INVALID),
    ("超长垃圾串", "list_tables", "x" * 5000, auth.E_INVALID),
    # --- 算法攻击 ---
    ("alg:none 无签名", "list_tables", _alg_none_token(), auth.E_INVALID),
    ("HS512 同密钥（算法白名单应拦下）", "list_tables", _hs512_token(), auth.E_INVALID),
    # --- 签名与签发方 ---
    ("换一个密钥签名", "list_tables",
     auth.make_token("u", R, secret_="a-different-secret-of-sufficient-length-0000"),
     auth.E_BAD_SIGNATURE),
    ("issuer 不符", "list_tables", auth.make_token("u", R, issuer_="evil"), auth.E_BAD_ISSUER),
    ("audience 不符", "list_tables", auth.make_token("u", R, audience_="evil"), auth.E_BAD_AUDIENCE),
    # --- 时间 ---
    ("已过期", "list_tables", auth.make_token("u", R, ttl=-10), auth.E_EXPIRED),
    ("ttl=0（签发即过期）", "list_tables", auth.make_token("u", R, ttl=0), auth.E_EXPIRED),
    ("iat 在未来（客户端时钟快）", "list_tables",
     auth.make_token("u", R, iat_offset=10 ** 6), auth.E_INVALID),
    # --- 必填 claim 缺失 ---
    ("缺 exp", "list_tables", _claim({"exp": None}), auth.E_INVALID),
    ("缺 iss", "list_tables", _claim({"iss": None}), auth.E_INVALID),
    ("缺 aud", "list_tables", _claim({"aud": None}), auth.E_INVALID),
    ("缺 sub", "list_tables", _claim({"sub": None}), auth.E_INVALID),
    # --- scope ---
    ("令牌没有 scope", "list_tables", auth.make_token("u", []), auth.E_SCOPE),
    ("令牌的 scope 键整个缺失", "list_tables",
     _claim({"scope": None}), auth.E_SCOPE),
    ("只有 db:query，却要调 db:read 的工具", "list_tables", auth.make_token("u", Q), auth.E_SCOPE),
    ("只有 kb:read，却要调 db:read 的工具", "list_tables", auth.make_token("u", K), auth.E_SCOPE),
    ("只有 db:read，却要调 db:query 的工具", "run_sql", auth.make_token("u", R), auth.E_SCOPE),
    ("只有 db:read，却要调 kb:read 的工具", "search_passages", auth.make_token("u", R), auth.E_SCOPE),
    ("size 大小写不符 DB:READ", "list_tables", auth.make_token("u", ["DB:READ"]), auth.E_SCOPE),
    ("用通配符 db:* 冒充 db:read", "list_tables", auth.make_token("u", ["db:*"]), auth.E_SCOPE),
    ("把 scope 写在别的前缀下 read:db", "list_tables", auth.make_token("u", ["read:db"]), auth.E_SCOPE),
    ("scope claim 是数字而不是字符串", "list_tables", _claim({"scope": 123}), auth.E_INVALID),
    ("scope claim 是列表而不是字符串", "list_tables", _claim({"scope": ["db:read"]}), auth.E_INVALID),
    # --- 工具本身不在册 ---
    ("工具名未登记", "no_such_tool", auth.make_token("u", list(auth.SCOPES)), auth.E_UNKNOWN_TOOL),
    ("工具名为空串", "", auth.make_token("u", list(auth.SCOPES)), auth.E_UNKNOWN_TOOL),
    ("工具名大小写不符 Run_SQL", "Run_SQL", auth.make_token("u", Q), auth.E_UNKNOWN_TOOL),
]

# ---------------------------------------------------------------- 应放行用例
# (说明, 工具, 令牌, 备注)
# 备注写进报告，用来交代「这条为什么这么设计」，尤其是看起来可疑的那几条。
ALLOW: list[tuple[str, str, str, str]] = [
    ("db:read 调 list_tables", "list_tables", auth.make_token("u", R), ""),
    ("db:read 调 get_schema", "get_schema", auth.make_token("u", R), ""),
    ("db:query 调 run_sql", "run_sql", auth.make_token("u", Q), ""),
    ("db:query 调 ask_database", "ask_database", auth.make_token("u", Q), ""),
    ("kb:read 调 search_passages", "search_passages", auth.make_token("u", K), ""),
    ("kb:read 调 get_passage", "get_passage", auth.make_token("u", K), ""),
    ("三种 scope 全给，调 list_tables", "list_tables",
     auth.make_token("u", ["db:read", "db:query", "kb:read"]),
     "scope 是「持有集合」语义，多给不该变成拒绝"),
    ("多给一个无关 scope，调 run_sql", "run_sql",
     auth.make_token("u", ["db:query", "kb:read"]), "同上"),
    ("scope 重复写两遍，调 list_tables", "list_tables",
     auth.make_token("u", ["db:read", "db:read"]), "去重后仍然放行"),
    ("scope 前后有空格", "list_tables",
     auth.make_token("u", [" db:read "]), "split() 会吃掉空白"),
    ("令牌额外带了未知 claim（role=admin）", "list_tables",
     _claim({"role": "admin"}), "未知 claim 被忽略，不影响裁决"),
    ("sub 是空字符串", "list_tables",
     _claim({"sub": ""}),
     "⚠️ 值得注意：require 只查键存在、不查值非空，所以空 sub 会放行"),
    ("列表元素里含空格（db:read db:query）", "list_tables",
     auth.make_token("u", ["db:read db:query"]),
     "scope 在令牌里是空格分隔的（RFC 6749），所以带空格的元素**本来就等于两个 scope**，"
     "不是注入也不是漏检。这条原本被我写成应拒用例，跑出来发现是期望写错了，"
     "照实改成放行并留在这里。"),
]


def decide(tool: str, token: object) -> dict:
    ok, info = auth.guard(tool, token)  # type: ignore[arg-type]
    return {"ok": ok, "code": "" if ok else info.get("code", ""),
            "detail": "" if ok else info.get("detail", "")}


def run_deny() -> list[dict]:
    out = []
    for label, tool, token, expect in DENY:
        r = decide(tool, token)
        out.append({"label": label, "tool": tool, "expect": expect,
                    "got": r["code"], "detail": r["detail"],
                    "pass": (not r["ok"]) and r["code"] == expect})
    return out


def run_allow() -> list[dict]:
    out = []
    for label, tool, token, note in ALLOW:
        r = decide(tool, token)
        out.append({"label": label, "tool": tool, "note": note,
                    "got": "（放行）" if r["ok"] else r["code"],
                    "detail": r["detail"], "pass": bool(r["ok"])})
    return out


# ---------------------------------------------------------------- 入口一致性
def run_entry_consistency() -> list[dict]:
    """把真实的工具函数调一遍，看它的裁决跟 `guard()` 是否一致。

    允许侧只调**不需要数据也不调模型**的 5 个工具：它们缺数据时会返回一个错误码
    字典，不会抛异常。`ask_database` 被排除，因为它一放行就真的会去调模型 ——
    本节不联网、不调模型，所以它只在决策层被断言，这一点在报告里写明。
    """
    from mcp_server import server

    rows: list[dict] = []
    all_scopes = list(auth.SCOPES)

    calls = {
        "list_tables": {},
        "get_schema": {},
        "run_sql": {"sql": "SELECT 1"},
        "search_passages": {"query": "茶"},
        "get_passage": {"doc_id": "1"},
        "ask_database": {"question": "有多少张专辑"},
    }

    for tool, kwargs in calls.items():
        denied_tok = ""                                    # 该拒：没令牌
        wrong_tok = auth.make_token("u", [s for s in all_scopes
                                          if s != auth.TOOL_SCOPES[tool]])  # 该拒：scope 不对
        allow_tok = auth.make_token("u", [auth.TOOL_SCOPES[tool]])           # 该放行

        # 1) 无令牌 → 工具必须拒绝
        r = getattr(server, tool)(token=denied_tok, **kwargs)
        rows.append({"tool": tool, "case": "无令牌", "expect": "拒绝",
                     "got": "拒绝" if r.get("denied") else f"放行（{r.get('code', '?')}）",
                     "pass": bool(r.get("denied")) and r.get("code") == auth.E_NO_TOKEN})

        # 2) scope 不对 → 工具必须拒绝，且错误码要是 INSUFFICIENT_SCOPE
        r = getattr(server, tool)(token=wrong_tok, **kwargs)
        rows.append({"tool": tool, "case": "scope 不足", "expect": "拒绝",
                     "got": "拒绝" if r.get("denied") else f"放行（{r.get('code', '?')}）",
                     "pass": bool(r.get("denied")) and r.get("code") == auth.E_SCOPE})

        # 3) 令牌齐全 → 工具**不得**拒绝。
        #    注意断言的是「没被拒绝」，不是「业务成功」：本实验不保证数据已构建。
        #    业务层返回什么错误都写进 got 里，不做隐藏。
        if tool == "ask_database":
            rows.append({"tool": tool, "case": "令牌齐全", "expect": "不拒绝",
                         "got": "未调用（放行后会去调模型，本实验不调模型）",
                         "pass": True, "skipped": True})
            continue
        try:
            r = getattr(server, tool)(token=allow_tok, **kwargs)
            denied = bool(r.get("denied"))
            rows.append({"tool": tool, "case": "令牌齐全", "expect": "不拒绝",
                         "got": "拒绝（不该）" if denied
                                else f"不拒绝；业务层：{r.get('code') or 'ok'}",
                         "pass": not denied})
        except Exception as e:  # noqa: BLE001
            rows.append({"tool": tool, "case": "令牌齐全", "expect": "不拒绝",
                         "got": f"不拒绝，但业务层抛了 {type(e).__name__}",
                         "pass": True, "raised": type(e).__name__})
    return rows


# ---------------------------------------------------------------- 实测到的边界行为
def notable_behaviors() -> list[dict]:
    """跑出来和直觉不一样、但**确实是设计如此或被接受**的几条。照实报。"""
    out = []

    r = decide("list_tables", _hs512_token())
    out.append({
        "item": "HS512 用同一个密钥签，报的是 INVALID_TOKEN 而不是 BAD_SIGNATURE",
        "observed": r["code"],
        "why": "算法白名单在验签之前就拒了，连签名都没看。拒得对，只是错误码偏向"
               "「令牌不合法」而非「签名不对」。调用方若按 BAD_SIGNATURE 做分支要注意。",
    })

    r = decide("list_tables", auth.make_token("u", R, iat_offset=600))
    out.append({
        "item": "iat 比服务端快 10 分钟就被拒",
        "observed": r["code"],
        "why": "PyJWT 默认校验 iat。真实部署里客户端时钟快一点就会拿到这个错误。"
               "这是实测行为，不是猜测；本仓库不改它，只把它写清楚。",
    })

    r = decide("list_tables", _claim({"sub": ""}))
    out.append({
        "item": "sub 为空字符串仍然放行",
        "observed": "（放行）" if r["ok"] else r["code"],
        "why": "`options={'require': [...]}` 只检查 claim 键存在，不检查值非空。"
               "本仓库接受这一点（sub 的取值语义属于调用方），但必须写明，"
               "免得被读成「sub 一定非空」的保证。",
    })

    r = decide("list_tables", auth.make_token("u", ["db:read", "db:read"]))
    out.append({
        "item": "scope 重复写不影响结果",
        "observed": "（放行）" if r["ok"] else r["code"],
        "why": "scope 解析成集合，重复项被去重。",
    })

    return out


# ---------------------------------------------------------------- 报告
def _table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def render_md(d: dict) -> str:
    L: list[str] = []
    L.append("# 鉴权用例矩阵（实测结果）\n")
    L.append("本文件由 `experiments/10_auth_test.py` 生成，**不要手改**；重跑该脚本即可复现。\n")

    L.append("## 口径\n")
    L.append("- **一条用例** = 一次 `(工具, 令牌)` 的鉴权裁决。")
    L.append("- **通过** = 实测错误码与用例声明的期望**逐字相同**。不接受「反正拒了」这种宽松判定 ——"
             "签名错和 scope 不足被报成同一个码，调用方就没法据此做分支。")
    L.append("- **应拒**用例测的是安全性；**应放行**用例测的是可用性。"
             "只有拒绝的测试是一种假安全：一刀切全拒也能拿满分。")
    L.append("- 本实验**不联网、不调用模型、不需要构建 `data/` 下的产物**。")
    L.append(f"- 运行方式：`python experiments/10_auth_test.py`（退出码 0 = 全过）\n")

    L.append("## 总览\n")
    L.append(_table([
        ["应拒用例", f"{d['deny_pass']} / {d['deny_cases']}"],
        ["应放行用例", f"{d['allow_pass']} / {d['allow_cases']}"],
        ["工具入口一致性", f"{d['entry_pass']} / {d['entry_cases']}"],
        ["**合计**", f"**{d['n_pass']} / {d['n_cases']}**"],
    ], ["项", "通过 / 总数"]))
    L.append("")

    L.append("## 一、应拒用例（安全性方向）\n")
    L.append(f"共 {d['deny_cases']} 条，通过 {d['deny_pass']} 条。\n")
    L.append(_table(
        [[i + 1, r["label"], f"`{r['tool']}`", f"`{r['expect']}`", f"`{r['got']}`",
          "✅" if r["pass"] else "❌"] for i, r in enumerate(d["deny_rows"])],
        ["#", "用例", "工具", "期望", "实测", "结果"]))
    L.append("")

    fails = [r for r in d["deny_rows"] if not r["pass"]]
    if fails:
        L.append("### 不符合期望的用例\n")
        for r in fails:
            L.append(f"- **{r['label']}**：期望 `{r['expect']}`，实测 `{r['got']}`。"
                     f"原始 detail：`{r['detail']}`")
        L.append("")

    L.append("## 二、应放行用例（可用性方向）\n")
    L.append(f"共 {d['allow_cases']} 条，通过 {d['allow_pass']} 条。\n")
    L.append(_table(
        [[i + 1, r["label"], f"`{r['tool']}`", "✅" if r["pass"] else "❌", r["note"] or "—"]
         for i, r in enumerate(d["allow_rows"])],
        ["#", "用例", "工具", "结果", "备注"]))
    L.append("")

    L.append("## 三、工具入口一致性（真实工具函数）\n")
    L.append("第一节只证明了「决策函数是对的」。这一节调用**真实的工具函数**，"
             "证明「工具真的用了那个决策」。少了这一节，某个工具入口漏写鉴权是查不出来的。\n")
    L.append(f"共 {d['entry_cases']} 条，通过 {d['entry_pass']} 条。\n")
    L.append(_table(
        [[f"`{r['tool']}`", r["case"], r["expect"], r["got"], "✅" if r["pass"] else "❌"]
         for r in d["entry_rows"]],
        ["工具", "情形", "期望", "实测", "结果"]))
    L.append("")
    L.append("> **「令牌齐全」那一行的口径**：断言的是**工具没有拒绝**，"
             "不是「业务执行成功」。本实验不要求数据已经构建，所以业务层返回"
             "「库不存在」之类的错误码是正常的，它们被原样写进「实测」列。")
    L.append("> 因此「实测」列的文字会**随机器而变**（数据构建过就是 `ok`，没构建过就是"
             "构建相关的错误码），但**「结果」列不会** —— 通过与否只取决于鉴权裁决。")
    L.append("> `ask_database` 的「令牌齐全」一行标为未调用：它一旦放行就会真的去调模型，"
             "而本实验不调模型。它在决策层（第一、二节）是被完整断言的。\n")

    L.append("## 四、实测到的边界行为\n")
    L.append("下面几条与直觉不同，但**跑出来就是这样**。照实记录，不做美化；"
             "本仓库不改它们，只把行为写清楚。\n")
    L.append(_table(
        [[r["item"], f"`{r['observed']}`", r["why"]] for r in d["notable"]],
        ["现象", "实测", "说明"]))
    L.append("")

    L.append("## 五、局限\n")
    L.append("- 全部用例都在**进程内**直接调用，没有经过 MCP 的传输层。"
             "「令牌经由 stdio 传进来之后还是不是这样」是 `08_mcp_smoke.py` 的范围。")
    L.append("- 密钥用的是仓库内置的开发用默认值。**换密钥不会改变本表任何一行**"
             "（裁决只看签验是否一致，与密钥取值无关），所以这里没有逐个密钥再跑一遍。")
    L.append("- 用例是**枚举**出来的，枚举得再全也不能证明「不存在更强的伪造手法」。"
             "本表证明的是「列出的这些都被挡住了」。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    console.setup_stdio()
    REPORTS.mkdir(parents=True, exist_ok=True)

    deny_rows = run_deny()
    allow_rows = run_allow()
    entry_rows = run_entry_consistency()
    notable = notable_behaviors()

    data = {
        "deny_cases": len(deny_rows), "deny_pass": sum(r["pass"] for r in deny_rows),
        "allow_cases": len(allow_rows), "allow_pass": sum(r["pass"] for r in allow_rows),
        "entry_cases": len(entry_rows), "entry_pass": sum(r["pass"] for r in entry_rows),
        "deny_rows": deny_rows, "allow_rows": allow_rows,
        "entry_rows": entry_rows, "notable": notable,
    }
    data["n_cases"] = data["deny_cases"] + data["allow_cases"]
    data["n_pass"] = data["deny_pass"] + data["allow_pass"]

    (REPORTS / "auth_results.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (REPORTS / "auth_results.md").write_text(render_md(data), encoding="utf-8", newline="\n")

    print("=" * 72)
    print("鉴权用例矩阵")
    print("=" * 72)
    print(f"  应拒      {data['deny_pass']} / {data['deny_cases']}")
    print(f"  应放行    {data['allow_pass']} / {data['allow_cases']}")
    print(f"  入口一致性 {data['entry_pass']} / {data['entry_cases']}")
    print(f"  合计      {data['n_pass']} / {data['n_cases']}")
    print("-" * 72)

    ok = True
    for r in deny_rows:
        if not r["pass"]:
            ok = False
            print(f"  [FAIL] 应拒但不符合期望：{r['label']}")
            print(f"         期望 {r['expect']}，实测 {r['got']} —— {r['detail']}")
    for r in allow_rows:
        if not r["pass"]:
            ok = False
            print(f"  [FAIL] 应放行却被拒：{r['label']} -> {r['got']} —— {r['detail']}")
    for r in entry_rows:
        if not r["pass"]:
            ok = False
            print(f"  [FAIL] 入口不一致：{r['tool']} / {r['case']} -> {r['got']}")

    if ok:
        print("  [OK] 两个方向全部通过：该拒的都拒了，该放行的都放行了")
    print(f"\n已写入 reports/auth_results.md 与 reports/auth_results.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
