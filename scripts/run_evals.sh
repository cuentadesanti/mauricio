#!/usr/bin/env bash
# Run the eval suite inside the running backend container.
# Usage:
#   ./scripts/run_evals.sh                    # all suites
#   ./scripts/run_evals.sh memory_recall      # single suite

set -euo pipefail

SUITE="${1:-}"

echo "==> Running Mauricio evals (suite: ${SUITE:-all})"
docker compose exec backend python -m apps.backend.eval.runner $SUITE

echo "==> Done. Check eval-report.json for details."
