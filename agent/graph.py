"""
Research agent as a LangGraph loop: agent -> tools -> agent ... -> synthesise

Context policy (what is kept, summarised, or dropped):
- Question + notes ---------> kept always (notes are the agent's real memory)
- Raw fetched page text ----> dropped right after extraction (never stored in messages)
- Search snippets ----------> kept unitl compaction, then dropped
- Old reasoning/tool turns -> summarised into notes: once history gets long, it is replace by "question + notes"
- Failed tool calls --------> kept as one short line
"""

import json, asyncio
from typing import TypedDict
import anthropic
from pydantic import ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.errors import GraphRecursionError
from .schemas import ResearchReport, RunResult, normalise, check_grounding
from .mcp_client import call_tool
from dotenv import load_dotenv

load_dotenv()

MAIN_MODEL = "claude-haiku-4-5"
EXTRACT_MODEL = "claude-haiku-4-5"
PRICES = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0)}
COMPACT_AFTER = 8       # number of messages before history is replaced by notes

SYSTEM = ("You are a research agent. Search the web, fetch the best sources, then stop."
          "Report facts only, no investment advice. Never cite a URL you did not fetch.")

client = anthropic.AsyncAnthropic()


def cost_of(model, usage):
    price_in, price_out = PRICES[model]
    return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000


async def extract_notes(question, url, page_text):
    """Turn a raw page into a few short notes. Returns (notes, cost)."""
    resp = await client.messages.create(
        model=EXTRACT_MODEL, max_tokens=400,
        messages=[{"role": "user", "content":
                   f"Question: {question}\nSource: {url}\n\nPage text:\n{page_text}\n\n"
                   "List only the facts relevant to the question as 2-5 short bullet points. "
                   "Copy numbers and dates exactly as written. If nothing is relevant, say so."}])
    return resp.content[0].text, cost_of(EXTRACT_MODEL, resp.usage)


class State(TypedDict):
    question: str
    messages: list
    notes: list
    fetched: set
    seen: set
    repeats: int
    cost: float
    steps: int
    trace: list
    stop_reason: str
    report: ResearchReport | None


def build_graph(session, tools, model, max_steps, max_cost, compact):

    async def agent(state):
        resp = await client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM,
            tools=tools, messages=state["messages"])
        cost = state["cost"] + cost_of(model, resp.usage)
        steps = state["steps"] + 1
        wants_tools = any(b.type == "tool_use" for b in resp.content)

        if cost > max_cost:
            reason = "budget"
        elif state["repeats"] >= 3:
            reason = "loop_detected"
        elif not wants_tools:
            reason = "completed"
        elif steps >= max_steps:
            reason = "max_steps"
        else:
            reason = ""

        return {"messages": state["messages"] + [{"role": "assistant", "content": resp.content}],
                "cost": cost, "steps": steps, "stop_reason": reason}

    async def tools_node(state):
        question = state["question"]
        notes, cost = list(state["notes"]), state["cost"]
        seen, fetched = set(state["seen"]), set(state["fetched"])
        trace, repeats = list(state["trace"]), state["repeats"]
        results = []

        for block in state["messages"][-1]["content"]:
            if block.type != "tool_use":
                continue

            key = block.name + json.dumps(block.input, sort_keys=True)
            if key in seen:
                text, is_error = "Duplicate call blocked. Try a different query or URL, or finish.", True
                repeats += 1
            else:
                seen.add(key)
                text, is_error = await call_tool(session, block.name, block.input)
                if is_error:
                    text = text[:200]                       # keep failed calls to one short line
                elif block.name == "fetch_url":
                    url = block.input["url"]
                    fetched.add(normalise(url))
                    text, extra = await extract_notes(question, url, text)   # raw page is dropped here
                    cost += extra
                    notes.append(f"[{url}]\n{text}")

            trace.append({"step": state["steps"], "tool": block.name, "args": block.input,
                          "error": is_error, "chars": len(text)})
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": text, "is_error": is_error})

        messages = state["messages"] + [{"role": "user", "content": results}]
        if compact and len(messages) > COMPACT_AFTER:
            messages = [{"role": "user", "content":
                         f"Question: {question}\n\nNotes so far:\n" + "\n\n".join(notes) +
                         "\n\nContinue researching, or stop if you have enough."}]

        return {"messages": messages, "notes": notes, "cost": cost, "seen": seen,
                "fetched": fetched, "trace": trace, "repeats": repeats}


    async def synthesise(state):
        tool = {"name": "submit_report", "description": "Submit the final report",
                "input_schema": ResearchReport.model_dump_json()}
        notes_text = "\n\n".join(state["notes"]) or "(no sources were fetched)"
        msgs = [{"role": "user", "content":
                    f"Question: {state["question"]}\n\nNotes from fetched sources:\n{notes_text}\n\n"
                    "Submit the final report with submi_report. Cite only URLs that appear in the notes. "
                    "If the notes are not enough, say so in limitations."}]
        cost = state["cost"]

        for _ in range(3):
            resp = await client.messages.create(
                model=model, max_tokens=2000, system=SYSTEM, tools=[tool],
                tool_choice={"type": "tool", "name": "submit_report"}, messages=msgs
            )
            cost += cost_of(model, resp.usage)
            block = next(b for b in resp.content if b.type == "tool_use")
            try:
                report = ResearchReport.model_validate(block.input)
                check_grounding(report, state["fetched"])
                return {"report": report, "cost": cost}
            except (ValidationError, ValueError) as e:
                msgs += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block.id,
                                                       "content": f"Invalid: {e}. Fix it and resubmit.",
                                                       "is_error": True}]}]

        return {"report": None, "cost": cost, "stop_reason": "error"}

    def route(state):
        return "synthesise" if state["stop_reason"] else "tools"


    graph = StateGraph(State)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools_node)
    graph.add_node("synthesise", synthesise)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, ["tools", "synthesise"])
    graph.add_edge("tools", "agent")
    graph.add_edge("synthesise", END)
    return graph.compile()


async def run_agent(question, session, tools, model=MAIN_MODEL,
                    max_steps=10, max_cost=0.40, compact=True, timeout=120) -> RunResult:
    graph = build_graph(session, tools, model, max_steps, max_cost, compact)
    start = {"question": question,
             "messages": [{"role": "user", "content": question}],
             "notes": [], "fetched": set(), "seen": set(), "repeats": 0,
             "cost": 0.0, "steps": 0, "trace": [], "stop_reason": "", "report": None}

    try:
        out = await asyncio.wait_for(
            graph.ainvoke(start, {"recursion_limit": 2 + max_steps + 10}), timeout)
    except (asyncio. TimeoutError, GraphRecursionError):
        return RunResult(report=None, stop_reason="error", steps_cost=0, cost_usd=0.0, trace=[])
    return RunResult(report=out["report"], stop_reason=out["stop_reason"],
                     steps_used=out["steps"], cost_usd=round(out["cost_usd"], 4), trace=out["trace"])