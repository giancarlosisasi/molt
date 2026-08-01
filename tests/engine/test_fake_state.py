"""Sanity checks for the engine test builder itself.

``tests/engine/fake_state.py`` is the fixture every engine test is written against, and it is the
one piece of this phase that must be correct **today** -- ``tests/engine/test_assemble.py`` and
``tests/engine/test_dependents_graph.py`` are both skipped by ``importorskip`` until
``molt.engine`` exists, so nothing else would catch a broken builder.

These assertions come from ``roadmap/research/test-suite/02-release-plan-engine.md`` "FakeFullState
API -> Python plan-builder" (default seed at lines 164-171, dir rule at lines 188-191); upstream
source ``assemble-release-plan/src/test-utils.ts:9-178``.

Phase note: the P2 brief listed three files for this writer. This fourth file is an explicit
amendment -- a module-level ``importorskip`` skips the *whole* module, so builder checks parked in
``test_assemble.py`` would have skipped along with it and proved nothing.
"""

from __future__ import annotations

import pytest
from tests.engine.fake_state import (
    DEFAULT_CHANGESET_ID,
    DEFAULT_CHANGESET_SUMMARY,
    DepEntry,
    FakeFullState,
    package_dir,
)

from molt.config import default_config
from molt.versioning import BumpType

pytestmark = pytest.mark.unit


def test_default_seed_matches_get_simple_setup() -> None:
    """``getSimpleSetup`` (``test-utils.ts:52-68``) with ``yarn`` respelled ``uv``.

    The seeded ``pkg-a`` patch is load-bearing: a large share of the ported rows assert release
    counts that only add up because ``FakeFullState()`` already carries one pending changeset
    (research doc 02, "Consequence" at line 169).
    """
    state = FakeFullState()

    assert state.packages.root_package.manifest.name == "root"
    assert state.packages.root_package.manifest.version == "0.0.0"
    assert state.packages.root_package.dir == "/"
    assert state.packages.root_dir == "/"
    assert state.packages.tool == {"type": "uv"}

    assert [pkg.manifest.name for pkg in state.packages.packages] == ["pkg-a"]
    assert state.packages.packages[0].manifest.version == "1.0.0"

    assert len(state.changesets) == 1
    changeset = state.changesets[0]
    assert changeset.id == DEFAULT_CHANGESET_ID
    assert changeset.summary == DEFAULT_CHANGESET_SUMMARY
    assert changeset.releases[0].name == "pkg-a"
    assert changeset.releases[0].type is BumpType.PATCH


def test_constructor_overrides_only_what_is_passed() -> None:
    """``new FakeFullState({changesets: []})`` is the documented clean-slate form.

    It clears the pending patch but keeps the seeded ``pkg-a`` package -- exactly what the
    reference row #29 and the whole dependent-bumping matrix rely on (``index.test.ts:745``,
    ``:1172``).
    """
    state = FakeFullState(changesets=[])

    assert state.changesets == []
    assert [pkg.manifest.name for pkg in state.packages.packages] == ["pkg-a"]


def test_default_seed_is_not_shared_between_instances() -> None:
    """Each builder gets its own tree; upstream rebuilds the seed per ``beforeEach``."""
    first = FakeFullState().add_package("pkg-b", "1.0.0")
    second = FakeFullState()

    assert [pkg.manifest.name for pkg in second.packages.packages] == ["pkg-a"]
    assert len(first.packages.packages) == 2


@pytest.mark.parametrize(
    ("name", "expected", "why"),
    [
        ("pkg-a", "/packages/pkg-a", "plain name passes through"),
        ("@ex/core", "/packages/ex-core", "scoped: strip the leading @, / becomes -"),
        ("@ex/ui/core", "/packages/ex-ui-core", "every / is replaced, not just the first"),
        ("a@b", "/packages/a@b", "only a *leading* @ is stripped"),
        ("@@ex/core", "/packages/@ex-core", "only ONE leading @ is stripped -- lstrip eats both"),
    ],
)
def test_package_dir_mangling(name: str, expected: str, why: str) -> None:
    """``getPackage`` dir rule (``test-utils.ts:21``); research doc 02 lines 188-191.

    Load-bearing for the ``workspace:<relpath>`` rows (#34, #50): the engine compares the
    normalized relpath written in the constraint against the package dir taken relative to
    ``root_dir`` (``determine-dependents.ts:207-217``).
    """
    assert package_dir(name) == expected, why


