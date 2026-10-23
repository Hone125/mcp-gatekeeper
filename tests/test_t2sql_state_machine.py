"""Text2SQL 状态机的**控制流**测试 —— 全部用假模型驱动，不联网、不花钱。

## 为什么要把模型换掉

`t2sql_core.run()` 里混着两件性质完全不同的东西：

1. **控制流**：重写几次、失败原因怎么回灌、次数用尽后返回什么、什么时候该停。
   纯逻辑，给定输入就有确定的输出。
2. **SQL 写得好不好**：只有真模型能回答。

不切开的话，「第一次被护栏拦下后会不会重写」这条断言就得真的调一次模型 ——
一条本该 5 毫秒跑完、跑一万次结果都一样的测试，变成一条要联网、要花钱、
会因为限流而随机挂掉的测试。切开的代价只是多一个 `llm` 参数。

下面用 `FakeLLM` 把控制流单独顶住。真模型的表现是 `experiments/09_text2sql_eval.py` 的事。

## 两套实现，同一批断言

同一个状态机有两套实现：手写版 `t2sql_core.run()` 与 LangGraph 版 `t2sql_graph.run()`。
所有调用 `run()` 的用例都通过 `impl` 这个 fixture 拿被测实现，于是**每条各跑两遍**
（用例 id 里的 `[hand]` / `[graph]`）。控制流测试本来就只看「给什么剧本、模型被怎么调、
返回什么」，不看内部怎么写 —— 正好拿来当两版共同的行为规格。

参数化只能证明「两版都满足同一组性质」。文件末尾的对照用例再往前一步：
同一剧本下，两版发给模型的每一轮消息、返回体，**逐字相同**。
"""
from __future__ import annotations

import re

import pytest

from mcp_server import guardrails as g
from mcp_server import t2sql_core as core
from mcp_server import t2sql_graph as graph


class FakeLLM:
    """按剧本回复的假模型。**它记录自己被怎么调用** —— 断言的一半在它身上。

    记录下来的 `seen` 让「回灌的上下文里到底有没有上一次的 SQL 和失败原因」
    这种断言成为可能。只断言「调用了 3 次」是不够的：重试 3 次但每次都拿着
    一模一样的消息，和根本没重试没有区别。
    """

    def __init__(self, replies: list[str], usage: dict | None = None):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []
        self.labels: list[str] = []
        self.usage = usage or {"prompt_tokens": 11, "completion_tokens": 7}

    def __call__(self, messages: list[dict], label: str = "") -> dict:
        self.seen.append([dict(m) for m in messages])
        self.labels.append(label)
        i = len(self.seen) - 1
        text = self.replies[i] if i < len(self.replies) else self.replies[-1]
        return {"text": text, "model": "fake-model", "usage": self.usage}


@pytest.fixture(params=[core, graph], ids=["hand", "graph"])
def impl(request):
    """被测的那一套实现：手写版或 LangGraph 版。参数里带上它的用例自动各跑一遍。

    参数化写在 fixture 上、而不是在每条用例上写 `parametrize`：两版的名单只在这一处，
    新加的用例只要参数里带上 `impl`、调用写成 `impl.run(...)`，就两版都测到了。
    """
    return request.param


def _run(impl, tiny_db, replies, **kw):
    fake = FakeLLM(replies)
    out = impl.run("有多少张专辑", db_path=tiny_db, llm=fake, record_cost=False, **kw)
    return fake, out


# ---------------------------------------------------------------- 上下文拼装
def test_the_prompt_carries_the_schema_not_a_hardcoded_one(impl, tiny_db):
    """结构是从**当次连接的库**里读出来的，不是写死在提示词里的。

    这条是「换个库还能用」的前提。写死的话，换一份数据之后模型会拿着
    上一份数据的表名去写 SQL，而且失败原因看起来像「模型不听话」。
    """
    fake, _ = _run(impl, tiny_db, ["SELECT 1"])
    user = fake.seen[0][1]["content"]
    assert "artist" in user and "album" in user
    assert "CREATE TABLE" not in user, "不该把建表语句原样塞进提示词"


def test_three_versions_differ_only_in_the_tail(impl, tiny_db):
    """三版提示词必须能 diff 出差别，而不是三份各自演化、看不出关系的文本。"""
    texts = {}
    for v in core.VERSIONS:
        fake, _ = _run(impl, tiny_db, ["SELECT 1"], version=v)
        texts[v] = fake.seen[0][1]["content"]
    assert texts["v1"] != texts["v2"] != texts["v3"]
    # 前面那一大段（结构说明）应当一致，差别只在末尾的追加要求
    head = texts["v1"].split("请写出能回答上述问题的 SQL。")[0]
    for v in ("v2", "v3"):
        assert texts[v].startswith(head), f"{v} 与 v1 的结构说明部分不一致"
    assert "UNSUPPORTED" not in texts["v2"], "v3 才引入自救标记，v2 不该有"
    assert "UNSUPPORTED" in texts["v3"]


