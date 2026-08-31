# remuda

Bulk LLM inference over pools of free/cheap models. Ride one until it tires, swap to the next.

[![Test](https://github.com/daviunx/remuda/actions/workflows/test.yml/badge.svg)](https://github.com/daviunx/remuda/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/remuda)](https://pypi.org/project/remuda/)
[![Python](https://img.shields.io/pypi/pyversions/remuda)](https://pypi.org/project/remuda/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A *remuda* is the herd of spare horses a working cowboy draws from: when one
tires, you saddle the next and keep moving. This tool does that with language
models. Point it at a pool of free or cheap models, hand it a thousand rows,
and it classifies, extracts, scores or generates a column for every one:
validating every answer, retrying with feedback, rotating past rate limits and
broken models, and never reporting a partial result as success.

## Why

Free-tier models are individually unreliable and collectively excellent. On
any given day a quarter of a free pool answers well, another quarter is rate
limited, and the rest return markdown essays when you asked for one word.
remuda's job is to make that mess dependable: a validation ladder per answer,
rotation across the pool, a crash-safe ledger, and resume that retries only
what failed, at no repeat cost.

## Install

```bash
pipx install remuda        # recommended for the CLI
pip install remuda         # or as a library
```

## Quick start

With `OPENROUTER_API_KEY` exported and no configuration at all, remuda
self-configures: an implicit `openrouter` provider and a `free` pool
discovered from its free tier. A local model server answering on
`127.0.0.1:11434` (Ollama, for instance) adds an implicit `local` provider.
Every implicit resolution is announced on stderr, an explicit registry entry
of the same name always wins, and `--no-bootstrap` (or `REMUDA_NO_BOOTSTRAP=1`)
turns it off.

```bash
# one question: the answer is all that reaches stdout
remuda run "Name the capital of Spain" -p free

# inline bulk: CSV in, the same CSV out with one new column
remuda run "Severity of this complaint: {{ complaint }}" \
    -i complaints.csv --field severity --vocab high,low -p free -o enriched.csv

# a repeatable job directory
remuda init jobs/my-job          # scaffold a commented job directory
remuda check jobs/my-job         # lint the spec, its input file and its pools
remuda preview jobs/my-job -n 3  # see the exact prompts, zero model calls
remuda run jobs/my-job           # derive every field for every row
remuda render .runs/my-job/<ts> --enrich -o out.csv   # input + new columns
```

All three shapes are one engine. A run resumes by default: re-invoke the same
command and only what is left is computed. Any key the pool could not answer
makes the run exit non-zero. A partial result is never reported as success.

## The ladder

An answer that fails validation is retried on the same model with feedback
describing what was wrong, then the chunk rotates to the next model in the
pool, then to the pool's mop-up model, and only then is the key recorded as
failed with its last reason. Within a packed call, valid answers are banked
immediately; only the keys still invalid re-enter the ladder. Rate limits and
transport failures cool a model down and redistribute its work without
consuming a validation attempt.

Each chunk prints a progress line to stderr as it lands, naming the model that
answered. Everything a run decides is appended to
`.runs/<job>/<timestamp>/ledger.jsonl` as it happens, so a killed run loses
nothing.

## What the models actually did

```bash
remuda runs list --runs-dir jobs/my-job/.runs
remuda stats    --runs-dir jobs/my-job/.runs   # success rate, latency, cost
remuda runs prune --keep 5 --runs-dir jobs/my-job/.runs   # dry; --write deletes
```

`stats` ranks models by how they have really performed, so YOU can reorder a
pool. remuda never reorders one itself: a tool that rewrote its own
configuration from yesterday's latency would be impossible to reason about.

## Embedding

```python
from remuda import Job, Registry, run_sync

report = run_sync(job, rows, registry, sink=collect, only=["severity"])
```

Rows are plain mappings from any source, results arrive per completed row, and
the `Report` is the same object the CLI persists. No files are required. The
registry is also constructible entirely in code; configuration files are one
loader over it, never the only way in.

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
precondition and may `depends_on` one previously derived field (one level, no
chains).

A prompt template can only read the columns listed in `prompt.inputs`. Any
other column on the row is unreachable from the template, and a template
variable outside that allowlist is refused at load time.

## Registry

Endpoints, credentials and model quirks are named once, never inside a job:

```
~/.config/remuda/       # user level
./.remuda/              # project level, overrides the user level by name
├── providers.yaml
├── models.yaml
└── pools.yaml
```

Credentials are never written in a file: a provider names the environment
variable its key is read from (`key_env`). Pools are ordered model references
with a `scatter` or `waterfall` strategy, an optional paid mop-up model, and
optional `discover:` queries resolved against a provider's live catalog when a
run starts.

### Discovering models

A pool entry can be a query against a provider's live catalog instead of a
model name:

```yaml
free-big:
  strategy: scatter
  entries:
    - discover: {provider: openrouter, free: true, min_context: 32000,
                 sort: context, take: 4}
```

Each provider declares which catalog shape it serves (`openrouter`,
`openai_compat`, `ollama`). A filter the catalog cannot answer (free-tier
filtering against a bare `/v1/models` list, for instance) is **refused**
naming the filter and the provider, never silently ignored.

Queries resolve when a run starts, and the resolved membership is snapshotted
into the run directory: a resumed run reuses exactly the models the first
attempt used, because free-tier membership churns week to week.

```bash
remuda pools show free-big      # materialized membership
remuda pools check free-big     # one minimal request per member
remuda models list openrouter --free
```

### Transports

| Provider kind | Runs where |
|---|---|
| `openai_compat` (OpenRouter, Ollama, vLLM, NIM) | anywhere |
| `opencode` (`opencode run -m <model>`) | the operator's machine only |

The opencode transport builds its command as an argument list and never
invokes a shell, so row data reaching the prompt cannot become a command. Its
version is probed once before a run that uses it.

## Documentation

The full CLI surface is documented in [docs/usage.md](docs/usage.md), and
recorded design exceptions in [docs/decisions.md](docs/decisions.md).

## Development

```bash
poetry install
poetry run pytest -q
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy .
```

## License

[MIT](LICENSE)
