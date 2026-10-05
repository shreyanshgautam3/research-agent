from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import asyncio
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from mcp import Client
from agent.mcp_client import SERVER, list_anthropic_tools
from agent.graph import run_agent

app = FastAPI(title="Research Agent")

API_KEY = os.environ["API_KEY"]
SPEND_LIMIT = float(os.getenv("SPEND_LIMIT_USD", "3"))
COMPACT = os.getenv("COMPACT", "off") == "on"
MAX_ACTIVE = 3

runs = {}
spend = {"total": 0.0}
lock = asyncio.Lock()


class ResearchRequest(BaseModel):
    question: str


def check_key(key):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


async def do_run(run_id, question):
    async with lock:
        runs[run_id]["status"] = "running"
        try:
            async with Client(SERVER) as client:
                tools = await list_anthropic_tools(client)
                result = await run_agent(question, client, tools, compact=COMPACT)
            spend["total"] += result.cost_usd
            runs[run_id]["result"] = result.model_dump()
            runs[run_id]["status"] = "done"
        except Exception as e:
            runs[run_id]["status"] = "error"
            runs[run_id]["error"] = f"{type(e).__name__}: {str(e)[:200]}"


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/research")
async def start_research(req: ResearchRequest, background: BackgroundTasks,
                         x_api_key: str = Header(default="")):
    check_key(x_api_key)
    if not req.question.strip() or len(req.question) > 500:
        raise HTTPException(status_code=400, detail="question must be 1-500 characters")
    if spend["total"] >= SPEND_LIMIT:
        raise HTTPException(status_code=429, detail="spend limit reached")
    active = sum(1 for r in runs.values() if r["status"] in ("queued", "running"))
    if active >= MAX_ACTIVE:
        raise HTTPException(status_code=429, detail="too many runs in progress")

    if len(runs) >= 50:
        runs.pop(next(iter(runs)))
    run_id = uuid.uuid4().hex[:8]
    runs[run_id] = {"status": "queued", "question": req.question}
    background.add_task(do_run, run_id, req.question)
    return {"run_id": run_id}


@app.get("/research/{run_id}")
async def get_research(run_id: str, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="unknown run id")
    return runs[run_id]


@app.get("/")
async def home():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))