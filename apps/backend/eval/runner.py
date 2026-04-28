"""
Eval runner. Carga casos YAML, los ejecuta contra el backend, registra resultados.
Diseñado para correr en CI o manualmente:
  docker compose exec backend python -m apps.backend.eval.runner
  docker compose exec backend python -m apps.backend.eval.runner memory_recall
"""
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import delete

from ..db.models import Chat, MemoryRow, Message
from ..db.session import SessionLocal
from ..domain.chat import ChatMode
from ..gateways.litellm_gateway import LiteLLMGateway
from ..services.chat_service import ChatService

CASES_DIR = Path(__file__).parent / "cases"


@dataclass
class CaseResult:
    suite: str
    case_id: str
    passed: bool
    reason: str
    output: str = ""
    tool_calls: list = field(default_factory=list)


async def reset_state() -> None:
    """Limpia BD entre casos para no contaminar memoria ni chats."""
    async with SessionLocal() as s:
        await s.execute(delete(MemoryRow))
        await s.execute(delete(Message))
        await s.execute(delete(Chat))
        await s.commit()


async def run_messages(
    messages: list[dict],
    *,
    system_prompt: str | None = None,
) -> tuple[str, list[str]]:
    """Llama al ChatService directamente. Devuelve (output_text, tool_calls_list)."""
    chat_svc = ChatService(gateway=LiteLLMGateway())

    # Inject optional system override at the front
    full_messages = messages
    if system_prompt:
        full_messages = [{"role": "system", "content": system_prompt}] + messages

    chunks: list[str] = []
    tool_calls_seen: list[str] = []

    async with SessionLocal() as session:
        async for sse in chat_svc.handle(
            session,
            user_handle="eval-user",
            channel="eval",
            mode=ChatMode.PERSISTENT,
            incoming_messages=full_messages,
        ):
            if not sse.startswith("data: ") or "[DONE]" in sse:
                continue
            try:
                payload = json.loads(sse[6:])
                delta = payload["choices"][0]["delta"]
                content = delta.get("content", "")
                if content:
                    chunks.append(content)
                # Capture tool calls if present
                for tc in delta.get("tool_calls", []) or []:
                    fn_name = (tc.get("function") or {}).get("name")
                    if fn_name and fn_name not in tool_calls_seen:
                        tool_calls_seen.append(fn_name)
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        await session.commit()

    return "".join(chunks).strip(), tool_calls_seen


async def wait_for_post_turn_jobs(seconds: float = 3.0) -> None:
    """Espera a que la extracción de memoria termine (corre en background)."""
    await asyncio.sleep(seconds)


def evaluate_expected(
    output: str,
    tool_calls: list[str],
    expected: dict,
) -> tuple[bool, str]:
    """
    Aplica las reglas de un bloque 'expected' al output y tool_calls.
    Devuelve (passed, reason).
    """
    out_lower = output.lower()

    for token in expected.get("must_mention", []):
        if token.lower() not in out_lower:
            return False, f"missing: {token!r}"

    for token in expected.get("must_not_mention", []):
        if token.lower() in out_lower:
            return False, f"unwanted: {token!r}"

    if "tool_called" in expected:
        expected_tool = expected["tool_called"]
        if expected_tool not in tool_calls:
            called = tool_calls or ["(none)"]
            return False, f"expected tool {expected_tool!r}, got {called}"

    # tool_args_contains: check is best-effort here (we don't capture full args via SSE)
    # A future version can integrate with Langfuse traces for this.

    return True, "ok"


async def run_case(suite: str, case: dict) -> CaseResult:
    case_id = case["id"]
    expected = case.get("expected", {})

    try:
        await reset_state()

        # Setup: run prior chats to plant memories
        for setup_chat in case.get("setup", []):
            await run_messages(setup_chat["messages"])
            await wait_for_post_turn_jobs()

        # Test: run the actual query and evaluate
        test_block = case["test"]
        output, tool_calls = await run_messages(test_block["messages"])

        passed, reason = evaluate_expected(output, tool_calls, expected)
        return CaseResult(
            suite=suite,
            case_id=case_id,
            passed=passed,
            reason=reason,
            output=output[:500],
            tool_calls=tool_calls,
        )

    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            suite=suite,
            case_id=case_id,
            passed=False,
            reason=f"exception: {exc}",
            output="",
        )


async def main(suite_filter: list[str] | None = None) -> int:
    results: list[CaseResult] = []

    yaml_files = sorted(CASES_DIR.glob("*.yaml"))
    if not yaml_files:
        print("No eval cases found in", CASES_DIR)
        return 1

    for yaml_file in yaml_files:
        suite_data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        suite_name = suite_data["suite"]
        if suite_filter and suite_name not in suite_filter:
            continue

        for case in suite_data.get("cases", []):
            print(f"  [{suite_name}] {case['id']}...", flush=True)
            result = await run_case(suite_name, case)
            results.append(result)
            mark = "✓" if result.passed else "✗"
            print(f"    {mark} {result.reason}", flush=True)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{total} passed")

    # Write JSON report for CI consumption
    report = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "results": [asdict(r) for r in results],
    }
    report_path = Path("eval-report.json")
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Report written to {report_path.resolve()}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    suites = sys.argv[1:] or None
    sys.exit(asyncio.run(main(suites)))
