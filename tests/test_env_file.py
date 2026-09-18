"""`.env` 的读取规矩。

这些用例的存在理由：`.env.example` 让读者「复制成 `.env` 再填值」。
如果没有任何代码读 `.env`，照着做的人填完仍然会拿到「缺少环境变量」——
而他无从知道问题不在自己身上。**这条链路必须有测试钉住，
否则它会以「文档说了、代码没做」的形式长期存在**（本仓真的这样存在过）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server import env_file

ROOT = Path(__file__).resolve().parents[1]


def test_reads_a_normal_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("LLM_API_KEY=sk-abc\nLLM_MODEL=some-model\n", encoding="utf-8")
    assert env_file.read(p) == {"LLM_API_KEY": "sk-abc", "LLM_MODEL": "some-model"}


def test_handles_quotes_comments_and_export(tmp_path):
    """引号 / `#` 注释 / `export` 前缀 / 空行 —— 手写正则最容易在这些地方错。"""
    p = tmp_path / ".env"
    p.write_text(
        "# 顶部的注释\n"
        "\n"
        "export LLM_API_KEY=\"sk-quoted\"\n"
        "LLM_BASE_URL='https://example.invalid/v1'\n"
        "LLM_MODEL=m   # 行尾注释\n",
        encoding="utf-8",
    )
    v = env_file.read(p)
    assert v["LLM_API_KEY"] == "sk-quoted"
    assert v["LLM_BASE_URL"] == "https://example.invalid/v1"
    assert v["LLM_MODEL"].strip() == "m"


def test_empty_values_are_dropped(tmp_path):
    """`LLM_API_KEY=` 是 `.env.example` 的默认形态 —— 空值等于没填。

    留着它会让下游拿到一个空字符串，`if not key` 照样报缺，但报错位置会离原因更远。
    """
    p = tmp_path / ".env"
    p.write_text("LLM_API_KEY=\nLLM_MODEL=x\n", encoding="utf-8")
    assert env_file.read(p) == {"LLM_MODEL": "x"}


def test_missing_file_is_not_an_error(tmp_path):
    """clone 下来本来就没有 `.env`（它在 .gitignore 里）。"""
    assert env_file.read(tmp_path / "nope.env") == {}
    assert env_file.apply(tmp_path / "nope.env") == {}


def test_a_broken_file_does_not_raise(tmp_path):
    p = tmp_path / ".env"
    p.write_bytes(b"\xff\xfe\x00 not utf-8 \x00")
    assert isinstance(env_file.read(p), dict)


def test_apply_does_not_override_the_real_environment(tmp_path, monkeypatch):
    """★ 显式设过的环境变量必须赢。

    否则「临时换个 key 试一下」会静默失效 —— 现象是「我明明改了 key，
    它还是用旧的那个」，这类 bug 排查成本极高。
    """
    p = tmp_path / ".env"
    p.write_text("LLM_API_KEY=from-file\nLLM_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    monkeypatch.delenv("LLM_MODEL", raising=False)

    applied = env_file.apply(p)

    assert os.environ["LLM_API_KEY"] == "from-env"      # 没被覆盖
    assert os.environ["LLM_MODEL"] == "from-file"       # 补上了
    assert applied == {"LLM_MODEL": "from-file"}        # 返回值只报真正生效的键


def test_the_shipped_env_example_declares_exactly_the_keys_the_code_reads():
    """`.env.example` 是给人抄的模板，键名必须和代码里读的那几个逐字一致。

    键名写错（比如 `API_KEY`）时用户得到的是「缺少环境变量 LLM_API_KEY」，
    而他手里那份 `.env` 明明填了 —— 又是一个把责任推给用户的小坑。

    这里**不比对 `read()` 的返回值**：模板里 `LLM_API_KEY=` 是空值，
    而空值会被 `read()` 丢掉（那是运行时的正确行为）。模板要检查的是**键名**。
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"} <= declared, sorted(declared)


class _FakeClient:
    """一个不联网的 OpenAI 客户端替身。记下构造参数和每次调用的参数。"""

    def __init__(self, **kw):
        self.kw = kw
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kw2):
                outer.calls.append(kw2)
                msg = type("M", (), {"content": "ok"})()
                choice = type("C", (), {"message": msg})()
                usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()
                return type("R", (), {"choices": [choice], "usage": usage})()

        self.chat = type("Chat", (), {"completions": _Completions()})()


