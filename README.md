# remuda

Bulk LLM inference over pools of free/cheap models. Ride one until it tires, swap to the next.

Private while v1 is built. Analysis: monorepo planning/neo-cli/neo-infer-cheap-bulk-llm/analysis.md (task 96674b95).

## Status

Feature-complete for v1: jobs, inline bulk and one-shot requests run over
named or discovered pools, against HTTP endpoints or the opencode CLI, with
resume, rendering, reporting and cross-run statistics.

## Quick start

```bash
remuda init jobs/my-job          # scaffold a commented job directory
remuda check jobs/my-job         # lint the spec, its input file and its pools
remuda preview jobs/my-job -n 3  # see the exact prompts, zero model calls
remuda run jobs/my-job           # derive every field for every row
remuda render .runs/my-job/<ts> --enrich -o out.csv   # input + new columns
```

`run` takes three shapes, all one engine:

```bash
# a job directory
remuda run jobs/my-job

# inline bulk: CSV in, the same CSV out with one new column
remuda run "Severity of {{ title }}?" -i posts.csv --field severity \
    --vocab high,low -p cheap -o enriched.csv

# one question: the answer is all that reaches stdout
remuda run "Name the capital of Spain" -p cheap | tr a-z A-Z
```

With `OPENROUTER_API_KEY` exported and no configuration files at all, remuda
self-configures: an implicit `openrouter` provider and a `free` pool
discovered from its free tier. A local model server answering on
`127.0.0.1:11434` adds an implicit `local` provider. Every implicit resolution
is announced on stderr, an explicit registry entry of the same name always
wins, and `--no-bootstrap` (or `REMUDA_NO_BOOTSTRAP=1`) turns it off.

A run resumes by default: re-invoke the same command and only what is left is
computed. Any key the pool could not answer makes the run exit non-zero — a
partial result is never reported as success.

## Running a job

```
remuda run jobs/my-job [--limit N] [--field NAME] [--pool NAME]
                       [--fill-missing COLUMN] [--fresh]
```

Each chunk prints a progress line to standard error as it lands, naming the
model that answered and the running distribution. Everything a run decides is
appended to `.runs/<job>/<timestamp>/ledger.jsonl` as it happens, so a killed
run loses nothing.

### The ladder

An answer that fails validation is retried on the same model with feedback
describing what was wrong, then the chunk rotates to the next model in the
pool, then to the pool's mop-up model, and only then is the key recorded as
failed with its last reason. Within a packed call, valid answers are banked
immediately — only the keys still invalid re-enter the ladder. Rate limits and
transport failures cool a model down and redistribute its work without
consuming a validation attempt.

## What the models actually did

```bash
remuda runs list --runs-dir jobs/my-job/.runs
remuda stats    --runs-dir jobs/my-job/.runs   # success rate, latency, cost
remuda runs prune --keep 5 --runs-dir jobs/my-job/.runs   # dry; --write deletes
```

`stats` ranks models by how they have really performed, so YOU can reorder a
pool. remuda never reorders one itself — a tool that rewrote its own
configuration from yesterday's latency would be impossible to reason about.

## Embedding

```python
from remuda import Job, Registry, run_sync

report = run_sync(job, rows, registry, sink=collect, only=["severity"])
```

Rows are plain mappings from any source, results arrive per completed row, and
the `Report` is the same object the CLI persists. No files are required.

`remuda check` names every defect it finds in one pass and exits non-zero.
`remuda init` marks every decision it cannot make for you with `REPLACE_ME`,
and `check` refuses the job until each one is filled in.

## Job directory

```
my-job/
├── job.yaml            # name, input, fields
├── input.csv           # rows (CSV, JSONL or a JSON array)
└── vocab/labels.txt    # closed vocabulary for a `classify` field
```

Field kinds: `classify` (closed vocabulary), `extract` (declared shape),
`generate` (free text under constraints), `score` (number in a range), and
`map` (deterministic lookup, no model involved). A field may declare a `when:`
precondition and may `depends_on` one previously derived field — one level,
no chains.

A prompt template can only read the columns listed in `prompt.inputs`. Any
other column on the row is unreachable from the template, and a template
variable outside that allowlist is refused at load time.

## Registry

Endpoints, credentials and model quirks are named once, never inside a job:

```
~/.config/remuda/       # user level
./.remuda/              # project level — overrides the user level by name
├── providers.yaml
├── models.yaml
└── pools.yaml
```

Credentials are never written in a file: a provider names the environment
variable its key is read from (`key_env`). Pools are ordered model references
with a `scatter` or `waterfall` strategy, an optional paid mop-up model, and
optional `discover:` queries resolved against a provider's live catalog when a
run starts.

The registry is also constructible entirely in code — configuration files are
one loader over it, never the only way in.

### Discovering models

A pool entry can be a query against a provider's live catalog instead of a
model name:

```yaml
free-fast:
  strategy: scatter
  entries:
    - discover: {provider: openrouter, free: true, min_context: 32000,
                 sort: throughput, take: 4}
```

Each provider declares which catalog shape it serves (`openrouter`,
`openai_compat`, `ollama`). A filter the catalog cannot answer — free-tier
filtering against a bare `/v1/models` list, for instance — is **refused**
naming the filter and the provider, never silently ignored.

Queries resolve when a run starts, and the resolved membership is snapshotted
into the run directory: a resumed run reuses exactly the models the first
attempt used, because free-tier membership churns week to week.

```bash
remuda pools show free-fast      # materialized membership
remuda pools check free-fast     # one minimal request per member; always exits 0
remuda models list openrouter --free --limit 20
```

### Transports

| Provider kind | Runs where |
|---|---|
| `openai_compat` (OpenRouter, Ollama, vLLM, NIM) | anywhere |
| `opencode` — `opencode run -m <model>` | the operator's laptop only |

The opencode transport builds its command as an argument list and never
invokes a shell, so row data reaching the prompt cannot become a command. Its
version is probed once before a run that uses it.

## Development

```bash
poetry install
poetry run pytest -q
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy .
```