def test_unknown_version_is_a_loud_error():
    with pytest.raises(ValueError):
        core.prompt_for("v9")


# ---------------------------------------------------------------- 从回复里取 SQL
@pytest.mark.parametrize("reply,kind,sql", [
    ("SELECT 1", "sql", "SELECT 1"),
    ("```sql\nSELECT 1\n```", "sql", "SELECT 1"),
    ("```\nSELECT 1\n```", "sql", "SELECT 1"),
    ("好，这样写：\nSELECT 1\n", "sql", "SELECT 1"),
    ("WITH x AS (SELECT 1) SELECT * FROM x", "sql", "WITH x AS (SELECT 1) SELECT * FROM x"),
    # 围栏外有解释文字时，优先取围栏里的
    ("解释一下：\n```sql\nSELECT 2\n```\n以上。", "sql", "SELECT 2"),
    # 注释行不该被当成语句的一部分带出去
    ("```sql\n-- 说明\nSELECT 3\n```", "sql", "SELECT 3"),
    ("", "empty", ""),
    ("我不太确定你的意思", "empty", ""),
    ("-- UNSUPPORTED 库里没有作曲家的表", "unsupported", ""),
    ("```sql\n-- UNSUPPORTED 缺少专辑发行年份\n```", "unsupported", ""),
])
def test_extract_sql_classifies_replies(reply, kind, sql):
    got = core.extract_sql(reply)
    assert got["kind"] == kind, got
    assert got["sql"] == sql, got


def test_unsupported_and_empty_are_not_the_same_thing():
    """★ 「模型说答不了」和「模型说了句废话」必须分开。

    混成一个「没拿到 SQL」，报告里就分不清「这题本来就超出数据范围」
    和「模型输出格式不对」—— 前者是可以计入分母的有效结论，后者是一次失败。
    """
    a = core.extract_sql("-- UNSUPPORTED 缺少销量表")
    b = core.extract_sql("让我想想……")
    assert a["kind"] == "unsupported" and b["kind"] == "empty"
    assert a["reason"] and b["reason"]


# ---------------------------------------------------------------- 回灌文本
def test_three_kinds_of_failure_get_three_different_nudges():
    """★ 三种失败要用三种改法。

    统一回一句「请重写」，模型只能瞎猜哪里错了 —— 它会把语法问题当成权限问题去改，
    或者反过来。这里断言三句话**两两不同**，并且各自提到了对应的原因。
    """
    static = core.feedback_for({"blocked": True, "rule": g.R_FORBIDDEN_KEYWORD,
                                "reason": "命中写操作/管理关键字：DROP"})
    engine = core.feedback_for({"blocked": True, "rule": g.R_ENGINE_DENY,
                                "reason": "not authorized"})
    execerr = core.feedback_for({"blocked": False, "error": "no such column: titel"})
    assert len({static, engine, execerr}) == 3
    assert g.R_FORBIDDEN_KEYWORD in static
    assert "pragma_" in engine, "被引擎拦下时要提示『别用表值 pragma』，否则模型会一直换语法重试"
    assert "no such column" in execerr


# ---------------------------------------------------------------- 控制流
def test_first_attempt_success_calls_the_model_exactly_once(impl, tiny_db):
    fake, out = _run(impl, tiny_db, ["SELECT name FROM artist"])
    assert out["ok"] is True and out["status"] == "ok"
    assert len(fake.seen) == 1
    assert out["attempts"] == 1 and out["max_attempts"] == 3
    assert out["rows"] == [["A"], ["B"]]


def test_a_blocked_first_attempt_is_retried_with_the_reason(impl, tiny_db):
    """★ 核心路径：第一次写了个 DROP，被护栏拦下，改写后成功。"""
    fake, out = _run(impl, tiny_db, [
        "DROP TABLE artist",                                    # 第一次：被 L1 拦
        "```sql\nSELECT name FROM artist\n```",                 # 第二次：成功
    ])
    assert out["ok"] is True and out["status"] == "ok"
    assert len(fake.seen) == 2
    assert out["attempts"] == 2
    assert out["history"][0]["blocked"] is True
    assert out["history"][0]["rule"] == g.R_FORBIDDEN_KEYWORD
    assert out["history"][1]["ok"] is True

    # 第二次的上下文里必须带着上一次的 SQL 和被拒的原因
    second = fake.seen[1]
    assert second[-2]["role"] == "assistant" and "DROP TABLE artist" in second[-2]["content"]
    assert second[-1]["role"] == "user" and g.R_FORBIDDEN_KEYWORD in second[-1]["content"]
    # 而且原来的系统提示词和结构说明还在（不能把上下文整个换掉）
    assert second[0]["role"] == "system"
    assert second[1]["content"] == fake.seen[0][1]["content"]


