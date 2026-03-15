"""Stored evaluation harness for retrieval and board reliability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.query import query_brain


@dataclass(frozen=True)
class QueryEvalCase:
    name: str
    question: str
    expected_mode: str | None = None
    required_terms: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    require_brain_source: bool = True
    allow_web: bool = True


def default_query_eval_cases() -> list[QueryEvalCase]:
    return [
        QueryEvalCase(
            name="duSraBheja latest",
            question="What is the latest on the duSraBheja project??",
            expected_mode="latest",
            required_terms=["duSraBheja"],
            allow_web=False,
        ),
        QueryEvalCase(
            name="dataGenie status",
            question="What is the dataGenie project status??",
            expected_mode="latest",
            required_terms=["dataGenie"],
            allow_web=False,
        ),
        QueryEvalCase(
            name="droplet ip exact fact",
            question="what is my droplet account ip ??",
            expected_mode="answer",
            required_terms=["104.131.63.231"],
            allow_web=False,
        ),
    ]


def score_query_eval_case(result: dict, case: QueryEvalCase) -> tuple[float, str, str]:
    checks = 0
    passed = 0
    notes: list[str] = []
    answer_text = str(result.get("answer") or "")

    checks += 1
    if result.get("ok", True) and not result.get("failure_stage"):
        passed += 1
    else:
        notes.append(f"query failed at stage={result.get('failure_stage')}")

    if case.expected_mode:
        checks += 1
        if result.get("mode") == case.expected_mode:
            passed += 1
        else:
            notes.append(f"mode was {result.get('mode')} not {case.expected_mode}")

    if case.require_brain_source:
        checks += 1
        if result.get("brain_sources"):
            passed += 1
        else:
            notes.append("no brain sources returned")

    checks += 1
    if case.allow_web or not result.get("used_web"):
        passed += 1
    else:
        notes.append("web enrichment used when local-only answer was expected")

    for term in case.required_terms:
        checks += 1
        if term.lower() in answer_text.lower() or any(term.lower() in str(source).lower() for source in result.get("brain_sources") or []):
            passed += 1
        else:
            notes.append(f"missing required term: {term}")

    for term in case.forbidden_terms:
        checks += 1
        if term.lower() not in answer_text.lower():
            passed += 1
        else:
            notes.append(f"forbidden term present: {term}")

    score = round(passed / max(checks, 1), 3)
    status = "pass" if score >= 0.85 else "fail"
    return score, status, "; ".join(notes) or "ok"


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


async def run_query_eval(
    session: AsyncSession,
    *,
    run_name: str = "retrieval-reliability",
    rounds: int = 3,
    cases: list[QueryEvalCase] | None = None,
    now: datetime | None = None,
) -> dict:
    active_cases = cases or default_query_eval_cases()
    eval_run = await store.create_eval_run(
        session,
        run_name=run_name,
        status="running",
        metadata_={"rounds": rounds, "case_count": len(active_cases)},
    )

    aggregate_scores: list[float] = []
    total_cases = 0
    passed_cases = 0
    for round_index in range(rounds):
        for case in active_cases:
            total_cases += 1
            result = await query_brain(session, question=case.question, now=now)
            score, status, notes = score_query_eval_case(result, case)
            aggregate_scores.append(score)
            if status == "pass":
                passed_cases += 1
            await store.create_eval_case_result(
                session,
                eval_run_id=eval_run.id,
                case_name=f"round-{round_index + 1}: {case.name}",
                question=case.question,
                expected={
                    "expected_mode": case.expected_mode,
                    "required_terms": case.required_terms,
                    "forbidden_terms": case.forbidden_terms,
                },
                actual=_json_safe(result),
                status=status,
                score=score,
                notes=notes,
            )

    summary = {
        "rounds": rounds,
        "cases": total_cases,
        "passed": passed_cases,
        "average_score": round(sum(aggregate_scores) / max(len(aggregate_scores), 1), 3),
    }
    await store.update_eval_run(session, eval_run.id, status="completed", summary=summary)
    return {
        "eval_run_id": str(eval_run.id),
        "summary": summary,
    }
