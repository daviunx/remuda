# Changelog

All notable changes to remuda are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Nothing has been released yet: `0.1.0` in `pyproject.toml` is the in-development
version, and remuda is not published to PyPI or the GitLab package registry
until it has a second real consumer.

## [Unreleased]

### Fixed

- Catalog-backed pools (`discover:` entries — including the bootstrap's
  implicit `free` pool) now resolve in every run shape. One-shot and inline
  bulk runs previously refused them with "not resolved for this run"; `run()`
  materializes any discover pool it was not handed, so only the job-directory
  path keeps its resume snapshot semantics.

### Added

- **Job specification.** A job directory (`job.yaml` + an input file) declares
  fields of kind `classify`, `extract`, `generate`, `score` or `map`, with
  `when:` preconditions and one level of `depends_on`. Prompts are Jinja
  templates that can read only the columns the field lists in `prompt.inputs` —
  every other column on the row is structurally unreachable.
- **Registry.** Layered `providers.yaml` / `models.yaml` / `pools.yaml` (user
  level, then project level), also constructible entirely in code. Credentials
  are named (`key_env`), never written; an inline credential header is refused.
- **Zero-config bootstrap.** `OPENROUTER_API_KEY` implies an `openrouter`
  provider and a `free` pool; a local server on `127.0.0.1:11434` implies a
  `local` provider. Every implicit resolution is announced, explicit entries
  win, and `--no-bootstrap` / `REMUDA_NO_BOOTSTRAP` turns it off.
- **Catalog discovery.** A pool entry can be a `discover:` query against a
  provider's live catalog (`openrouter`, `openai_compat`, `ollama` shapes). A
  filter the catalog cannot answer is refused by name, never ignored. Resolved
  membership is snapshotted per run so a resume reuses the same models.
- **Transports.** OpenAI-compatible HTTP (httpx) and the `opencode` CLI as a
  subprocess — built as an argument list, never through a shell.
- **Engine.** Row packing into chunks, per-model lanes with concurrency, rpm,
  cooldown and spend ceilings, and the retry ladder: retry with repair
  feedback, rotate to the next model, then the pool's mop-up, then record the
  key as failed. Valid answers inside a packed call are banked immediately.
- **Ledger and resume.** Every decided `(row, field)` is appended to
  `ledger.jsonl` and fsync'd as it happens, alongside the spec lock, the job
  snapshot, the resolved pools and the report. A run resumes by default and
  refuses to resume across a changed job or input.
- **Rendering and reporting.** `render` re-emits a finished run as csv, jsonl
  or json, with passthrough columns, `--enrich` and `--fill-missing`, calling
  no model; `report` re-prints a run's report.
- **CLI.** `init`, `check`, `preview`, `run`, `render`, `report`, `stats`,
  `runs list|prune`, `pools show|check`, `models list`, `version`. `run` takes
  three shapes: a job directory, an inline bulk run over a file, or a one-shot
  request whose answer is all that reaches stdout. Diagnostics go to stderr;
  exit codes are 0 / 1 / 130.
- **Embedding API.** `run`, `run_sync`, `run_job_dir` and `one_shot` over plain
  mappings, with a per-row sink and a progress callback — no files required.
- **Cross-run statistics.** `stats` ranks models by measured success rate,
  latency and cost across past runs. remuda never reorders a pool itself.
- **CI.** `.gitlab-ci.yml` composed from `neomanex/ci-templates`: ruff, ruff
  format, mypy strict and the full pytest suite behind the coverage gate. No
  publish or release job yet, deliberately.
- Repository scaffolding: `CLAUDE.md`, this changelog, `docs/usage.md`,
  `docs/decisions.md` and `.env.example`.

### Changed

- The ledger append now runs on a worker thread (`asyncio.to_thread`) instead
  of blocking the event loop between chunks. `RunStore.append` takes a lock so
  concurrent writers still append whole, fsync'd lines.
