from dotenv import load_dotenv
load_dotenv()

import sys
import asyncio
from mcp import Client
from agent.mcp_client import SERVER, list_anthropic_tools
from agent.graph import run_agent

DEFAULT_QUESTION = "What was Apple's total revenue in its most recent fiscal year, and which source says so?"


async def main(question):
    async with Client(SERVER) as client:
        tools = await list_anthropic_tools(client)
        result = await run_agent(question, client, tools)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    asyncio.run(main(question))