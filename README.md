# remuda

Bulk LLM inference over pools of free/cheap models. Ride one until it tires, swap to the next.

Private while v1 is built. Analysis: monorepo planning/neo-cli/neo-infer-cheap-bulk-llm/analysis.md (task 96674b95).

## Status

Jobs run. The specification, the layered registry, the retry/rotate/mop-up
ladder over OpenAI-compatible endpoints, the crash-safe ledger with resume,
and the `run` / `check` / `preview` / `render` / `report` / `init` commands are
implemented. Still to come: the opencode transport, catalog discovery,
zero-config bootstrap, one-shot and inline-bulk invocation, and cross-run stats.

## Quick start

```bash
remuda init jobs/my-job          # scaffold a commented job directory
remuda check jobs/my-job         # lint the spec, its input file and its pools
remuda preview jobs/my-job -n 3  # see the exact prompts, zero model calls
remuda run jobs/my-job           # derive every field for every row
remuda render .runs/my-job/<ts> --enrich -o out.csv   # input + new columns
```

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

## Development

```bash
poetry install
poetry run pytest -q
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy .
```
