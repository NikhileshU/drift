#!/usr/bin/env bash
# J8c / spec review checkpoint 4: real promptfoo run -> adapter -> drift snapshot -> drift diff.
# Builds a throwaway git repo in a temp dir, evaluates twice (once failing, once fixed),
# and shows the failing case land in the Fixed bucket. No API key, no network beyond npx.
#
# Usage: ./end_to_end.sh [workdir]     (default: a fresh mktemp -d)
set -euo pipefail

work="${1:-$(mktemp -d)}"
mkdir -p "$work"
cp "$(dirname "$0")/promptfooconfig.yaml" "$work/"
cd "$work"
git init -q . && git config user.email eval@example.com && git config user.name Eval
echo "eval repo" > README.md && git add -A && git commit -qm "baseline eval set"
drift init >/dev/null

run() {  # promptfoo -> results.json -> snapshot, against the current commit
  npx -y promptfoo@latest eval -c promptfooconfig.yaml -o out.json --no-cache
  drift ingest promptfoo out.json -o results.json
  git add -A && git commit -qm "$1"
  # The three provenance fields are derived from promptfoo, not hand-typed — an
  # `unset` judge_version would make drift diff's comparability check meaningless.
  eval "$(python3 -c 'import json,shlex,sys
p = json.load(open("results.json"))["metadata"]["provenance"]
print(" ".join("--%s %s" % (k.replace("_","-"), shlex.quote(v)) for k, v in p.items()))' \
    | sed 's/^/drift snapshot --results-file results.json /')"
  git rev-parse HEAD
}

before="$(run 'baseline results' | tail -1)"

# Fix the case that was failing its `contains: "I understand"` assertion.
sed -i'' -e 's/answer: "Calm down."/answer: "I understand this is frustrating, and I will sort it out."/' \
  promptfooconfig.yaml
after="$(run 'fix escalation tone' | tail -1)"

drift diff "$before" "$after"
echo "Workspace: $work"
