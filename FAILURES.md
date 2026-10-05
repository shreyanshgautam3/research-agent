# Failure analysis

What worked, what failed and what I did about it. Numbers come from the evaluation runs in `results_on.jsonl` and `results_off.jsonl` and from the debugging runs before them. Where a cause is a hypothesis and not a verified fact, it says so.

## Setup

- 15 financial questions: 8 lookups, 4 comparisons, 3 traps. Answers and primary-source URLs are in `answer_key.md`, checked on 3 Oct 2026.
- Scoring by hand: correct, partial (right company or one side right, but incomplete or the wrong metric), no answer, wrong figure. Traps are scored on behavior.
- Claude Sonnet 5.5 as the main model at default effort, Claude Haiku 4.5 for extraction. One run per question per configuration, so results are indicative, not statistical.
- Cost comes from the agent's own meter. For the compaction-on run it read $1.10 against $1.04 in the Anthropic console.

## Final outcomes per question

| # | Question | Compaction on | Compaction off | Notes |
|---|---|---|---|---|
| 1 | Apple revenue, latest fiscal year | Correct | Correct | 8-K is the primary source |
| 2 | Microsoft revenue, FY ended June 2025 | Correct | Correct | |
| 3 | NVIDIA revenue, FY ended Jan 2026 | Correct | Correct | |
| 4 | Tesla revenue, 2025 | Correct | Correct | |
| 5 | Amazon AWS segment revenue, 2025 | Correct | Correct | Segment, not company total |
| 6 | JPMorgan net income, 2025 | No answer | Correct | On-run report claimed "search failed" while the trace shows no tool errors |
| 7 | Walmart revenue, FY2026 | Correct | Correct | Total revenues, not net sales |
| 8 | Netflix revenue, 2025 | Correct | Correct | |
| 9 | Microsoft vs Alphabet revenue | Partial | Correct | On-run had Alphabet's figure only |
| 10 | Coca-Cola vs PepsiCo revenue | Partial | Correct | On-run had Coca-Cola's figure only |
| 11 | Ford vs GM net income | Correct | Partial | Off-run had Ford's figure only |
| 12 | Visa vs Mastercard revenue | Partial | No answer | On: Mastercard only. Off: neither figure extracted |
| 13 | False premise: Apple revenue "fell" | Pass | Pass | Said revenue rose about 6% |
| 14 | Apple fiscal 2027 revenue | Pass | Pass | Said no results exist, gave no figure |
| 15 | Block revenue, 2025 | Partial | Partial | $24.19B as a labelled sum of quarters. H&R Block never mentioned |

| | On | Off |
|---|---|---|
| Correct or pass | 10 | 12 |
| Partial | 4 | 2 |
| No answer | 1 | 1 |
| Wrong figure | 0 | 0 |
| Cost / steps | $1.10 / 70 | $1.20 / 70 |

## Failures found and fixed

| # | Symptom | Root cause | Fix | Evidence |
|---|---|---|---|---|
| 1 | Every failed fetch showed a 30-character error, so the agent never learned why | In mcp 2.x only `ToolError` messages reach the model. Other exceptions become "Error executing tool <name>" | The server raises `ToolError` with short reasons (HTTP status, redirect target, unsupported type) | Error lengths went from 30 to 55 to 149 characters, and the agent started recovering from redirects |
| 2 | Answered with fiscal 2024 ($391B) when fiscal 2025 ($416B) was the latest reported year | The model did not know today's date and assumed one | Today's date added to the system prompt. Sonnet used as the main model | In a 4-run pilot on this question, Haiku was wrong twice (stale year, then no sources) and Sonnet was correct twice |
| 3 | Final report step failed with HTTP 400 on Sonnet | Sonnet 5.5 rejects a forced `tool_choice` (only `auto` and `none` work) | `auto` plus a prompt instruction, and a retry when the tool is not called | Error gone after the change |
| 4 | Two of ten steps lost to redirects, and the model did not always retry the corrected URL | The model had to re-request the redirect target itself | The server follows up to 3 redirects and re-checks every hop against the SSRF guard | Pilot traces |
| 5 | sec.gov returned 403 for every fetch | The SEC requires automated clients to declare a contact in the User-Agent | `FETCH_USER_AGENT` environment variable | Both sec.gov pages fetched afterwards |
| 6 | Walmart, PepsiCo and Block had no answer although the right page was fetched | A 20,000-character cap cut off the financial tables (hypothesis, supported by the retest) | Cap raised to 60,000 | Walmart and Block recovered. Walmart's recovery may also be source variance |
| 7 | 7 of 14 tool errors in one run were "PDF not supported". JPMorgan and PepsiCo had no answer | The server only read HTML | PDF text extraction (first 15 pages) | JPMorgan answered correctly in 3 steps from its own PDF. PepsiCo was correct in the compaction-off run from its own PDF |
| 8 | One run took 8 steps and $0.071 and stopped with `loop_detected`, against 4 steps and $0.057 without compaction. The answer was the same | Compaction ran after the new results were added and discarded search results the model had not read. It re-searched and re-fetched until the duplicate guard stopped it | Compaction keeps the latest exchange, a list of unfetched URLs and an "already tried" list | Same question after the fix: 0 duplicate blocks |
| 9 | At medium reasoning effort, 2 of 4 pilot questions made zero tool calls and returned empty reports | Most likely the effort setting (not isolated) | Reverted to default effort | At default effort, 4 of 4 used tools. Small sample |

