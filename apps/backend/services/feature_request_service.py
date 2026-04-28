import json
import logging
from typing import Literal

from pydantic import BaseModel

from ..core.config import settings
from ..db.repository import Repository
from ..db.session import SessionLocal
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway

logger = logging.getLogger(__name__)


TRIAGE_PROMPT = """You are a senior engineer reviewing a feature request for a personal AI assistant called Mauricio.

The codebase is a Python FastAPI backend with:
- Tools in apps/backend/tools/ (each is a class with `spec: ToolSpec` and `async run(args, ctx)`)
- LLM access via LiteLLM
- Postgres + pgvector for memory & knowledge
- Existing tools: time_now, web_search, note_add/list/read, lamp (kasa smart bulb), memory_edit, memory_list, start_voice_chat, end_voice_chat, propose_new_tool

You receive a request from the user (also the admin/maintainer) for a new tool.

Decide one of three verdicts:
- "viable": the tool is feasible, low-risk, and you can describe how to implement it.
- "clarify_needed": the request is ambiguous; ask 1-3 specific questions.
- "not_viable": this would require risky changes (auth, secrets, network policies), is harmful, or duplicates existing functionality.

Output strict JSON:
{
  "verdict": "viable|clarify_needed|not_viable",
  "reason": "1-2 sentences",
  "questions": ["..."],
  "implementation_plan": {
    "files_to_create": ["apps/backend/tools/foo.py"],
    "files_to_modify": ["apps/backend/tools/registry.py"],
    "external_libs": ["requests"],
    "config_keys": ["FOO_API_KEY"],
    "risks": ["uses third-party API rate limits"],
    "test_cases": [
      {"description": "happy path", "input": "...", "expected": "..."}
    ]
  }
}

Notes:
- "questions" is only populated when verdict is "clarify_needed"; otherwise []
- "implementation_plan" is only populated when verdict is "viable"; otherwise {}
- Never suggest changes to db/models.py, infra/migrations/, or core security files
"""


class TriageResult(BaseModel):
    verdict: Literal["viable", "clarify_needed", "not_viable"]
    reason: str
    questions: list[str] = []
    implementation_plan: dict = {}


class FeatureRequestService:
    def __init__(self):
        self.gw = LiteLLMGateway()

    async def handle_request(
        self,
        *,
        request_id: str,
        title: str,
        summary: str,
        use_cases: list[str],
        external_apis: list[str],
    ) -> None:
        try:
            triage = await self._triage(title, summary, use_cases, external_apis)
            await self._log_triage(request_id, triage)

            if triage.verdict == "viable":
                logger.info(f"[feature_request] {title!r} is viable, dispatching to orchestrator")
                from .improvement_orchestrator import ImprovementOrchestrator

                orchestrator = ImprovementOrchestrator()
                result = await orchestrator.implement_tool(
                    request_id=request_id,
                    title=title,
                    summary=summary,
                    use_cases=use_cases,
                    plan=triage.implementation_plan,
                )
                logger.info(f"[feature_request] orchestrator result: {result}")
            elif triage.verdict == "clarify_needed":
                logger.info(
                    f"[feature_request] {title!r} needs clarification: {triage.questions}"
                )
                # Future: push back to user via chat/WhatsApp
            else:
                logger.info(
                    f"[feature_request] {title!r} not viable: {triage.reason}"
                )
        except Exception:
            logger.exception(f"[feature_request] error handling request {request_id}")
            async with SessionLocal() as s:
                await Repository(s).log_event(
                    "feature_request.error",
                    {"request_id": request_id, "stage": "triage"},
                )
                await s.commit()

    async def _triage(
        self,
        title: str,
        summary: str,
        use_cases: list[str],
        external_apis: list[str],
    ) -> TriageResult:
        use_cases_block = "\n".join(f"- {u}" for u in use_cases)
        apis_str = ", ".join(external_apis) if external_apis else "(none mentioned)"
        user_msg = (
            f"TITLE: {title}\n"
            f"SUMMARY: {summary}\n"
            f"USE CASES:\n{use_cases_block}\n"
            f"EXTERNAL APIS: {apis_str}"
        )
        req = CompletionRequest(
            messages=[
                {"role": "system", "content": TRIAGE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model_hint=settings.strong_model,
            temperature=0.0,
            max_tokens=1500,
            response_format={"type": "json_object"},
            metadata={"job": "feature_request_triage"},
        )
        resp = await self.gw.complete(req)
        data = json.loads(resp.content.strip())
        return TriageResult(**data)

    async def _log_triage(self, request_id: str, triage: TriageResult) -> None:
        async with SessionLocal() as s:
            await Repository(s).log_event(
                "feature_request.triaged",
                {
                    "request_id": request_id,
                    "verdict": triage.verdict,
                    "reason": triage.reason,
                    "questions": triage.questions,
                    "plan": triage.implementation_plan,
                },
            )
            await s.commit()
