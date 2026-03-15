from __future__ import annotations

import asyncio

from src.services import evaluation as evaluation_service


def test_score_query_eval_case_passes_on_grounded_answer() -> None:
    case = evaluation_service.QueryEvalCase(
        name="droplet ip",
        question="what is my droplet account ip ??",
        expected_mode="answer",
        required_terms=["104.131.63.231"],
    )
    result = {
        "ok": True,
        "mode": "answer",
        "answer": "Direct answer: your droplet IP is 104.131.63.231.",
        "brain_sources": [{"title": "droplet note"}],
    }

    score, status, notes = evaluation_service.score_query_eval_case(result, case)

    assert score == 1.0
    assert status == "pass"
    assert notes == "ok"


def test_run_query_eval_executes_multiple_rounds(monkeypatch) -> None:
    calls = []

    async def fake_query_brain(session, *, question, now=None):
        calls.append(question)
        return {
            "ok": True,
            "mode": "answer",
            "answer": "104.131.63.231",
            "brain_sources": [{"title": "droplet note"}],
            "failure_stage": None,
        }

    class FakeStore:
        async def create_eval_run(self, session, *, run_name, status="running", summary=None, metadata_=None):
            class Run:
                id = "eval-run-1"
            return Run()

        async def create_eval_case_result(self, session, **kwargs):
            return kwargs

        async def update_eval_run(self, session, eval_run_id, **kwargs):
            return {"eval_run_id": eval_run_id, **kwargs}

    monkeypatch.setattr(evaluation_service, "query_brain", fake_query_brain)
    monkeypatch.setattr(evaluation_service, "store", FakeStore())

    result = asyncio.run(
        evaluation_service.run_query_eval(
            object(),
            rounds=3,
            cases=[
                evaluation_service.QueryEvalCase(
                    name="droplet ip",
                    question="what is my droplet account ip ??",
                    expected_mode="answer",
                    required_terms=["104.131.63.231"],
                )
            ],
        )
    )

    assert len(calls) == 3
    assert result["summary"]["cases"] == 3
    assert result["summary"]["passed"] == 3
