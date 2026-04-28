"""Tests for FeatureRequestService — verifies the three triage verdicts."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.services.feature_request_service import FeatureRequestService, TriageResult


def _make_completion_response(verdict: str, reason: str, questions=None, plan=None):
    """Build a fake CompletionResponse with the given triage JSON."""
    data = {
        "verdict": verdict,
        "reason": reason,
        "questions": questions or [],
        "implementation_plan": plan or {},
    }
    mock_resp = MagicMock()
    mock_resp.content = json.dumps(data)
    return mock_resp


# ---------------------------------------------------------------------------
# Triage: viable
# ---------------------------------------------------------------------------


async def test_triage_viable_dispatches_orchestrator():
    """When verdict is 'viable', orchestrator.implement_tool should be called."""
    plan = {
        "files_to_create": ["apps/backend/tools/calculator.py"],
        "files_to_modify": ["apps/backend/tools/registry.py"],
        "external_libs": [],
        "config_keys": [],
        "risks": [],
        "test_cases": [{"description": "adds two numbers", "input": "2+2", "expected": "4"}],
    }
    mock_resp = _make_completion_response("viable", "Straightforward calculator tool.", plan=plan)

    mock_gw = AsyncMock()
    mock_gw.complete.return_value = mock_resp

    mock_orchestrator = AsyncMock()
    mock_orchestrator.implement_tool.return_value = {
        "ok": True,
        "pr_url": "https://github.com/pr/1",
    }

    with (
        patch("backend.services.feature_request_service.LiteLLMGateway", return_value=mock_gw),
        patch("backend.services.feature_request_service.SessionLocal") as mock_sl,
        patch(
            "backend.services.improvement_orchestrator.ImprovementOrchestrator",
            return_value=mock_orchestrator,
        ),
        patch(
            "backend.services.feature_request_service.FeatureRequestService._log_triage",
            new_callable=AsyncMock,
        ),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_session

        svc = FeatureRequestService()
        svc.gw = mock_gw
        await svc.handle_request(
            request_id="req-1",
            title="calculator",
            summary="Basic arithmetic",
            use_cases=["add numbers", "subtract"],
            external_apis=[],
        )

    mock_gw.complete.assert_called_once()
    call_args = mock_gw.complete.call_args[0][0]
    assert any("viable" in m["content"] for m in call_args.messages if m["role"] == "system")
    mock_orchestrator.implement_tool.assert_awaited_once()


# ---------------------------------------------------------------------------
# Triage: clarify_needed
# ---------------------------------------------------------------------------


async def test_triage_clarify_needed_does_not_dispatch_orchestrator():
    """When verdict is 'clarify_needed', no orchestrator call should happen."""
    mock_resp = _make_completion_response(
        "clarify_needed",
        "Need more info about the required APIs.",
        questions=["Which SMS provider?", "Do you have Twilio credentials?"],
    )

    mock_gw = AsyncMock()
    mock_gw.complete.return_value = mock_resp

    mock_orchestrator = AsyncMock()

    with (
        patch("backend.services.feature_request_service.LiteLLMGateway", return_value=mock_gw),
        patch("backend.services.feature_request_service.SessionLocal") as mock_sl,
        patch(
            "backend.services.feature_request_service.FeatureRequestService._log_triage",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.services.improvement_orchestrator.ImprovementOrchestrator",
            return_value=mock_orchestrator,
        ),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_session

        svc = FeatureRequestService()
        svc.gw = mock_gw
        await svc.handle_request(
            request_id="req-2",
            title="send_sms",
            summary="Send SMS messages",
            use_cases=["send reminder"],
            external_apis=["Twilio"],
        )

    # Orchestrator should NOT have been invoked
    mock_orchestrator.implement_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Triage: not_viable
# ---------------------------------------------------------------------------


async def test_triage_not_viable_does_not_dispatch_orchestrator():
    """When verdict is 'not_viable', no orchestrator call should happen."""
    mock_resp = _make_completion_response(
        "not_viable",
        "Duplicates existing web_search functionality.",
    )

    mock_gw = AsyncMock()
    mock_gw.complete.return_value = mock_resp

    mock_orchestrator = AsyncMock()

    with (
        patch("backend.services.feature_request_service.LiteLLMGateway", return_value=mock_gw),
        patch("backend.services.feature_request_service.SessionLocal") as mock_sl,
        patch(
            "backend.services.feature_request_service.FeatureRequestService._log_triage",
            new_callable=AsyncMock,
        ),
        patch(
            "backend.services.improvement_orchestrator.ImprovementOrchestrator",
            return_value=mock_orchestrator,
        ),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_session

        svc = FeatureRequestService()
        svc.gw = mock_gw
        await svc.handle_request(
            request_id="req-3",
            title="search_the_web",
            summary="Search the internet",
            use_cases=["find news"],
            external_apis=[],
        )

    mock_orchestrator.implement_tool.assert_not_called()


# ---------------------------------------------------------------------------
# TriageResult validation
# ---------------------------------------------------------------------------


def test_triage_result_model_valid_verdicts():
    """TriageResult only accepts the three allowed verdict values."""
    for verdict in ("viable", "clarify_needed", "not_viable"):
        tr = TriageResult(verdict=verdict, reason="test")
        assert tr.verdict == verdict


def test_triage_result_model_rejects_unknown_verdict():
    """TriageResult should reject invalid verdict strings."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TriageResult(verdict="unknown", reason="bad")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_handle_request_logs_error_on_gateway_failure():
    """If the LLM gateway raises, the error should be logged without propagating."""
    mock_gw = AsyncMock()
    mock_gw.complete.side_effect = RuntimeError("gateway down")

    with (
        patch("backend.services.feature_request_service.LiteLLMGateway", return_value=mock_gw),
        patch("backend.services.feature_request_service.SessionLocal") as mock_sl,
        patch("backend.services.feature_request_service.Repository") as mock_repo_cls,
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_session

        mock_repo = AsyncMock()
        mock_repo_cls.return_value = mock_repo

        svc = FeatureRequestService()
        svc.gw = mock_gw

        # Should NOT raise — errors are caught and logged
        await svc.handle_request(
            request_id="req-err",
            title="bad_tool",
            summary="This will fail",
            use_cases=[],
            external_apis=[],
        )

    mock_repo.log_event.assert_called_once()
    logged_topic = mock_repo.log_event.call_args[0][0]
    assert logged_topic == "feature_request.error"
