from typing import Literal
from pydantic import BaseModel, Field


class Finding(BaseModel):
    claim: str
    source_urls: list[str] = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]

class ResearchReport(BaseModel):        # model produces this
    question: str
    summary: str
    findings: list[Finding]             
    limitations: str                    # what model couldn't verify
    as_of: str = Field(description="The period the figures cover e.g. 'fiscal 2025 (ended Sept 27, 2025)'")                          # source date

class RunResult(BaseModel):
    report: ResearchReport | None
    stop_reason: Literal["completed", "max_steps", "budget", "loop_detected", "error", "no_sources"]
    steps_used: int
    cost_usd: float
    trace: list[dict]
    notes: list[str] = []


def normalise(url: str) -> str:
    return url.strip().rstrip("/").lower()

def check_grounding(report: ResearchReport, fetched: set[str]) -> None:
    for f in report.findings:
        for u in f.source_urls:
            if normalise(u) not in fetched:
                raise ValueError(f"cited URL was not fetched: {u}")