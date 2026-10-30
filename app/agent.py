"""A minimal agent: the ReAct loop (Reason -> Act -> Observe -> repeat).

Agentic AI = the LLM decides WHICH tool to use and WHEN to stop,
instead of us hard-coding the steps.

Loop:
  1. Send the LLM the question + tool list + what happened so far.
  2. LLM replies with JSON: {"action": "<tool name>", "input": "..."}
     or {"action": "final", "input": "<answer>"}.
  3. We run the tool, add the result to history, go back to 1.
  4. Safety: max_steps cap, unknown tools rejected, bad JSON handled.

No framework needed. LangChain / Google ADK do this same loop for you
with more features (memory, tracing, multi-agent).
"""
import ast
import json
import operator
import re
from typing import Callable

from app.llm import generate

# ---------- Tools ----------

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def calculator(expr: str) -> str:
    """Safe math evaluator. Never use eval() on LLM output!"""
    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression")
    try:
        return str(ev(ast.parse(expr, mode="eval").body))
    except (ValueError, SyntaxError, ZeroDivisionError) as e:
        return f"error: {e}"


def build_tools(search_fn: Callable[[str], str]) -> dict[str, tuple[Callable[[str], str], str]]:
    """Tool registry: name -> (function, description shown to the LLM)."""
    return {
        "search_docs": (search_fn, "Search the knowledge base. Input: a search query."),
        "calculator": (calculator, "Evaluate arithmetic like '12*(3+4)'. Input: expression."),
    }


# ---------- Loop ----------

def _system_prompt(tools) -> str:
    tool_lines = "\n".join(f"- {name}: {desc}" for name, (_, desc) in tools.items())
    return (
        "You are an agent that solves tasks step by step using tools.\n"
        f"Available tools:\n{tool_lines}\n\n"
        "Reply with ONLY one JSON object, no other text:\n"
        '{"action": "<tool name>", "input": "<tool input>"}  to use a tool, or\n'
        '{"action": "final", "input": "<answer>"}  when done.'
    )


def parse_action(text: str) -> dict:
    """Extract the JSON object even if the LLM wraps it in ```json fences or prose."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found")
    data = json.loads(match.group(0))
    if not isinstance(data, dict) or "action" not in data or "input" not in data:
        raise ValueError("JSON must have 'action' and 'input'")
    return {"action": str(data["action"]), "input": str(data["input"])}


def run_agent(question: str, tools: dict, max_steps: int = 5) -> tuple[str, list[dict]]:
    system = _system_prompt(tools)
    steps: list[dict] = []
    history = ""

    for _ in range(max_steps):
        prompt = f"TASK: {question}\n\nHISTORY:\n{history or '(none yet)'}\n\nNext JSON:"
        raw = generate(prompt, system=system)

        try:
            act = parse_action(raw)
        except (ValueError, json.JSONDecodeError) as e:
            # Tell the model what went wrong and let it retry (self-correction)
            history += f"\nYour last reply was invalid ({e}). Reply with valid JSON only."
            continue

        if act["action"] == "final":
            return act["input"], steps

        tool = tools.get(act["action"])
        if tool is None:
            observation = f"error: unknown tool '{act['action']}'"
        else:
            try:
                observation = tool[0](act["input"])
            except Exception as e:  # a broken tool must not crash the agent
                observation = f"error: tool failed: {e}"

        steps.append({"tool": act["action"], "input": act["input"], "output": observation[:1000]})
        history += f"\nACTION {act['action']}({act['input']}) -> {observation[:1000]}"

    return "Stopped: reached the step limit without a final answer.", steps
