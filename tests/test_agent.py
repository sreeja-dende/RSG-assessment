"""Agent tests: a scripted fake LLM proves the loop logic without any API."""
from app import agent
from app.agent import calculator, parse_action, run_agent


def fake_llm(replies):
    it = iter(replies)
    return lambda prompt, system="": next(it)


def test_calculator_safe():
    assert calculator("12*(3+4)") == "84"
    assert calculator("10/0").startswith("error")
    assert calculator("__import__('os')").startswith("error")  # no code execution


def test_parse_action_handles_code_fences():
    raw = 'Sure!\n```json\n{"action": "calculator", "input": "2+2"}\n```'
    assert parse_action(raw) == {"action": "calculator", "input": "2+2"}


def test_agent_uses_tool_then_answers(monkeypatch):
    monkeypatch.setattr(agent, "generate", fake_llm([
        '{"action": "calculator", "input": "6*7"}',
        '{"action": "final", "input": "The answer is 42"}',
    ]))
    tools = agent.build_tools(lambda q: "unused")
    answer, steps = run_agent("what is 6*7?", tools)
    assert answer == "The answer is 42"
    assert steps == [{"tool": "calculator", "input": "6*7", "output": "42"}]


def test_agent_recovers_from_bad_json_and_unknown_tool(monkeypatch):
    monkeypatch.setattr(agent, "generate", fake_llm([
        "I think I should search",                        # invalid -> retried
        '{"action": "web_browse", "input": "x"}',         # unknown tool
        '{"action": "final", "input": "done"}',
    ]))
    answer, steps = run_agent("task", agent.build_tools(lambda q: ""))
    assert answer == "done"
    assert "unknown tool" in steps[0]["output"]


def test_agent_stops_at_step_limit(monkeypatch):
    monkeypatch.setattr(agent, "generate", lambda p, system="": '{"action": "calculator", "input": "1+1"}')
    answer, steps = run_agent("loop forever", agent.build_tools(lambda q: ""), max_steps=3)
    assert answer.startswith("Stopped")
    assert len(steps) == 3


def test_agent_survives_crashing_tool(monkeypatch):
    def boom(_):
        raise RuntimeError("db down")
    monkeypatch.setattr(agent, "generate", fake_llm([
        '{"action": "search_docs", "input": "x"}',
        '{"action": "final", "input": "ok"}',
    ]))
    answer, steps = run_agent("t", agent.build_tools(boom))
    assert answer == "ok" and "tool failed" in steps[0]["output"]
