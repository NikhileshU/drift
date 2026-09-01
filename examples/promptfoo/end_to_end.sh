#!/usr/bin/env bash
# J8c / spec review checkpoint 4: real promptfoo run -> adapter -> drift snapshot -> drift diff.
# Builds a throwaway git repo in a temp dir, evaluates twice (once failing, once fixed),
# and shows the failing case land in the Fixed bucket. No API key, no network beyond npx.
set -euo pipefail

work="$(mktemp -d)"
cp "$(dirname "$0")/promptfooconfig.yaml" "$work/"
cd "$work"
git init -q . && git config user.email eval@example.com && git config user.name Eval
echo "eval repo" > README.md && git add -A && git commit -qm "baseline eval set"
drift init >/dev/null

run() {  # promptfoo -> results.json -> snapshot, against the current commit
  npx -y promptfoo@latest eval -c promptfooconfig.yaml -o out.json --no-cache
  drift ingest promptfoo out.json -o results.json
  git add -A && git commit -qm "$1"
  drift snapshot --results-file results.json \
    --model-version echo --prompt-version "$2" --judge-version promptfoo-asserts@v1
  git rev-parse HEAD
}

before="$(run 'baseline results' support-agent@v1 | tail -1)"

# Fix the case that was failing its `contains: "I understand"` assertion.
sed -i'' -e 's/answer: "Calm down."/answer: "I understand this is frustrating, and I will sort it out."/' \
  promptfooconfig.yaml
after="$(run 'fix escalation tone' support-agent@v2 | tail -1)"

drift diff "$before" "$after"
echo "Workspace: $work"
