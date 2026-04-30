"""Scheduler sidecar — polls the `schedules` table every SCHEDULER_INTERVAL_S
seconds for due rows, dispatches them by `kind`, and marks status.

Designed to run as its own docker-compose service (so the FastAPI process
stays single-purpose / stateless). Failure of the sidecar must not impair
the API; failure of the API must not block scheduled work.

Dispatch is intentionally tiny right now: only `reminder`, which logs a
`reminder.fired` event the chat post-turn flow can pick up. As outbound
channels land (whatsapp_send, calendar_create…) they get added here as
new kinds. Each dispatcher must be idempotent — we use simple status
flips (pending → done|failed); we don't retry failed jobs automatically.
"""
import asyncio
import os
import sys
from datetime import UTC, datetime

from ..db.repository import Repository
from ..db.session import SessionLocal

INTERVAL_S = int(os.getenv("SCHEDULER_INTERVAL_S", "60"))


async def _dispatch_reminder(repo: Repository, sched) -> None:
    msg = (sched.payload or {}).get("message", "")
    await repo.log_event(
        "reminder.fired",
        {
            "schedule_id": sched.id,
            "user_id": sched.user_id,
            "message": msg,
            "fired_at": datetime.now(UTC).isoformat(),
        },
    )


DISPATCHERS = {
    "reminder": _dispatch_reminder,
}


async def _tick() -> tuple[int, int]:
    """One poll: returns (dispatched, failed)."""
    dispatched = 0
    failed = 0
    async with SessionLocal() as session:
        repo = Repository(session)
        due = await repo.list_due_schedules(now=datetime.now(UTC))
        for sched in due:
            handler = DISPATCHERS.get(sched.kind)
            if handler is None:
                await repo.mark_schedule_failed(sched.id, f"no dispatcher for {sched.kind}")
                failed += 1
                continue
            try:
                await handler(repo, sched)
                await repo.mark_schedule_done(sched.id)
                dispatched += 1
            except Exception as e:  # noqa: BLE001
                await repo.mark_schedule_failed(sched.id, str(e))
                failed += 1
        await session.commit()
    return dispatched, failed


async def run_forever() -> None:
    print(f"[scheduler] starting, interval={INTERVAL_S}s", flush=True)
    while True:
        try:
            dispatched, failed = await _tick()
            if dispatched or failed:
                print(
                    f"[scheduler] dispatched={dispatched} failed={failed}",
                    flush=True,
                )
        except Exception as e:  # noqa: BLE001
            # Never let the loop die — logging the error is enough; next
            # tick will retry whatever is still pending. Don't sleep less
            # than INTERVAL_S so DB outages don't turn into a hot loop.
            print(f"[scheduler] tick error: {e}", file=sys.stderr, flush=True)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run_forever())
