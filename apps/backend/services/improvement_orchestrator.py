import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from textwrap import dedent
from uuid import uuid4

from ..core.config import settings
from ..db.repository import Repository
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)


CLAUDE_CODE_PROMPT_TEMPLATE = """You are a senior Python engineer working on Mauricio, a personal AI assistant codebase.

Your task: implement a new tool based on the spec below.

## Repo conventions

- Tools live in `apps/backend/tools/`. Each tool is a class:
  ```python
  from .base import ToolSpec

  class FooTool:
      spec = ToolSpec(
          name="foo",
          description="What it does. Use when the user...",
          parameters={{"type": "object", "properties": {{...}}, "required": [...]}},
      )

      async def run(self, args: dict, ctx: dict) -> dict:
          # ctx has 'user_id', 'chat_id', maybe 'satellite_id', 'external_id'
          ...
          return {{"ok": True, "result": ...}}
  ```
- Register the tool in `apps/backend/tools/registry.py`:
  ```python
  from .foo import FooTool
  # in build_registry():
  tools["foo"] = FooTool()
  ```
- If the tool needs config (API keys, hosts), add them to `apps/backend/core/config.py` as Optional fields.
- If the tool needs a config-gated registration, follow the pattern of `lamp` / `web_search` (only register if config is set).
- Tests go in `tests/unit/test_tools.py`. Mock external calls.

## Constraints (HARD — do not violate)

- Do NOT modify migrations, db/models.py, or any file in infra/migrations/.
- Do NOT modify apps/backend/services/improvement_orchestrator.py or feature_request_service.py.
- Do NOT add new top-level dependencies unless absolutely needed; if you must, edit pyproject.toml minimally.
- Do NOT touch .env or any secret.
- Keep the change focused: just the tool, its registration, tests, and config additions.

## The feature

TITLE: {title}
SUMMARY: {summary}

USE CASES:
{use_cases_block}

EXTERNAL APIS: {external_apis}

## Implementation plan (from triage, may be incomplete; use your judgment)

{plan_block}

## Deliverables

1. Implement the tool file at apps/backend/tools/{title}.py
2. Register it in registry.py
3. Add config if needed in core/config.py
4. Add at least 2 unit tests in tests/unit/test_tools.py (one happy path, one error case)
5. Run `pytest tests/unit/ -k {title} --tb=short` and ensure it passes

When done, summarize what files you changed."""


