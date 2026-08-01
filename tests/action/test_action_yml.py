"""Conformance tests for ``action.yml`` -- the composite action, read as the data file it is.

``action.yml`` is the one part of molt's release automation a user never runs locally, so the
instinct is to write it off as untestable. That gives up more than it has to: ``ruamel.yaml`` is
already a runtime dependency, the file is a document with a fixed schema, and the two ways it can
be wrong at the *shape* level -- an input the entry point does not accept, an output nobody
writes -- are exactly the two that fail silently on a runner (design D7).

So four things are asserted here, against the real shipped file:

1. every declared input maps onto an option the Typer command **actually** declares, resolved from
   the command's parameters rather than from a hand-copied list;
2. the declared outputs are exactly the four documented snake_case names, each mapped from the run
   step that writes it;
3. ``runs.using`` is ``composite``;
4. every step is either a ``uses:`` pinned to a 40-character commit SHA or a ``shell: bash``
   script -- a composite step with no ``shell`` is a hard runner error, and an unpinned third-party
   action contradicts the advice this action's own docs give.

What none of this catches
--------------------------
Whether the shell line quotes correctly on a runner, whether ``setup-uv`` resolves, whether ``uvx``
finds the pinned version, and whether the values survive the ``${{ steps.*.outputs.* }}`` round
trip. That residue is recorded as ``openspec/GAPS.md`` ``CO-12`` / ``CO-14`` and is covered by a
manual run against a scratch repository, not by a claim.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("molt.action.cli", reason="the entry point this file is compared against")

import typer
from ruamel.yaml import YAML

from molt.action.cli import ACTION_INPUTS, OUTPUT_NAMES, app

pytestmark = [pytest.mark.unit]

#: The repository root -- ``tests/action/`` is two levels below it, and the composite has to sit
#: exactly there for ``uses: giancarlosisasi/molt@v1`` to resolve (design D1).
ACTION_PATH = Path(__file__).resolve().parents[2] / "action.yml"

#: A git commit is 40 hexadecimal characters. A tag or a branch name is not, which is the whole
#: point: a moving ref can be repointed at different code after it has been reviewed.
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_action() -> dict[str, Any]:
    """The shipped ``action.yml``, parsed.

    YAML 1.2 through ``ruamel.yaml`` -- the same reader ``molt.changeset`` uses for changeset
    frontmatter, and for the same reason: YAML 1.1 would read an unquoted ``true`` default as a
    boolean, which is precisely the value this action passes through as a string.
    """
    return YAML(typ="safe").load(ACTION_PATH.read_text(encoding="utf-8"))


ACTION = load_action()
STEPS: list[dict[str, Any]] = list(ACTION["runs"]["steps"])


def declared_options() -> set[str]:
    """Every long option the entry point really declares, read off the Typer command."""
    return {option for parameter in typer.main.get_command(app).params for option in parameter.opts}


def run_step() -> dict[str, Any]:
    """The step that invokes ``molt-action``. The outputs are mapped from its id."""
    return next(step for step in STEPS if "molt-action" in str(step.get("run", "")))


def test_every_declared_input_maps_onto_an_option_the_entry_point_declares() -> None:
    """The action's inputs and the entry point's options are one surface, checked in both
    directions.

    ``ACTION_INPUTS`` is the mapping between them -- most inputs become an option, two do not:
    ``molt-version`` is consumed by the install step (it is the ``==`` pin, so the process it
    configures is a *different* molt), and ``github-token`` arrives as an environment variable
    because a token on a command line lands in the process table.

    The set equality is what makes an input impossible to add to the YAML without giving it a home,
    and the resolution against ``typer.main.get_command(app).params`` is what makes a renamed
    option a failing row rather than an input the composite passes and the entry point rejects.
    """
    declared = declared_options()
    mapped = {option for option in ACTION_INPUTS.values() if option is not None}

    assert set(ACTION["inputs"]) == set(ACTION_INPUTS), (
        "every input in action.yml needs a row in ACTION_INPUTS (and vice versa); an input with "
        "no home is an input the entry point silently ignores"
    )
    assert {f"--{option}" for option in mapped} <= declared
    script = str(run_step()["run"])
    assert [option for option in mapped if f"--{option}" not in script] == [], (
        "an input mapped to an option the run step never passes is dead configuration"
    )


def test_every_input_carries_a_description_and_a_default() -> None:
    """The input descriptions *are* the documentation a workflow author reads.

    Every input is optional and every one has a documented default, which is what makes "supply a
    token and nothing else" a working configuration (spec: "The documented defaults").
    """
    for name, spec in ACTION["inputs"].items():
        assert str(spec.get("description", "")).strip(), f"`{name}` has no description"
        assert "default" in spec, f"`{name}` has no default"
        assert spec.get("required", False) is False, f"`{name}` is not optional"


def test_the_declared_outputs_are_exactly_the_four_documented_names() -> None:
    """Four outputs, snake_case, each mapped from the step that writes it.

    snake_case is the deliberate divergence from upstream's camelCase ``action.yml``: it is what
    ``website/docs/guides/ci-github-action.md`` promises and what every other machine-readable
    payload molt emits uses. The mapping assertion is what stops an output from being declared and
    left permanently empty, which a workflow reads as "nothing was published".
    """
    outputs = ACTION["outputs"]
    step_id = run_step()["id"]

    assert tuple(outputs) == OUTPUT_NAMES
    for name, spec in outputs.items():
        assert f"steps.{step_id}.outputs.{name}" in str(spec["value"])
        assert str(spec.get("description", "")).strip(), f"`{name}` has no description"


def test_the_action_is_a_composite() -> None:
    """``runs.using: composite`` -- molt's action *is* the CLI it installs.

    A compiled JavaScript action (upstream's ``runs: node24`` plus a checked-in ``dist/index.js``)
    would be a second implementation of the release loop to keep in step with the first, and a
    bundle nobody reviews.
    """
    assert ACTION["runs"]["using"] == "composite"
    assert STEPS, "a composite action with no steps does nothing"


@pytest.mark.parametrize(
    "step", STEPS, ids=[str(step.get("name", index)) for index, step in enumerate(STEPS)]
)
def test_every_step_is_a_pinned_action_or_a_bash_script(step: dict[str, Any]) -> None:
    """A step either runs bash, or runs a third-party action pinned to an immutable commit.

    Both halves fail on a runner rather than here if they are wrong, and neither fails loudly. A
    composite ``run:`` step with no ``shell:`` is rejected outright by the runner; a ``uses:``
    pinned to a tag keeps working while silently becoming different code, which is the supply-chain
    risk this action's own documentation tells users to avoid -- so it holds itself to it.
    """
    uses = step.get("uses")
    if uses is None:
        assert step.get("shell") == "bash", "a composite run step must name its shell"
        assert str(step.get("run", "")).strip(), "a step with neither `uses` nor `run` does nothing"
        return
    _, _, ref = str(uses).partition("@")
    assert _COMMIT_SHA.match(ref), f"`{uses}` is not pinned to a 40-character commit SHA"
