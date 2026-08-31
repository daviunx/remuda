# Command reference

The README explains what remuda is and how a job is shaped. This page is the
reference for the CLI itself: every verb, what it writes, and where it writes
it. Run `remuda COMMAND --help` for the full flag list of any one command.

## The output contract

Every command splits its two streams, so all of them compose in a pipeline.

| Stream | Carries |
|--------|---------|
| stdout | The answer, and nothing else: a one-shot answer, rendered CSV/JSONL/JSON, a table that IS the requested data |
| stderr | Everything else: progress lines, registry announcements, the run report, refusals |

| Exit code | Meaning |
|-----------|---------|
| `0` | Everything asked for was produced |
| `1` | Refused, or the run left at least one key unanswered. A partial result is never reported as success |
| `130` | Interrupted. Re-run the same command to resume |

## Verbs

| Command | Does | Writes |
|---------|------|--------|
| `remuda init DIR` | Scaffold a commented job directory; every decision it cannot make is marked `REPLACE_ME` | the job directory |
| `remuda check DIR` | Lint the spec, the input file's real columns and every pool the job names. No model is called | nothing |
| `remuda preview DIR [-n N]` | Print the exact prompts the first N rows would send | nothing |
| `remuda run …` | The three run shapes below | a run directory |
| `remuda render RUN_DIR` | Turn a finished run into csv/jsonl/json. No model is called | stdout, or `-o FILE` |
| `remuda report RUN_DIR [--json]` | Re-print a finished run's report, or its raw `report.json` | nothing |
| `remuda pools show NAME` | A pool's materialized membership, discovery resolved | nothing |
| `remuda pools check NAME` | One minimal request per member: alive, latency, refusal. Always exits 0 (a probe reports, it does not judge) | nothing |
| `remuda models list PROVIDER` | What a provider's own catalog currently serves | nothing |
| `remuda stats` | Cross-run per-model success rate, latency and cost | nothing |
| `remuda runs list` | Every recorded run, newest first | nothing |
| `remuda runs prune --keep N` | Drop old run directories. Dry by default; `--write` deletes | deletes run dirs |
| `remuda version` | The installed version | nothing |

## The three shapes of `run`

The target decides the shape. An existing directory is a job; anything else IS
the prompt.

```bash
# 1. A job directory: resumes its previous run by default
remuda run jobs/rally-severity [--limit N] [--only FIELD] [--pool NAME]
                               [--fill-missing COLUMN] [--fresh]

# 2. Inline bulk: CSV in, the same CSV out with one new column
remuda run "Severity of {{ title }}?" -i posts.csv --field severity \
    --vocab high,low -p cheap -o enriched.csv

# 3. One-shot: the answer is all that reaches stdout
remuda run "Name the capital of Spain" -p cheap | tr a-z A-Z
```

Shapes 1 and 2 record a ledger and can be resumed; shape 3 writes nothing at
all. Shapes 2 and 3 require `--pool`, because there is no job file to name one.

| Flag | Applies to | Effect |
|------|-----------|--------|
| `--limit N` | 1, 2 | Only the first N rows |
| `--only FIELD` | 1 | Derive only these fields (repeatable). Dependencies must be included |
| `--field NAME` | 2 | The name of the column the run derives |
| `--vocab a,b,c` | 2, 3 | Closed vocabulary the answer must come from |
| `--fill-missing COLUMN` | 1, 2 | Only rows whose named column is empty |
| `--fresh` | 1, 2 | Start a new run instead of resuming |
| `--runs-dir DIR` | 1, 2 | Where run directories live |
| `--pool NAME` | all | Override the pool every field names |
| `--config-dir DIR` | all | Registry layer, repeatable. Replaces the defaults entirely |
| `--no-bootstrap` | all | Ignore providers the environment implies |

## Resume

A run resumes by default: re-invoke the same command and only what is left is
computed. The ledger treats `ok` and `skipped` as done and deliberately retries
`failed`, so a rerun re-attempts exactly the keys that never got an answer.

Resume refuses (rather than mixing results) when the job definition or the
input file has changed since the run started. `--fresh` starts a new run and
leaves the old one on disk.

## Where a run is recorded

```
.runs/<job>/<timestamp>/
├── spec.lock.json        # what the run was started against (resume checks this)
├── job.json              # the job snapshot, so render needs no job directory
├── pool.resolved.json    # the models discovery materialized, frozen for resume
├── ledger.jsonl          # one fsync'd line per decided (row, field)
└── report.json           # totals, distribution, per-model statistics
```

`render` and `report` read only this directory, so a finished run is
re-rendered in any shape without calling a model again.

## Configuration

```
~/.config/remuda/     # user level
./.remuda/            # project level, overrides the user level by name
├── providers.yaml    # endpoints, timeouts, key_env (a variable NAME, never a key)
├── models.yaml       # model ids, per-model request quirks, prices
└── pools.yaml        # ordered members or discover: queries, strategy, mop-up
```

With `OPENROUTER_API_KEY` exported and no files at all, remuda self-configures
an `openrouter` provider and a `free` pool; a local server on `127.0.0.1:11434`
adds a `local` provider. Every implicit resolution is announced on stderr, an
explicit entry of the same name always wins, and `--no-bootstrap` (or
`REMUDA_NO_BOOTSTRAP=1`) turns it off. See `.env.example` for the variable
names remuda reads.
