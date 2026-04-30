"""Periodic eval runner. Runs the full suite, computes pass rate, logs an
`eval.run.completed` event to the DB for trending, and screams to stderr if
the rate drops below threshold.

Designed for `docker compose run` / cron / systemd timer. Runs to completion
and exits — no built-in loop. Wrap with a sleep loop or scheduler externally.

Usage (one-shot):
  docker compose exec backend python -m apps.backend.eval.cron
  docker compose exec backend python -m apps.backend.eval.cron memory_recall

Usage (periodic, in compose):
  command: sh -c "while true; do python -m apps.backend.eval.cron; sleep 3600; done"

Exit codes:
  0  pass rate >= EVAL_ALERT_THRESHOLD
  1  pass rate <  EVAL_ALERT_THRESHOLD (the actually-actionable failure)
  2  runner crashed (eval-report.json missing or unparseable)
"""
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ..db.repository import Repository
from ..db.session import SessionLocal
from .runner import main as run_evals

REPORT_PATH = Path("eval-report.json")
ALERT_THRESHOLD = float(os.getenv("EVAL_ALERT_THRESHOLD", "0.75"))


async def _log_run_event(report: dict, suites: list[str] | None) -> None:
    """Persist the run summary to the events table so we can trend pass rate
    over time. Never raises — observability must not break the cron."""
    try:
        async with SessionLocal() as s:
            await Repository(s).log_event(
                "eval.run.completed",
                {
                    "suites": suites or "all",
                    "total": report.get("total", 0),
                    "passed": report.get("passed", 0),
                    "failed": report.get("failed", 0),
                    "pass_rate": (
                        report["passed"] / report["total"]
                        if report.get("total") else 0.0
                    ),
                    "failures": [
                        {"id": r["case_id"], "reason": r["reason"]}
                        for r in report.get("results", [])
                        if not r.get("passed")
                    ],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )
            await s.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[eval-cron] failed to log event: {e}", file=sys.stderr)


async def cron(suites: list[str] | None = None) -> int:
    try:
        await run_evals(suites)
    except Exception as e:  # noqa: BLE001
        print(f"[eval-cron] runner crashed: {e}", file=sys.stderr)
        return 2

    if not REPORT_PATH.exists():
        print(f"[eval-cron] no report at {REPORT_PATH.resolve()}", file=sys.stderr)
        return 2

    try:
        report = json.loads(REPORT_PATH.read_text())
    except json.JSONDecodeError as e:
        print(f"[eval-cron] bad report JSON: {e}", file=sys.stderr)
        return 2

    total = report.get("total", 0)
    passed = report.get("passed", 0)
    pass_rate = passed / total if total else 0.0
    await _log_run_event(report, suites)

    line = f"[eval-cron] {passed}/{total} pass_rate={pass_rate:.0%}"
    if pass_rate < ALERT_THRESHOLD:
        # ANSI red so it stands out in journalctl / docker logs.
        print(f"\033[31m⚠️  EVAL DROP {line} (threshold {ALERT_THRESHOLD:.0%})\033[0m",
              file=sys.stderr)
        for r in report.get("results", []):
            if not r.get("passed"):
                print(f"   ✗ {r['case_id']}: {r['reason']}", file=sys.stderr)
        return 1

    print(line)
    return 0


if __name__ == "__main__":
    arg_suites = sys.argv[1:] or None
    sys.exit(asyncio.run(cron(arg_suites)))
