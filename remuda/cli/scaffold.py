"""The commented job directory `remuda init` writes (FR-8).

Everything the scaffold cannot decide for the operator carries the
`REPLACE_ME` marker, and `remuda check` refuses a job that still holds one —
so a half-filled scaffold can never reach a model.
"""

from pathlib import Path

from remuda.errors import RemudaError
from remuda.spec.lint import PLACEHOLDER

INPUT_FILENAME = "input.csv"
VOCABULARY_FILENAME = "vocab/labels.txt"

#: Replaced with the job name at scaffold time (.format is unusable here —
#: the template contains Jinja braces of its own).
_NAME_TOKEN = "__JOB_NAME__"

_JOB_YAML = f"""\
# remuda job definition — validate with:  remuda check <this directory>
#
# Every {PLACEHOLDER} below is a decision only you can make. `remuda check`
# refuses this job until each one is filled in.

name: {_NAME_TOKEN}
description: What this job derives from each row.

input:
  # Path relative to this directory. CSV, JSONL or a JSON array.
  path: {INPUT_FILENAME}
  # Column holding each row's unique key — results are keyed by it.
  key: id

# How the run is executed. Raise records_per_call to pack several rows into
# one model call; raise workers to run more calls at once.
execution:
  records_per_call: 1
  attempts_per_model: 2
  workers: 4

fields:
  - name: label
    # classify | extract | generate | score | map
    kind: classify
    # A pool registered in ~/.config/remuda/pools.yaml or ./.remuda/pools.yaml
    pool: {PLACEHOLDER}_POOL
    # One label per line; the answer must be one of them.
    vocabulary_file: {VOCABULARY_FILENAME}
    prompt:
      # The ALLOWLIST: the template can read these input columns and no
      # others. A variable outside this list is refused by `check`.
      inputs: [text]
      template: |
        Classify the text below.

        Text: {{{{ text }}}}

        Answer with exactly one label and nothing else.

    # Optional: skip this field when the input is insufficient.
    # when:
    #   source: text
    #   min_length: 20

    # Optional: derive this field after another one (single level, no chains).
    # depends_on: some_other_field
"""

_VOCABULARY = f"""\
# One label per line. Lines starting with '#' are ignored.
{PLACEHOLDER}_LABEL_ONE
{PLACEHOLDER}_LABEL_TWO
"""

_INPUT_CSV = """\
id,text
1,The first example row. Swap this file for your own data.
2,The second example row.
"""


class ScaffoldError(RemudaError):
    """A job directory could not be scaffolded."""


def scaffold_job_dir(directory: Path, name: str | None = None) -> tuple[Path, ...]:
    """Write a commented, near-valid job directory at `directory`.

    Raises:
        ScaffoldError: the directory already holds a job definition.
    """
    target = Path(directory)
    job_name = name or target.resolve().name
    job_file = target / "job.yaml"
    if job_file.exists():
        raise ScaffoldError(
            f"{job_file} already exists — pick an empty directory, or edit "
            "the job that is already there"
        )

    (target / "vocab").mkdir(parents=True, exist_ok=True)
    written = [
        _write(job_file, _JOB_YAML.replace(_NAME_TOKEN, job_name)),
        _write(target / VOCABULARY_FILENAME, _VOCABULARY),
        _write(target / INPUT_FILENAME, _INPUT_CSV),
    ]
    return tuple(written)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path
