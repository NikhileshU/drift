#!/usr/bin/env bash
# J9c: pip install alone -> pytest -> snapshot, with zero edits to the user's test files.
# Then breaks one eval and shows drift diff catch it as Regressed.
#
# Usage: ./end_to_end.sh [workdir]     (needs `drift` and `pytest` on PATH)
set -euo pipefail

work="${1:-$(mktemp -d)}"
mkdir -p "$work"
cp -r "$(dirname "$0")/tests" "$work/"
cd "$work"
git init -q . && git config user.email eval@example.com && git config user.name Eval
echo "eval repo" > README.md && git add -A && git commit -qm "eval suite"

echo "== the user's test files mention Drift nowhere:"
! grep -rl "import getdrift\|drift_snapshot\|pytest_plugins" tests/ || exit 1

drift init >/dev/null
git add -A && git commit -qm "drift init"

echo "== pytest (no flags, no conftest, no import)"
pytest -q
before="$(git rev-parse HEAD)"

echo "== break one eval, commit, re-run"
sed -i'' -e 's/"Did you mean the 12-pack or the 24-pack?", "12-pack"/"Did you mean the small or large pack?", "12-pack"/' \
  tests/test_support_agent_evals.py
git commit -qam "agent stops naming pack sizes"
pytest -q || true   # the suite fails; the snapshot is written anyway, which is the point

drift diff "$before" "$(git rev-parse HEAD)"
echo "Workspace: $work"