def test_an_execution_error_is_retried_differently_from_a_block(impl, tiny_db):
    """护栏放行但 SQL 执行失败 —— 这时该改的是**语义**（列名），不是写法。"""
    fake, out = _run(impl, tiny_db, [
        "SELECT titel FROM album",          # 列名拼错，护栏放行、执行报错
        "SELECT title FROM album",
    ])
    assert out["ok"] is True and len(fake.seen) == 2
    assert out["history"][0]["blocked"] is False
    assert "no such column" in out["history"][0]["error"].lower()
    assert "no such column" in fake.seen[1][-1]["content"]


def test_exhaustion_keeps_the_whole_failure_scene(impl, tiny_db):
    """★ 次数用尽后返回**完整失败现场**，而不是一个干净的「失败」错误码。

    三次各自的 SQL、被哪条规则拦下、报错原文，全部留在 `history` 里。
    这些是台账里最值钱的部分 —— 它们是真实模型踩过的坑，丢掉就没了。
    只返回一个 `EXHAUSTED` 好看，但什么都没留下。
    """
    fake, out = _run(impl, tiny_db, ["DROP TABLE artist"] * 5)
    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert len(fake.seen) == 3, "上限就是 3 次，第 4 次不该发生"
    assert out["attempts"] == 3 == len(out["history"])
    assert out["sql"] == "DROP TABLE artist", "要留下最后一次的 SQL"
    for h in out["history"]:
        assert h["blocked"] is True and h["rule"] == g.R_FORBIDDEN_KEYWORD


def test_status_distinguishes_blocked_from_failed(impl, tiny_db):
    """耗尽时的 status 要能区分「一直过不了护栏」和「一直执行报错」。"""
    _, blocked = _run(impl, tiny_db, ["DROP TABLE artist"] * 3)
    _, failed = _run(impl, tiny_db, ["SELECT titel FROM album"] * 3)
    assert blocked["status"] == "blocked" and blocked["error"]
    assert failed["status"] == "failed" and "no such column" in failed["error"]


def test_a_model_that_answers_unsupported_stops_immediately(impl, tiny_db):
    """★ 说「答不了」是一个**有效结论**，不该被当成失败再去重写两次。

    它只值一次调用。如果这里变成 3 次，那么「模型明确知道超出范围」的题
    会白烧两次调用，而且报告里会把它们记成失败。
    """
    fake, out = _run(impl, tiny_db, ["-- UNSUPPORTED 库里没有作曲家的表"] * 3)
    assert len(fake.seen) == 1, "说了 UNSUPPORTED 就不该再试"
    assert out["status"] == "unsupported" and out["ok"] is False
    assert out["attempts"] == 1
    assert "作曲家的表" in out["error"]


def test_an_empty_reply_gets_a_format_nudge_and_counts_as_an_attempt(impl, tiny_db):
    fake, out = _run(impl, tiny_db, ["让我想想……", "SELECT name FROM artist"])
    assert out["ok"] is True and len(fake.seen) == 2
    assert out["history"][0]["kind"] == "empty"
    nudge = fake.seen[1][-1]["content"]
    assert "SELECT" in nudge and "不要写解释" in nudge


def test_the_repair_budget_is_a_named_constant(impl, tiny_db):
    """上限必须是模块级常量，不是散在代码里的魔数。"""
    assert core.MAX_REPAIR == 2
    assert core.MAX_ATTEMPTS == core.MAX_REPAIR + 1
    _, out = _run(impl, tiny_db, ["DROP TABLE artist"] * 9)
    assert out["max_attempts"] == core.MAX_ATTEMPTS


# ---------------------------------------------------------------- 用量
def test_usage_accumulates_over_attempts(impl, tiny_db):
    """用量按**每次调用**累加，不是只记最后一次 —— 否则重试的成本被抹掉了。"""
    fake, out = _run(impl, tiny_db, ["DROP TABLE artist", "SELECT name FROM artist"])
    assert len(fake.seen) == 2
    assert out["usage"]["calls"] == 2
    assert out["usage"]["prompt_tokens"] == 22
    assert out["usage"]["completion_tokens"] == 14