def test_add_package_applies_the_dir_rule() -> None:
    """The builder, not just the helper, must derive the dir (``test-utils.ts:10-23``)."""
    state = FakeFullState().add_package("@ex/core", "0.1.0")

    assert state.packages.packages[0].dir == "/packages/pkg-a", "the seeded member too"
    assert state.packages.packages[-1].dir == "/packages/ex-core"


def test_add_package_rejects_a_duplicate_name() -> None:
    """``test-utils.ts:158-166`` -- adding the same package twice throws."""
    state = FakeFullState()
    with pytest.raises(ValueError, match="pkg-a"):
        state.add_package("pkg-a", "2.0.0")


def test_add_changeset_rejects_a_duplicate_id() -> None:
    """``test-utils.ts:88-93`` -- ids are unique; the seeded id is already taken."""
    state = FakeFullState()
    with pytest.raises(ValueError, match=DEFAULT_CHANGESET_ID):
        state.add_changeset(releases=[("pkg-a", BumpType.MINOR)])


def test_update_package_rejects_a_missing_package() -> None:
    """``test-utils.ts:169-177`` -- updating an unknown package throws rather than no-oping."""
    state = FakeFullState()
    with pytest.raises(ValueError, match="pkg-z"):
        state.update_package("pkg-z", "2.0.0")


def test_dependency_writers_route_to_the_right_manifest_field() -> None:
    """The three fields stay separate; ``update_dependencies`` is the bulk form.

    The separation is not cosmetic: ``dev_dependencies`` (PEP 735 groups) yields a ``none``
    dependent bump while ``optional_dependencies`` (extras) yields a patch, exactly like a runtime
    dependency (``determine-dependents.ts:100-117``; research README section 4.5). Upstream's
    fourth kind, ``peer``, is deliberately absent (research README section 4.4).
    """
    state = FakeFullState().add_package("pkg-b", "1.0.0")
    state.update_dependency("pkg-b", "pkg-a", "==1.0.0")
    state.update_dev_dependency("pkg-b", "pkg-c", ">=1.0.0,<2.0.0")
    state.update_optional_dependency("pkg-b", "pkg-f", ">=1.0.0,<1.1.0")

    manifest = state.packages.packages[-1].manifest
    assert manifest.dependencies == {"pkg-a": "==1.0.0"}
    assert manifest.dev_dependencies == {"pkg-c": ">=1.0.0,<2.0.0"}
    assert manifest.optional_dependencies == {"pkg-f": ">=1.0.0,<1.1.0"}

    state.update_dependencies(
        "pkg-b",
        [
            DepEntry(name="pkg-d", version_range="==2.0.0"),
            DepEntry(name="pkg-e", version_range="==3.0.0", kind="dev"),
            DepEntry(name="pkg-g", version_range="==4.0.0", kind="optional"),
        ],
    )
    assert manifest.dependencies == {"pkg-a": "==1.0.0", "pkg-d": "==2.0.0"}
    assert manifest.dev_dependencies == {"pkg-c": ">=1.0.0,<2.0.0", "pkg-e": "==3.0.0"}
    assert manifest.optional_dependencies == {"pkg-f": ">=1.0.0,<1.1.0", "pkg-g": "==4.0.0"}


def test_default_config_is_the_ported_changesets_default() -> None:
    """``defaults.ts:7-18`` + the ``config.ts`` schema defaults, in molt's key names.

    Only ``base_branch`` / ``ignore`` / ``fixed`` / ``linked`` / ``update_internal_dependencies``
    come from the written defaults; the rest are filled in by ``normalizeWrittenConfig``
    (``config.ts:57-60, 95-110, 111-131, 148-156``).

    The values are read off the real :func:`molt.config.default_config`, which replaced the
    engine suite's own ``FakeConfig`` stand-in (owner ruling 2026-07-31, closing gap ``RPE-7``);
    the three snapshot / private-package assertions therefore use the configuration's nested
    spellings rather than the flat ones the stand-in carried.

    ``model_copy(update=...)`` is the documented way to derive a variant; the base value must stay
    unchanged so parallel tests cannot leak config into each other.
    """
    config = default_config()
    assert config.ignore == ()
    assert config.fixed == ()
    assert config.linked == ()
    assert config.bump_workspace_sources_only is False
    assert config.update_internal_dependents == "out-of-range"
    assert config.update_internal_dependencies == "patch"
    assert config.snapshot.prerelease_template is None
    assert config.snapshot.use_calculated_version is False
    assert config.private_packages.version is True

    derived = config.model_copy(update={"ignore": ("pkg-b",)})
    assert derived.ignore == ("pkg-b",)
    assert config.ignore == ()