class ImprovementOrchestrator:
    """Lanza Claude Code en un git worktree y abre un PR si todo va bien."""

    def __init__(self):
        self.repo_root = Path(settings.repo_root or "/app")
        self.worktrees_dir = Path("/tmp/mauricio-worktrees")
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_title(title: str) -> str:
        """Strip chars unsafe for branch names and filesystem paths."""
        import re
        return re.sub(r"[^a-zA-Z0-9_]", "_", title)[:40]

    async def implement_tool(
        self,
        *,
        request_id: str,
        title: str,
        summary: str,
        use_cases: list[str],
        plan: dict,
    ) -> dict:
        title = self._sanitize_title(title)
        branch = f"auto/feat-{title}-{uuid4().hex[:8]}"
        worktree_path = self.worktrees_dir / branch.replace("/", "-")

        await self._log(
            "orchestrator.start",
            {"request_id": request_id, "title": title, "branch": branch},
        )

        try:
            # 1. Create isolated worktree from main
            await self._git(
                "worktree", "add", "-b", branch, str(worktree_path), "main",
                cwd=self.repo_root,
            )

            # 2. Build Claude Code prompt
            prompt = self._build_prompt(title, summary, use_cases, plan)

            # 3. Run Claude Code headless
            success, output = await self._run_claude_code(worktree_path, prompt)
            if not success:
                await self._log(
                    "orchestrator.claude_code.failed",
                    {"request_id": request_id, "output": output[:1000]},
                )
                return {"ok": False, "stage": "claude_code", "output": output[:1000]}

            # 4. Run tests locally inside the worktree
            tests_ok, test_output = await self._run_tests(worktree_path)
            if not tests_ok:
                await self._log(
                    "orchestrator.tests.failed",
                    {"request_id": request_id, "output": test_output[:1000]},
                )
                return {"ok": False, "stage": "tests", "output": test_output[:1000]}

            # 5. Commit and push
            await self._git("add", "-A", cwd=worktree_path)
            await self._git(
                "commit",
                "-m",
                (
                    f"feat(tools): add {title}\n\n"
                    f"{summary}\n\n"
                    f"Implements feature request {request_id}.\n\n"
                    f"\U0001f916 Generated by Mauricio self-improvement loop"
                ),
                cwd=worktree_path,
            )
            await self._git("push", "-u", "origin", branch, cwd=worktree_path)

            # 6. Open PR via gh CLI
            pr_url = await self._open_pr(
                worktree_path, branch, title, summary, use_cases, request_id
            )

            await self._log(
                "orchestrator.pr_opened",
                {"request_id": request_id, "pr_url": pr_url},
            )
            return {"ok": True, "pr_url": pr_url, "branch": branch}

        except Exception as exc:
            logger.exception("orchestrator failed")
            await self._log(
                "orchestrator.error",
                {"request_id": request_id, "error": str(exc)},
            )
            return {"ok": False, "error": str(exc)}
        finally:
            # Clean up worktree (remote branch stays for PR review)
            try:
                await self._git(
                    "worktree", "remove", str(worktree_path), "--force",
                    cwd=self.repo_root,
                )
            except Exception:
                shutil.rmtree(worktree_path, ignore_errors=True)

    def _build_prompt(
        self, title: str, summary: str, use_cases: list[str], plan: dict
    ) -> str:
        return CLAUDE_CODE_PROMPT_TEMPLATE.format(
            title=title,
            summary=summary,
            use_cases_block="\n".join(f"- {u}" for u in use_cases),
            external_apis=", ".join(plan.get("external_libs", [])) or "(none)",
            plan_block=json.dumps(plan, indent=2) if plan else "(no plan provided)",
        )

    async def _run_claude_code(
        self, cwd: Path, prompt: str
    ) -> tuple[bool, str]:
        """Ejecuta `claude --print --dangerously-skip-permissions PROMPT` en headless."""
        env = {**os.environ}
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            prompt,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except TimeoutError:
            proc.kill()
            return False, "claude code timeout (10 min)"
        return proc.returncode == 0, stdout.decode("utf-8", errors="replace")

    async def _run_tests(self, cwd: Path) -> tuple[bool, str]:
        """Corre pytest en el worktree."""
        proc = await asyncio.create_subprocess_exec(
            "uv", "run", "pytest", "tests/unit/", "-x", "--tb=short",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except TimeoutError:
            proc.kill()
            return False, "pytest timeout (5 min)"
        return proc.returncode == 0, stdout.decode("utf-8", errors="replace")

    async def _git(self, *args: str, cwd: Path) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {list(args)} failed: {stdout.decode()[:500]}"
            )
        return stdout.decode()

    async def _open_pr(
        self,
        cwd: Path,
        branch: str,
        title: str,
        summary: str,
        use_cases: list[str],
        request_id: str,
    ) -> str:
        use_cases_md = "\n".join(f"- {u}" for u in use_cases)
        body = dedent(f"""\
            ## What
            Adds a new tool `{title}`.

            ## Why
            {summary}

            ## Use cases
            {use_cases_md}

            ## Auto-generated context
            - Request ID: `{request_id}`
            - Generated by: Mauricio self-improvement loop
            - Tests: pytest passed locally before push
            - Branch: `{branch}`

            ## Reviewer checklist
            - [ ] Tool spec matches use cases
            - [ ] No secrets leaked
            - [ ] No new top-level deps unless justified
            - [ ] Tests are meaningful, not just placeholder
            - [ ] CI evals pass (auto-checked below)
        """)

        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "create",
            "--title", f"feat(tools): {title}",
            "--body", body,
            "--base", "main",
            "--head", branch,
            "--label", "auto-generated",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"gh pr create failed: {stdout.decode()[:500]}"
            )
        # gh outputs the PR URL as the last line
        return stdout.decode().strip().splitlines()[-1]

    async def _log(self, topic: str, payload: dict) -> None:
        async with SessionLocal() as s:
            await Repository(s).log_event(topic, payload)
            await s.commit()
