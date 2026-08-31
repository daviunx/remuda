# Recorded decisions

Deliberate, reviewed departures from a Neomanex standard. Each row names the
standard, what remuda does instead, and why the standard's remedy would cost
more than it buys.

## Boolean names without an `is_` / `has_` / `can_` prefix

**Standard:** `documentation/standards/python/04-naming-conventions.md` →
*Boolean Variables* ("use `is_`, `has_`, `can_`, `should_` prefixes").

**Decision:** booleans that ARE a published name keep that name. Everything
else carries a prefix, and internal booleans were renamed to comply
(`is_counted`, `is_whole`, `is_needed`, `should_validate`, `should_bootstrap`).

| Name | Where it is published | Renaming would |
|------|----------------------|----------------|
| `fresh` | `--fresh` on `remuda run`, and the `fresh=` parameter of `api.run_job_dir` | Break the CLI flag and the library's public signature |
| `enrich` | `--enrich` on `remuda render` | Break the CLI flag |
| `fill_missing` | `--fill-missing` on `remuda run` and `remuda render` | Break the CLI flag |
| `free` | `discover: {free: true}` in `pools.yaml` | Break every registry file already written |
| `required` | `extract` property declarations in `job.yaml` | Break every job spec already written |
| `not_empty` | `generate` field declarations in `job.yaml` | Break every job spec already written |
| `single_line`, `integer`, `case_sensitive` | Field-kind declarations in `job.yaml` | Break every job spec already written |

**Why not rename anyway.** Typer derives a flag from the parameter name, and
the spec models parse YAML keys by field name, so each rename is a breaking
change to a surface an operator has already written down: a job directory, a
registry file, a shell script. `analysis.md` fixes these YAML keys as the job
specification's user-facing contract. The prefix rule exists so a reader can
tell a boolean from a noun; inside a `typer.Option("--fresh")` declaration or a
Pydantic field typed `bool`, that is never in doubt.

**Scope.** This exception covers only booleans whose name is the flag or the
YAML key. A new internal boolean takes the prefix.
