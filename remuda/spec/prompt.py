"""Jinja rendering restricted to a field's declared inputs.

The allowlist firewall (FR-1/FR-2): a template is rendered with a context
built ONLY from `prompt.inputs`. Every other column on the row — however
sensitive, however large — is structurally unreachable from the template,
rather than merely unused by it.

This module deliberately knows nothing about the spec models, so the models
can import it for validation without an import cycle.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from jinja2 import Environment, StrictUndefined, meta
from jinja2.exceptions import TemplateError

from remuda.spec.errors import PromptRenderError

_ENVIRONMENT = Environment(
    undefined=StrictUndefined,
    autoescape=False,  # prompts are plain text for a model, never HTML
    keep_trailing_newline=True,
)


def template_variables(template: str) -> frozenset[str]:
    """Return every variable a template reads.

    Raises:
        PromptRenderError: the template is not valid Jinja.
    """
    try:
        parsed = _ENVIRONMENT.parse(template)
    except TemplateError as exc:  # TemplateSyntaxError and friends
        raise PromptRenderError(f"template is not valid Jinja: {exc}") from exc
    return frozenset(meta.find_undeclared_variables(parsed))


def render_prompt(
    template: str,
    inputs: Sequence[str],
    row: Mapping[str, Any],
) -> str:
    """Render `template` seeing only the values of `inputs` on `row`.

    Raises:
        PromptRenderError: the row lacks a declared input, or the template
            is not valid Jinja.
    """
    missing = [name for name in inputs if name not in row]
    if missing:
        raise PromptRenderError(
            "row is missing declared prompt input(s): " + ", ".join(sorted(missing))
        )
    context = {name: row[name] for name in inputs}
    try:
        return _ENVIRONMENT.from_string(template).render(context)
    except TemplateError as exc:  # UndefinedError, syntax, filter failures
        raise PromptRenderError(f"failed to render template: {exc}") from exc
