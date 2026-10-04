import os
import sys
import asyncio
from mcp import Client, StdioServerParameters
from dotenv import load_dotenv

load_dotenv()

if "TAVILY_API_KEY" not in os.environ:
    raise RuntimeError("Set TAVILY_API_KEY in your terminal before running")

SERVER_PATH = os.path.join(os.path.dirname(__file__), "..", "server", "tools_server.py")

SERVER = StdioServerParameters(
    command=sys.executable,
    args=[SERVER_PATH],
    env={"TAVILY_API_KEY": os.environ["TAVILY_API_KEY"]},   # the child process does not inherit your environment
)


async def list_anthropic_tools(client):
    """Ask the MCP server what tools it has, in the format the Anthropic API expects."""
    result = await client.list_tools()
    return [{"name": t.name,
             "description": t.description or "",
             "input_schema": t.input_schema} for t in result.tools]


async def call_tool(client, name, args):
    """Run one tool. Returns (text, is_error). Never raises."""
    try:
        result = await asyncio.wait_for(client.call_tool(name, args), timeout=60)
    except asyncio.TimeoutError:
        return f"{name} timed out after 60s", True
    except Exception as e:
        return f"{name} failed: {e}", True
    text = "".join(c.text for c in result.content if c.type == "text")
    return text, bool(result.is_error)