## Failures still open

| # | Symptom | Likely cause | Status |
|---|---|---|---|
| 1 | Comparisons return one company's figure although both pages were fetched (Q9, Q10, Q12 with compaction on, Q11 and Q12 with it off) | Hypothesis: the extraction step drops the headline number on long documents. I did not inspect the notes to confirm | Open. Next step: read the notes for these runs, then try an extraction prompt that must copy every headline total with its period |
| 2 | The same question changes outcome between runs (Visa vs Mastercard: correct, partial, no answer. JPMorgan: 2 steps once, 9 steps another time) | Run-to-run variance in which sources the model picks and when it stops | Open. Needs several runs per question |
| 3 | Block, Inc. vs H&R Block never flagged in any run | No reliable ambiguity check. A prompt line asking the model to state which company it meant added an unneeded caveat to Walmart and still did not make it name H&R Block | Open |
| 4 | Confidence labels differ between runs for the same fact (high, high, medium) and early runs gave "high" on a single secondary source | Confidence is self-reported by the model | Open. Computing it in code is a next step |
| 5 | The grounding check passes if the cited URL was fetched, even if the page does not support the claim | It checks URLs, not content | Open. I spot-checked the Apple runs and found the notes supported the claims. Not checked systematically |
| 6 | Duplicate URLs across findings, and process notes such as "the 10-K excerpt was truncated" appearing as findings | The prompt does not stop the model splitting one fact per source | Open, cosmetic |
| 7 | `stop_reason: completed` appears on runs that found no sources at all | The label only means the model stopped calling tools | Open. A separate "no sources" reason is a next step |
| 8 | A report said "my search failed" when the trace showed no tool errors | The final report step explains process problems it cannot observe | Open |
| 9 | A 10-K as HTML returns only the cover page and table of contents | Character cap and document structure | Known limit. Press releases (8-K exhibits) work well |
| 10 | Some sites block automated clients (403 from macrotrends and some news and IR pages), plus occasional timeouts and 503s | Site policy and network conditions | Not fixable here. The agent falls back to other sources or reports partial results |
| 11 | The extraction note sometimes gave the period end date as the release date | Haiku confused two dates | Open. Did not reach a final report |
| 12 | An HTTP 400 on the report step in one of about three early Sonnet runs ("tool_use ids were found without tool_result blocks") | Diagnosed: when a response contained several `submit_report` calls and the first failed validation, the retry message answered only one of them. The fix is a tool result for every call | Not triggered in the 30 evaluation runs |
| 13 | Deployment limits: run history and spend counter reset on restart, the counter ignores crashed runs, one shared API key, cold start of about a minute on the free tier | In-memory state, simple auth, free instance | Documented. A persistent store and per-user keys would be needed for wider use |
| 14 | Security gaps: a response is size-checked after it is downloaded, and DNS rebinding is not handled by the SSRF guard | Simple implementation | Open |

## What held up

- No wrong figures in 30 evaluation runs. When a number was missing, the agent said so.
- Traps: the false premise was rejected in both runs, no fiscal 2027 revenue was invented, Walmart was never reported at the $706.4B net-sales figure, and Block's gross profit was always kept separate from revenue.
- Guardrails: the duplicate-call guard blocked 4 repeated calls across 3 compaction-on runs, no run hit the step limit or the $0.40 cost cap in the final evaluation, and tool errors never crashed a run.
- Observability: the trace, the per-call error notes and the saved notes made most of the failures above diagnosable.

## Caveats on the method

- One run per question per setting, so differences of one or two questions are noise.
- The answer key was written and scored by hand. It covers one domain (company financials), and figures are valid as of 3 Oct 2026.
- The on-vs-off comparison covers history compaction only. Extract-then-discard of raw pages was on in both.
- The suggested causes marked as hypotheses (extraction dropping numbers, the effort setting) were not isolated in controlled tests.