# remuda

Bulk LLM inference over pools of free/cheap models — ride one until it tires, swap to the next. Library + CLI: job specs, retry/rotation ladder, crash-safe ledger with resume, catalog discovery, cross-run statistics.

**Stack:** Python 3.12–3.13, Pydantic v2, Typer + Rich, httpx, Jinja2. Standalone GitLab repo (`neomanex/remuda`), checked out as a monorepo submodule at `tools/remuda`.

## Folder Map

| Folder | What |
|--------|------|
| `remuda/spec/` | Job models, loader, lint, Jinja prompt firewall |
| `remuda/registry/` | Providers, models, pools; layered YAML config + environment bootstrap |
| `remuda/catalog/` | Per-provider model discovery adapters and pool resolution |
| `remuda/transport/` | `Transport` Protocol, OpenAI-compatible HTTP, opencode subprocess |
| `remuda/engine/` | Packing, per-model lanes, the retry ladder, the runner |
| `remuda/validate/` | Per-kind answer validation and repair feedback |
| `remuda/ledger/` | Run directories, crash-safe JSONL ledger, resume, renderer |
| `remuda/cli/` | Typer app; `commands/` one module per verb |
| `docs/` | `usage.md` (the CLI surface), `decisions.md` (recorded exceptions) |

Library entry points: `remuda/api.py` (`run`, `run_sync`, `run_job_dir`, `one_shot`), `remuda/inspection.py`, `remuda/stats.py`.

## Tests

| Folder | Coverage | Run |
|--------|----------|-----|
| `tests/unit/` | Spec, registry, packing, ladder, runner, ledger, validators | `poetry run pytest tests/unit/` |
| `tests/integration/` | Real loopback HTTP server, real subprocess, CLI end to end | `poetry run pytest tests/integration/` |
| `tests/` | Everything, with the coverage gate | `poetry run pytest -q --cov` |

Coverage floor is `fail_under = 80` in `pyproject.toml`. Gates before every commit: `ruff check .`, `ruff format --check .`, `mypy .`, the full suite.

## Deploy / Infra

Public open-source library (MIT). GitHub `daviunx/remuda` is the public home (PyPI package `remuda`); GitLab `neomanex/remuda` stays the monorepo submodule origin. Push BOTH on every change: `git push origin main && git push github main`.

| What | Path |
|------|------|
| CI (the only gate) | `.github/workflows/test.yml` — GitHub Actions. There is no GitLab CI: never add a `.gitlab-ci.yml` |
| PyPI release | `.github/workflows/publish.yml` — fires on a `v*.*.*` tag push to GitHub; PyPI OIDC trusted publishing (`id-token: write`, environment `pypi`), no token secret |
| Env vars | `.env.example` — provider key names only, never values |

## Gotchas

- `tests/unit/conftest.py` carries an autouse socket guard: a unit test that opens a connection FAILS. Anything touching a socket or a subprocess belongs in `tests/integration/`
- The engine appends the ledger via `asyncio.to_thread` — `RunStore.append` is blocking and thread-safe by design. Never call it directly on the event loop, and never drop its lock or its `fsync`
- A prompt template can only read the columns its field declares in `prompt.inputs`. Adding a `{{ column }}` without declaring it is refused at load time, not silently rendered empty
- Registry files carry `key_env` (the NAME of an environment variable), never a credential. An inline credential header is refused by the model validator
- A run resumes by default. A ledger holds `ok` and `skipped` results only as "done" — `failed` is deliberately retried on resume
- Boolean flags/fields that mirror a CLI flag or a spec YAML key keep their bare names (`--fresh`, `required`, `not_empty`) — see `docs/decisions.md` before "fixing" them to `is_*`
