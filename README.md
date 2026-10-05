# research-agent

A web research agent built with LangGraph and Claude. It searches the web and reads pages through an MCP tool server, manages its own context, and returns source-grounded reports validated by a Pydantic schema.

**Live demo:** [ResearchAgent](https://researchagent-jc84.onrender.com/)

Access needs an API key. The service runs on a free instance that sleeps when idle, so the first request can take about a minute.

## What it does

You give it a research question. The agent loops through search, read and take notes, then writes a structured report:

- `summary`, `findings` (claim, source URLs, confidence), `limitations`, `as_of`
- run metadata: stop reason, steps used, cost in USD, and a full tool-call trace

Two rules are enforced in code. Every cited URL must be a page the agent actually fetched, and the final report is built from the agent's notes only. When the agent cannot find a figure, it says so instead of guessing.

This is a research tool, not investment advice.

## Architecture

```
 Browser / API client
        |  POST /research (X-API-Key)  ->  run_id
        |  GET  /research/{run_id}     ->  status, result
        v
 FastAPI (api/main.py): auth, spend limit, one run at a time, background task
        v
 +-- Agent (LangGraph) -------------------------------------------------+
 |   agent --> tools --> agent --> ... --> synthesise --> report        |
 |  (Claude)  (MCP calls,  (decide:       (schema-forced report         |
 |             note        continue       from notes only, then         |
 |             extraction) or stop)       grounding check)              |
 |  guards: max steps, cost cap, duplicate-call blocking, timeouts      |
 +----------------------+-----------------------------------------------+
                        | MCP over stdio (one server process per run)
                        v
 MCP server (server/tools_server.py)
   web_search -> Tavily        fetch_url -> HTTP, HTML or PDF text
   SSRF guard, size caps, redirect re-validation, declared User-Agent
```

Claude Sonnet 5.5 runs the loop and writes the final report. Claude Haiku 4.5 extracts notes from fetched pages. Both are set by environment variables.

## Design decisions

### LangGraph for control flow, hand-written nodes
The graph has three nodes: `agent` decides the next step, `tools` runs MCP tool calls and extracts notes, and `synthesise` writes the report. The context policy, guardrails and cost meter are plain Python inside those nodes. LangGraph provides the state, the edges and a loop backstop (the recursion limit). I did not use a prebuilt agent.

### Where logic lives across the MCP boundary
Safety sits next to the dangerous action. The MCP server blocks non-public addresses (SSRF), caps page size, follows redirects itself while re-checking every hop, and sends a declared User-Agent. Run-level logic lives in the agent: duplicate-call blocking, the cost cap, notes and validation. Each run starts its own server process.

### Context policy: what is kept, summarized, or dropped

| Item | Decision | Why |
|---|---|---|
| System prompt and question | Kept always | Anchors the task |
| Notes (a few lines per fetched page) | Kept always | The agent's memory. The final report uses only these |
| Raw page text | Dropped right after extraction | Largest token cost. A Haiku call distils it into notes first |
| Search results | Visible for one step. With compaction, reduced to a list of unfetched URLs | URLs are what the agent needs; snippets are cheap to re-find |
| Older reasoning and tool turns | With compaction: replaced by question + notes + "already tried" list | Limits context growth on long runs |
| Failed tool calls | Kept as one short line (up to 200 chars), listed under "already tried" after compaction | Stops the agent retrying the same failure |

Compaction runs when the message list exceeds 4 messages and always keeps the latest exchange. It is off in the deployed app (`COMPACT=off`), because the evaluation showed no reliable benefit on short runs.

### Structured output and grounding
The final call offers a `submit_report` tool whose schema is the Pydantic model. The reply is validated, and on failure the error goes back to the model for up to two retries. A grounding check then rejects any cited URL that was not fetched. It does not check that a page actually supports a claim (see FAILURES.md).

### Guardrails
- Max 10 steps, a $0.40 cost cap and a 120 s timeout per run.
- Exact duplicate tool calls are blocked, and three repeats stop the run (`loop_detected`).
- Tool errors become short one-line observations, so the agent adapts instead of crashing. Timeouts are retried once.
- Fetch safety: public http(s) addresses only, 10 MB download cap, 60,000 characters returned, PDFs read up to 15 pages.
- API: key required, one run at a time, a spend limit and a question-length limit.

## Evaluation

15 financial questions: 8 fact lookups, 4 two-company comparisons and 3 traps (false premise, an event that has not happened, an ambiguous company name). Each has an entry in `answer_key.md` with a primary-source URL and the date it was checked (3 Oct 2026). Scoring was done by hand: correct, partial, no answer or wrong figure. Traps were scored on behavior.

| | Compaction on | Compaction off |
|---|---|---|
| Correct or passed | 10 / 15 | 12 / 15 |
| Partial | 4 | 2 |
| No answer | 1 | 1 |
| Wrong figure | 0 | 0 |
| Total cost | $1.10 | $1.20 |
| Cost per fully correct answer | $0.11 | $0.10 |
| Total steps | 70 | 70 |

How to read this:
- **No wrong figures in 30 runs.** When sources lacked a number, the agent reported a partial answer or said it could not determine it.
- **On vs off is within noise.** One run per question per setting, and the same question changed outcome between runs (Visa vs Mastercard was correct once, partial once, no answer once). Compaction saved cost on the longest runs and did nothing on short ones; typical runs last about 5 steps, so it rarely triggers.
- **"Off" disables only history compaction.** Extract-then-discard of raw pages stays on in both.
- Average cost is about $0.07 to $0.08 per run. The cost meter matched the Anthropic console within about 6% ($1.10 vs $1.04 for the compaction-on run).

The full per-question table and every failure I found are in [FAILURES.md](FAILURES.md). The five that mattered most:
1. mcp 2.x hid tool-error messages from the model, so the agent could not adapt (fixed).
2. A 20,000-character page cap cut off financial tables (raised to 60,000).
3. Half of the tool errors in one run were PDFs the server could not read (fixed with PDF extraction).
4. My first compaction discarded search results the model had not read yet, causing re-searches and a loop stop (fixed).
5. Comparisons sometimes returned only one company's figure, even though both pages were fetched (open).

## API

```
POST /research         header: X-API-Key    body: {"question": "..."}   ->  {"run_id": "..."}
GET  /research/{id}    header: X-API-Key                                ->  {"status": "queued|running|done|error", ...}
GET  /health
```

A finished run includes `result` with `report`, `stop_reason`, `steps_used`, `cost_usd`, `trace` and `notes`. A small web page at `/` calls these endpoints, and `/docs` has the interactive API docs.

## Run locally

Requires Python (version in `pyproject.toml`) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Create `.env`:

```
ANTHROPIC_API_KEY=...
TAVILY_API_KEY=...
API_KEY=<long random string>
FETCH_USER_AGENT=research-agent YourName your.email@example.com
SPEND_LIMIT_USD=3
COMPACT=off
```

Optional: `AGENT_MODEL` and `EXTRACT_MODEL` to change the models.

```bash
uv run uvicorn api.main:app --port 8000     # then open http://127.0.0.1:8000/
uv run main.py "your question here"         # one-off run in the terminal
```

On Windows, don't use `--reload`: it breaks subprocess handling.

`FETCH_USER_AGENT` matters. sec.gov returns 403 to automated clients that do not declare a contact.

## Run the evaluation

```bash
uv run eval.py on                  # or: off. Optional second argument: a questions file
uv run show.py results_on.jsonl    # prints answers, sources, steps, cost, errors
```

Questions are in `questions.txt` and the answer key is in `answer_key.md`. Results are appended to `results_on.jsonl` and `results_off.jsonl`, so delete old files before a fresh run.

## Deploy (Render)

The repo has a `Dockerfile`. On Render, create a Web Service, choose Docker as the language and add the environment variables from the `.env` example in the dashboard. Do not set `PORT`: Render sets it. Set the health check path to `/health`.

Free-tier behavior to know about:
- the service sleeps after 15 minutes without traffic and takes about a minute to wake,
- run history and the spend counter live in memory and reset on restart.

## Project layout

```
agent/        graph.py (LangGraph agent), mcp_client.py, schemas.py
server/       tools_server.py (MCP server: web_search, fetch_url)
api/          main.py (FastAPI), index.html (web page)
main.py       command-line runner
eval.py       runs questions.txt and writes results_*.jsonl
show.py       prints a results file
questions.txt, answer_key.md, results_on.jsonl, results_off.jsonl
FAILURES.md   failure analysis
Dockerfile, pyproject.toml, uv.lock
```

## Limitations and next steps

- Long documents: a 10-K as HTML returns little more than the cover page and table of contents. Section-aware fetching would fix this.
- Extraction can drop a headline number on long documents, which is the likely cause of the one-sided comparisons (unverified).
- The agent does not reliably flag ambiguous names (Block, Inc. vs H&R Block).
- Confidence labels are self-reported by the model and vary between runs. Computing them in code is a next step.
- One shared API key, a single run at a time and in-memory storage. A demo mode with per-visitor limits and a persistent run store would be needed for wider use.
- Check Anthropic's model deprecation page occasionally, since the models are set by environment variables.