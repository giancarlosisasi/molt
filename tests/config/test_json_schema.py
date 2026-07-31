"""Conformance tests for molt's generated config JSON Schema.

Ported from ``roadmap/research/test-suite/03-config-changeset-io.md``, the
``packages/config/scripts/json-schema.test.ts`` section (2 rows), extracted from changesets
v3.0.0-next.9 (`json-schema.test.ts:4-12`).

changesets hand-runs ``@valibot/to-json-schema`` in a build script and commits the result
(`scripts/generate-json-schema.ts` -> `packages/config/schema.json`), purely so editors can
autocomplete the config file. molt gets the same artifact for free from pydantic v2's
``model_json_schema()`` (tech-stack section 6; research README section 6, "Reversing the msgspec
call"), so row 1 ports verbatim and only the schema *content* is molt-specific.

The syrupy baseline under ``__snapshots__/`` is intentionally absent: this module is
``importorskip``-guarded, so nothing runs (and nothing can be recorded) until ``molt.config``
lands. Generate it then with ``uv run pytest tests/config --snapshot-update``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.config", reason="build step 3 - config not yet implemented (TDD target)")

from molt.config import Config

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


# Canonical top-level option names. These are the keys the schema must offer for autocomplete;
# the camelCase migration aliases may appear alongside them, but snake_case is canonical
# (research doc 02, recommendation 4; website docs config/json-schema.md).
CANONICAL_OPTIONS: tuple[str, ...] = (
    "base_branch",
    "changed_file_patterns",
    "changelog",
    "commit",
    "ecosystem",
    "fixed",
    "forge",
    "format",
    "ignore",
    "linked",
    "private_packages",
    "snapshot",
    "update_internal_dependencies",
    "update_internal_dependents",
    "bump_workspace_sources_only",
)

# npm / peerDependencies concepts with no PyPI analogue (research README section 4.4). If any of
# these reaches the published schema, editors will happily autocomplete an option molt rejects.
DROPPED_OPTIONS: tuple[str, ...] = (
    "access",
    "peerDependencies",
    "onlyUpdatePeerDependentsWhenOutOfRange",
    "___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH",
)


@pytest.mark.unit
def test_can_generate_a_json_schema() -> None:
    """Row 1 (`json-schema.test.ts:4-6`): generation resolves without throwing.

    Upstream's version guards a hand-rolled valibot->JSON-Schema conversion; molt's guards that
    every field in the model stays schema-representable (a bare ``Callable`` annotation or an
    un-serializable default is enough to break it).
    """
    schema = Config.model_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("properties")


@pytest.mark.snapshot
def test_generates_the_expected_json_schema(snapshot: SnapshotAssertion) -> None:
    """Row 2 (`json-schema.test.ts:8-12`): the schema matches its recorded baseline.

    Upstream asserts against the committed ``../schema.json``; molt uses syrupy. The content is
    entirely molt's - snake_case keys, PyPI-shaped, no npm scope/dist-tag/peerDeps surface.
    """
    assert Config.model_json_schema() == snapshot


@pytest.mark.unit
@pytest.mark.parametrize("option", CANONICAL_OPTIONS)
def test_schema_exposes_the_canonical_snake_case_option(option: str) -> None:
    """Canonical names are the schema's property keys, not just accepted aliases.

    ``AliasChoices("base_branch", "baseBranch")`` puts the snake_case spelling first, which is
    what ``model_json_schema()`` publishes (research doc 02 line 760, tech-stack section 6).
    """
    properties: dict[str, Any] = Config.model_json_schema().get("properties", {})
    assert option in properties


@pytest.mark.unit
@pytest.mark.parametrize("option", DROPPED_OPTIONS)
def test_schema_does_not_offer_dropped_changesets_options(option: str) -> None:
    """Dropped options must not appear anywhere in the schema - including inside ``$defs``.

    Checked against the serialized schema rather than the top-level ``properties`` map because
    ``private_packages`` and ``snapshot`` are ``$ref``-ed sub-objects, and
    ``onlyUpdatePeerDependentsWhenOutOfRange`` would hide there. The ``properties`` guard keeps
    the negative from passing against an empty document.
    """
    schema = Config.model_json_schema()
    assert schema.get("properties"), "empty schema - this negative assertion must not be vacuous"
    assert option not in json.dumps(schema)


@pytest.mark.unit
def test_schema_offers_the_molt_native_backend_options() -> None:
    """``ecosystem`` and ``forge`` have no changesets equivalent (research README section 4.5/5).

    They are the seams for the two things upstream structurally cannot do - other Python
    packaging backends and non-GitHub forges - so they must be discoverable in the editor.
    """
    properties: dict[str, Any] = Config.model_json_schema().get("properties", {})
    assert "ecosystem" in properties
    assert "forge" in properties


@pytest.mark.unit
def test_schema_does_not_carry_the_private_packages_tag_subkey() -> None:
    """``privatePackages.tag`` controlled npm dist-tags; PyPI has none (research README 4.4).

    Asserted on the ``private_packages`` sub-schema specifically, since ``"tag"`` is too common a
    word to assert against the whole serialized document.

    Resolved through ``$ref`` rather than by scanning ``$defs`` for a private-looking key: if
    ``private_packages`` is modelled inline (a plain dict, a ``TypedDict``, a ``RootModel``)
    there is no ``$defs`` entry at all, a key scan yields nothing, and the negative passes over
    an empty haystack while ``"tag"`` sits in the published schema.

    ``anyOf``/``oneOf`` members are followed too, because the option is a union with the ``false``
    shorthand (`config.ts:95-105`) and pydantic emits the ``$ref`` *inside* the wrapper - so
    reading only the property's own ``$ref`` would land on the wrapper and be vacuous again. The
    ``"version"`` guard is what makes this test able to fail: it proves the resolved body really
    is the object ``tag`` would have lived in.
    """
    schema = Config.model_json_schema()
    defs: dict[str, Any] = schema.get("$defs", {})
    private: dict[str, Any] = schema["properties"]["private_packages"]
    bodies = [
        defs[node["$ref"].rsplit("/", 1)[-1]]
        for node in [private, *private.get("anyOf", []), *private.get("oneOf", [])]
        if isinstance(node, dict) and "$ref" in node
    ] or [private]
    haystack = json.dumps(bodies)
    assert "version" in haystack, (
        "private_packages sub-schema not resolved - this assertion must never be vacuous"
    )
    assert "tag" not in haystack