def test_usage_survives_a_model_that_reports_none(impl, tiny_db):
    """有些网关不回 usage。**不许因此崩掉**，只能记 0 并在别处说明。"""
    fake = FakeLLM(["SELECT name FROM artist"])
    fake.usage = None  # type: ignore[assignment]
    out = impl.run("q", db_path=tiny_db, llm=fake, record_cost=False)
    assert out["ok"] is True
    assert out["usage"]["calls"] == 1
    assert out["usage"]["prompt_tokens"] == 0


def test_usage_survives_a_dict_shaped_usage(impl, tiny_db):
    """usage 可能是对象也可能是字典，两种都要认。"""
    fake = FakeLLM(["SELECT 1"], usage={"prompt_tokens": 3, "completion_tokens": 4})
    out = impl.run("q", db_path=tiny_db, llm=fake, record_cost=False)
    assert out["usage"]["prompt_tokens"] == 3


# ---------------------------------------------------------------- 契约
def test_result_shape_is_stable_across_every_status(impl, tiny_db):
    """四种结局的返回体字段**完全一致**，只取值不同。

    字段时有时无的话，调用方每次都得 `if "rows" in out` —— 那种代码迟早漏一处。
    """
    keys = None
    for replies in (["SELECT name FROM artist"], ["DROP TABLE artist"] * 3,
                    ["-- UNSUPPORTED 缺表"] * 3, ["让我想想……"] * 3):
        _, out = _run(impl, tiny_db, replies)
        if keys is None:
            keys = set(out)
        assert set(out) == keys, f"返回体字段不一致：{set(out) ^ keys}"


def test_run_never_raises_on_a_hostile_model(impl, tiny_db):
    """模型回什么都行 —— 空、超长、纯符号、带控制字符。跑到底，不抛异常。"""
    for reply in ["", " " * 100, "\x00\x01", "```", "SELECT", "--", "🎉" * 50,
                  "SELECT " + "x" * 5000]:
        _, out = _run(impl, tiny_db, [reply] * 3)
        assert isinstance(out, dict) and out["status"] in ("ok", "blocked", "failed", "unsupported")


def test_a_missing_database_is_reported_not_raised(impl, tmp_path):
    """库没建时要把「先跑构建脚本」这句话带出来，而不是抛一个 FileNotFoundError。"""
    fake = FakeLLM(["SELECT 1"])
    out = impl.run("q", db_path=tmp_path / "nope.db", llm=fake, record_cost=False)
    assert out["ok"] is False
    assert "01_build_db.py" in str(out), "错误信息要指向修复动作"


# ---------------------------------------------------------------- 两版对照
@pytest.mark.parametrize("replies", [
    ["SELECT name FROM artist"],
    ["DROP TABLE artist", "```sql\nSELECT name FROM artist\n```"],
    ["SELECT titel FROM album", "SELECT title FROM album"],
    ["DROP TABLE artist"] * 3,
    ["SELECT titel FROM album"] * 3,
    ["-- UNSUPPORTED 库里没有作曲家的表"],
    ["让我想想……", "SELECT name FROM artist"],
    ["让我想想……", "DROP TABLE artist", "SELECT * FROM pragma_table_info('album')"],
], ids=["ok", "blocked-then-ok", "error-then-ok", "blocked-x3", "failed-x3",
        "unsupported", "empty-then-ok", "empty-static-engine"])
def test_both_implementations_send_and_return_exactly_the_same(tiny_db, replies):
    """★ 同一剧本下，两版**发给模型的每一轮消息**和**返回体**逐字相同。

    上面的参数化用例只能断言「两版都满足同一组性质」，这一条断言「两版是同一个行为」。
    它专门守着 `t2sql_graph.EMPTY_NUDGE`：那句格式提醒是从手写版照抄的（手写版没把它提成常量），
    改了一边忘了另一边时，上面只查子串的用例照样绿，这一条会红。
    最后一个剧本把「没给出 SQL / 静态规则拦下 / 引擎回调拦下」三种回灌串在同一次运行里。
    """
    a, b = FakeLLM(replies), FakeLLM(replies)
    out_a = core.run("有多少张专辑", db_path=tiny_db, llm=a, record_cost=False)
    out_b = graph.run("有多少张专辑", db_path=tiny_db, llm=b, record_cost=False)
    assert b.seen == a.seen, "两版发给模型的消息不一样"
    assert b.labels == a.labels, "两版给模型调用打的标签不一样"
    assert out_b == out_a, "两版的返回体不一样"


