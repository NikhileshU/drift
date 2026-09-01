# promptfoo → Drift, end to end (J8c)

A real, offline promptfoo run and the Drift results.json the adapter produced from it.
The `echo` provider means no API key and no network: `promptfoo eval` here is a genuine
promptfoo run, just with a deterministic model stand-in.

| File | What it is |
|---|---|
| `promptfooconfig.yaml` | Two tests, two named metrics (`answer_correctness`, `verbosity`). One passes, one fails. |
| `out.json` | promptfoo 0.122.2's own output, unedited. |
| `results.json` | What `drift ingest promptfoo` made of it. |

## Reproduce it

```sh
npx -y promptfoo@latest eval -c promptfooconfig.yaml -o out.json --no-cache
drift ingest promptfoo out.json -o results.json
drift snapshot --results-file results.json \
  --model-version echo --prompt-version support-agent@v1 --judge-version promptfoo-asserts@v1
```

The field mapping, and why `case_id` is built the way it is, are in
[`docs/promptfoo-mapping.md`](../../docs/promptfoo-mapping.md).
