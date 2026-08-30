# remuda

Bulk LLM inference over pools of free/cheap models. Ride one until it tires, swap to the next.

Private while v1 is built. Analysis: monorepo planning/neo-cli/neo-infer-cheap-bulk-llm/analysis.md (task 96674b95).

## Status

Phase 1 (zero-network core) is implemented: the job specification, the layered
registry, and the `check` / `preview` / `init` commands. Nothing in this phase
opens a network connection — running a job comes in phase 2.

## Quick start

```bash
remuda init jobs/my-job        # scaffold a commented job directory
remuda check jobs/my-job       # lint the spec, its input file and its pools
remuda preview jobs/my-job -n 3  # see the exact prompts, zero model calls
```

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