# ---------------------------------------------------------------- 多轮追问（LangGraph 版独有）
def _session(tiny_db):
    """一个挂着 InMemorySaver 的会话。返回 `ask(thread, 问题, 剧本) -> (假模型, 返回体)`。"""
    from langgraph.checkpoint.memory import InMemorySaver

    g = graph.build_graph(InMemorySaver())

    def ask(thread, question, replies):
        fake = FakeLLM(replies)
        out = graph.ask(g, thread, question, db_path=tiny_db, llm=fake, record_cost=False)
        return fake, out
    return ask


def test_a_follow_up_carries_the_previous_question_and_its_final_sql(tiny_db):
    """★ 第二问发给模型的消息里，要带着第一问问了什么、最后用的哪条 SQL。

    插的位置也钉住：系统规则 → 之前的「问 → SQL」→ 这一问。这一问仍是最后一条，
    结构说明仍只在这一问里出现一次。
    """
    ask = _session(tiny_db)
    _, first = ask("s1", "每个艺术家有几张专辑",
                   ["SELECT artist_id, COUNT(*) AS n FROM album GROUP BY artist_id"])
    fake, second = ask("s1", "那只看前 1 个",
                       ["SELECT artist_id, COUNT(*) AS n FROM album GROUP BY artist_id "
                        "ORDER BY n DESC LIMIT 1"])
    msgs = fake.seen[0]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "每个艺术家有几张专辑" in msgs[1]["content"]
    assert msgs[2]["content"] == first["sql"], "要带上一问**最终执行**的那条 SQL"
    assert "那只看前 1 个" in msgs[3]["content"] and "数据库结构" in msgs[3]["content"]
    assert sum("数据库结构" in m["content"] for m in msgs) == 1, "结构说明不该每一问都重复带"
    assert second["ok"] is True and second["rows"] == [[1, 2]]


def test_per_question_fields_start_fresh_in_the_same_thread(tiny_db):
    """★ 坑：`history` 带 operator.add，同一个 thread 里会跨问题累加 —— 每问必须从空现场开始。

    后半段是负控：绕开 `ask()`、不清空 history 直接 invoke 第二问，第二问的现场里**确实**
    混进了第一问的。没有这一半，前半段的断言可能只是恒真。
    """
    ask = _session(tiny_db)
    _, first = ask("s1", "q1", ["DROP TABLE artist", "SELECT name FROM artist"])
    _, second = ask("s1", "q2", ["SELECT title FROM album"])
    assert first["attempts"] == 2
    assert second["attempts"] == 1 and len(second["history"]) == 1
    assert second["usage"]["calls"] == 1, "用量也是每问独立的"
    assert second["history"][0]["sql"] == "SELECT title FROM album"

    from langgraph.checkpoint.memory import InMemorySaver

    g = graph.build_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    ctx = graph.T2SQLContext(llm=FakeLLM(["SELECT name FROM artist"]), db_path=tiny_db,
                             record_cost=False)
    base = {"version": "v3", "attempt": 0,
            "usage": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}}
    g.invoke({**base, "question": "q1"}, context=ctx, config=cfg)
    leaked = g.invoke({**base, "question": "q2", "history": []}, context=ctx, config=cfg)
    assert len(leaked["history"]) == 2, "不清空时 history 本该累加 —— 负控没起作用"


def test_threads_do_not_share_memory(tiny_db):
    """不同的 thread_id 是不同的会话：另一个会话问过什么，这里一个字都不该看到。"""
    ask = _session(tiny_db)
    ask("alice", "每个艺术家有几张专辑",
        ["SELECT artist_id, COUNT(*) AS n FROM album GROUP BY artist_id"])
    fake, _ = ask("bob", "那只看前 1 个", ["SELECT 1"])
    assert [m["role"] for m in fake.seen[0]] == ["system", "user"], "另一个会话的上下文漏进来了"


def test_the_readme_diagram_is_the_one_the_code_draws(root):
    """README 里那张流程图必须就是代码现画出来的那张。手抄一张图，迟早和代码对不上。"""
    text = (root / "README.md").read_text(encoding="utf-8")
    m = re.search(r"```mermaid\n(.*?)```", text, re.S)
    assert m, "README 里找不到 mermaid 图"
    drawn = graph.GRAPH.get_graph().draw_mermaid()
    assert m.group(1).strip() == drawn.strip(), (
        "README 的流程图和代码对不上。重新导出：\n"
        "  python -c \"from mcp_server.t2sql_graph import GRAPH; "
        "print(GRAPH.get_graph().draw_mermaid())\"")