class _Recorder:
    """记下 `OpenAI(...)` 是怎么被构造的。

    不直接把替身对象本身返回去：被替身的是**类**，`real_llm` 会拿关键字参数
    去构造它，所以必须在这里接住那次构造，才能断言「key / base_url 从哪来」。
    """

    def __init__(self):
        self.clients: list[_FakeClient] = []

    @property
    def last(self) -> _FakeClient:
        assert self.clients, "OpenAI(...) 根本没被构造"
        return self.clients[-1]


def _isolate_llm_env(monkeypatch, tmp_path, with_dotenv: bool) -> _Recorder:
    """把「进程环境」和「`.env`」两处都清干净，只留想测的那一处。"""
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / ".env"
    if with_dotenv:
        p.write_text("LLM_API_KEY=sk-from-dotenv\n"
                     "LLM_BASE_URL=https://dotenv.invalid/v1\n"
                     "LLM_MODEL=model-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", p)
    import openai
    rec = _Recorder()
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: (rec.clients.append(_FakeClient(**kw)),
                                                       rec.clients[-1])[1])
    return rec


def test_real_llm_refuses_when_neither_env_nor_dotenv_has_credentials(monkeypatch,
                                                                     tmp_path):
    """负控：两处都没有凭据时必须报缺。**没有这一条，下一条是空转的。**"""
    from mcp_server import t2sql_core

    _isolate_llm_env(monkeypatch, tmp_path, with_dotenv=False)
    with pytest.raises(RuntimeError, match="缺少环境变量"):
        t2sql_core.real_llm([{"role": "user", "content": "ping"}], "preflight")


def test_a_filled_in_dotenv_is_actually_enough_to_reach_the_model_call(monkeypatch,
                                                                     tmp_path):
    """★ 这条才是那个缺失链路的回归口径。

    **进程环境里一个 LLM_ 变量都没有，凭据只存在于 `.env` 里。**
    照 `.env.example` 做的人所处的正是这个状态 —— 如果他填完之后仍然报
    「缺少环境变量」，那么那份文档就是在骗人（本仓真的这样过）。

    断言落到「模型被真的调用了、用的模型名来自 `.env`」，
    而不是「没有报错」：后者在一个把凭据吞掉、然后静默返回空串的实现上也会通过。
    """
    from mcp_server import t2sql_core

    rec = _isolate_llm_env(monkeypatch, tmp_path, with_dotenv=True)
    out = t2sql_core.real_llm([{"role": "user", "content": "ping"}], "preflight")

    assert out["model"] == "model-from-dotenv"      # 模型名确实来自 .env
    assert rec.last.calls and rec.last.calls[0]["model"] == "model-from-dotenv"
    assert rec.last.kw["api_key"] == "sk-from-dotenv"   # 客户端拿 .env 里的 key 建的
    assert rec.last.kw["base_url"] == "https://dotenv.invalid/v1"


def test_an_explicit_env_var_wins_over_the_dotenv_file(monkeypatch, tmp_path):
    """反过来也要钉住：显式设置的环境变量优先于 `.env` 文件。"""
    from mcp_server import t2sql_core

    rec = _isolate_llm_env(monkeypatch, tmp_path, with_dotenv=True)
    monkeypatch.setenv("LLM_MODEL", "model-from-env")

    out = t2sql_core.real_llm([{"role": "user", "content": "ping"}], "preflight")

    assert out["model"] == "model-from-env"
    assert rec.last.calls[0]["model"] == "model-from-env"


@pytest.mark.parametrize("name", [".env"])
def test_the_dotenv_file_is_gitignored(name):
    """`.env` 里有密钥。它**必须**被忽略 —— 这条检查放在这里，
    是因为本模块正是那个「会去读它」的地方，两件事该一起被看见。"""
    out = subprocess.run(["git", "check-ignore", "-v", name], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         cwd=str(ROOT), timeout=30)
    assert out.returncode == 0, f"{name} 没有被 .gitignore 忽略：{out.stdout}{out.stderr}"
