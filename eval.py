from dotenv import load_dotenv
load_dotenv()

import sys, json, asyncio
from mcp import Client
from agent.mcp_client import SERVER, list_anthropic_tools
from agent.graph import run_agent


async def run_one(question, compact):
    async with Client(SERVER) as client:
        tools = await list_anthropic_tools(client)
        return await run_agent(question, client, tools, compact=compact)

async def main(compact):
    questions = [q.strip() for q in open("questions.txt", encoding="utf-8") if q.strip()]
    out_file = f"results_{'on' if compact else 'off'}.jsonl"

    for q in questions:
        try:
            result = await run_one(q, compact)
        except Exception as e:
            print("CRASH:", type(e).__name__, str(e)[:200], "|", q[:50])

        trace = result.trace
        row = {
            "question": q,
            "compact": compact,
            "stop_reason": result.stop_reason,
            "steps": result.steps_used,
            "cost": result.cost_usd,
            "compactions": sum(1 for t in trace if t.get("event") == "compacted"),
            "duplicates": sum(1 for t in trace if t.get("note", "").startswith("Duplicate")),
            "failed_calls": sum(1 for t in trace if t.get("error")),
            "fetched_ok": sum(1 for t in trace if t.get("tool") == "fetch_url" and not t["error"]),
            "notes": result.notes,
            "errors": [t["note"] for t in trace if t.get("error")],
            "report": result.report.model_dump() if result.report else None,
        }

        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{result.stop_reason:14} steps={result.steps_used:2} cost=${result.cost_usd:.3f}  {q[:60]}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] == "on"))