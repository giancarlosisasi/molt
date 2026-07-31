"""Conformance tests for molt's git commit-message generation.

Ports ``packages/cli/src/commit/commit.test.ts`` in full (test-suite doc 08, commit group: 7
Port -- "message strings port 1:1; only the plugin-contract wrapper adapts"). Source:
``packages/cli/src/commit/index.ts:1-31`` (the ``getAddMessage``/``getVersionMessage`` template
strings) and ``packages/cli/src/commit/getCommitFunctions.ts`` (the dynamic-import plugin loader
that molt replaces with an entry point, see the last test below).

Pure string generation, no I/O: inputs are inline literals standing in for ``molt.changeset.
Changeset`` and ``molt.engine.Release``/``ReleasePlan`` (test-contract.md section 5) rather than
the real types from build steps 3/4, so this file collects and runs the moment ``molt.commit``
lands, independent of the changeset-IO and engine build steps. Upstream's ``getAddMessage`` only
ever reads ``changeset.summary`` (``commit/index.ts:6-10``) and ``getVersionMessage`` only ever
reads ``release.name``/``.type``/``.newVersion`` (``commit/index.ts:13-19``), so the local
dataclasses below carry exactly those fields plus enough of the documented shape (``.id``,
``.old_version``, ``.changesets``) to stay structurally interchangeable with the real types later.
No package.json/pyproject adaptation bites this file -- release objects already carry
``new_version`` as a plain string.

Every ``get_version_message`` assertion below is exact string equality, not a substring or regex
match: the 2-space release-line indent, the blank-line placement around ``[skip ci]``, and the
exact trailing-newline shape are all load-bearing per the group file, and only ``==`` catches a
1-space indent or a dropped trailing newline.

Before pinning these literals, the committed CLI suite was checked for a conflicting pin:
``tests/cli/test_version.py::test_the_commit_message_names_every_release`` explicitly declines to
assert the exact upstream wording ("molt owns the sentence"), asserting only that names,
versions, the release count, and the ``none``-exclusion are present. The upstream literal wording
ported here satisfies all of those looser assertions, so there is no actual conflict -- the CLI
suite simply defers the exact-wording decision to this file, which is the right place for it.
``tests/cli/test_add.py::test_commit_config_stages_and_commits_the_changeset`` similarly only
asserts the summary is present and cites ``docs(changeset): <summary>`` as upstream's template,
which this file also ports unmodified.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass

import pytest

from molt.versioning import BumpType

pytest.importorskip(
    "molt.commit", reason="build step 7 - molt.commit not yet implemented (TDD target)"
)

from molt.commit import get_add_message, get_version_message

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class Changeset:
    """Inline literal standing in for ``molt.changeset.Changeset`` (test-contract.md section 5).

    Only ``.summary`` is read by ``get_add_message`` -- upstream's ``getAddMessage`` never
    touches ``.releases``/``.id`` either (``commit/index.ts:6-10``) -- but the full shape is kept
    here so this literal stays structurally interchangeable with the real type once build step 3
    lands.
    """

    summary: str
    releases: tuple[tuple[str, BumpType], ...] = ()
    id: str | None = None


@dataclass(frozen=True)
class Release:
    """Inline literal standing in for one ``molt.engine.Release`` (test-contract.md section 5)."""

    name: str
    type: BumpType
    new_version: str
    old_version: str = "1.0.0"
    changesets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleasePlan:
    """Inline literal standing in for ``molt.engine.ReleasePlan``."""

    releases: tuple[Release, ...]
    changesets: tuple[Changeset, ...] = ()


# ======================================================================================
# getAddMessage -- commit.test.ts:56-94
# ======================================================================================


def test_get_add_message_simple_changeset_has_no_skip_ci_suffix() -> None:
    """commit.test.ts:59-73 -- ``skipCI: "version"`` does not trigger the ``add``-message skip.

    The whole message is ``docs(changeset): <summary>``; the skip-CI target has to equal
    ``"add"``/``True`` before anything is appended (``commit/index.ts:6-10``).
    """
    changeset = Changeset(summary="test changeset summary commit")

    message = get_add_message(changeset, skip_ci="version")

    assert message == "docs(changeset): test changeset summary commit"


def test_get_add_message_appends_skip_ci_block_when_target_is_add() -> None:
    """commit.test.ts:75-94 -- ``skipCI: "add"`` appends exactly ``\\n\\n[skip ci]\\n``.

    The trailing-newline shape is asserted with ``==``, not a substring check: a missing or
    extra newline here changes what git treats as commit body vs. trailer.
    """
    changeset = Changeset(summary="test changeset summary commit")

    message = get_add_message(changeset, skip_ci="add")

    assert message == "docs(changeset): test changeset summary commit\n\n[skip ci]\n"


# ======================================================================================
# getVersionMessage -- commit.test.ts:96-214
# ======================================================================================


def test_get_version_message_single_package_with_skip_ci() -> None:
    """commit.test.ts:96-109 -- one release, ``skipCI: "version"``.

    Exact-equality on the whole message pins the 2-space release indent and the blank-line
    placement around ``[skip ci]``.
    """
    plan = ReleasePlan(releases=(Release("package-a", BumpType.MINOR, "1.1.0"),))

    message = get_version_message(plan, skip_ci="version")

    assert message == (
        "RELEASING: Releasing 1 package(s)\n\nReleases:\n  package-a@1.1.0\n\n[skip ci]\n"
    )


def test_get_version_message_single_package_without_skip_ci() -> None:
    """commit.test.ts:111-122 -- same release plan, ``skipCI: False`` -- no ``[skip ci]`` block
    and no blank line beyond the one that already follows the release list.
    """
    plan = ReleasePlan(releases=(Release("package-a", BumpType.MINOR, "1.1.0"),))

    message = get_version_message(plan, skip_ci=False)

    assert message == ("RELEASING: Releasing 1 package(s)\n\nReleases:\n  package-a@1.1.0\n")


def test_get_version_message_multiple_releases_from_one_changeset() -> None:
    """commit.test.ts:124-158 -- two releases, each on its own ``  name@version`` line, in
    release order.
    """
    plan = ReleasePlan(
        releases=(
            Release("package-a", BumpType.PATCH, "1.0.1"),
            Release("package-b", BumpType.MINOR, "1.1.0"),
        )
    )

    message = get_version_message(plan, skip_ci="version")

    assert message == (
        "RELEASING: Releasing 2 package(s)\n"
        "\n"
        "Releases:\n"
        "  package-a@1.0.1\n"
        "  package-b@1.1.0\n"
        "\n"
        "[skip ci]\n"
    )


def test_get_version_message_merging_releases_from_multiple_changesets() -> None:
    """commit.test.ts:160-175 -- the message is built from the release plan's flattened
    ``releases``, so it makes no difference that the two releases came from different changesets.
    """
    plan = ReleasePlan(
        releases=(
            Release("package-a", BumpType.MINOR, "1.1.0"),
            Release("package-b", BumpType.MINOR, "1.1.0"),
        )
    )

    message = get_version_message(plan, skip_ci="version")

    assert message == (
        "RELEASING: Releasing 2 package(s)\n"
        "\n"
        "Releases:\n"
        "  package-a@1.1.0\n"
        "  package-b@1.1.0\n"
        "\n"
        "[skip ci]\n"
    )


def test_get_version_message_excludes_none_type_releases_from_message_and_count() -> None:
    """commit.test.ts:177-214 -- a ``type: "none"`` release is dropped from both the release
    list and the package count (``commit/index.ts:13-17``).

    Distinct from the engine's truthy-``none`` filter rule (research README section 3.3, which
    decides whether a *changeset* is considered at all): this filter runs after the engine has
    already produced the release plan, and only decides what the commit message *displays*.
    """
    plan = ReleasePlan(
        releases=(
            Release("pkg-a", BumpType.NONE, "1.0.0"),
            Release("pkg-b", BumpType.MINOR, "1.1.0"),
        )
    )

    message = get_version_message(plan, skip_ci="version")

    assert "RELEASING: Releasing 1 package(s)" in message
    assert "pkg-b@1.1.0" in message
    assert "pkg-a" not in message


# ======================================================================================
# The plugin-contract wrapper -- molt-native, no upstream row
# ======================================================================================


def test_default_commit_functions_are_registered_as_entry_points() -> None:
    """No upstream row: pins the plugin-registration shape for the owner.

    Upstream's ``CommitFunctions`` default export (``commit/index.ts:5,31``, loaded dynamically
    by ``getCommitFunctions.ts``) becomes a molt entry-point plugin, mirroring the changelog
    generator registration under ``[project.entry-points."molt.changelog"]``
    (test-contract.md section 5;
    ``tests/changelog/test_changelog.py::test_the_built_in_generators_are_registered_as_entry_points``
    is the analogous test this one is modeled on).

    Decision pinned here, flagged for owner sign-off: group ``"molt.commit"``, one entry named
    ``"default"`` pointing at the ``molt.commit`` module itself rather than a wrapper object --
    ``get_add_message``/``get_version_message`` module attributes already *are* the
    ``CommitFunctions`` surface::

        [project.entry-points."molt.commit"]
        default = "molt.commit"

    NOTE: like the changelog test this mirrors, ``importlib.metadata`` reads installed
    distribution metadata, not ``pyproject.toml`` directly -- adding the table above does not
    turn this green on its own until the editable install is rebuilt
    (``uv sync --reinstall-package molt-cli``).
    """
    entry_points = importlib.metadata.entry_points(group="molt.commit")
    names = {entry_point.name for entry_point in entry_points}

    assert "default" in names, (
        f"expected a 'default' molt.commit entry point, got {names}. "
        'NOTE: requires [project.entry-points."molt.commit"] default = "molt.commit" in '
        "pyproject.toml plus a rebuilt editable install."
    )
    loaded = entry_points["default"].load()
    assert callable(loaded.get_add_message)
    assert callable(loaded.get_version_message)


def test_load_provider_resolves_molt_commit_default_by_stripping_the_group_prefix() -> None:
    """AC-7 (owner ruling, 2026-07-30): the prefix-strip resolution mechanism is ratified.

    ``commit = true`` normalizes to the ref ``"molt.commit.default"``, which matches neither the
    entry-point name (``"default"``) nor an importable module by that name. ``load_provider``
    resolves it anyway by also trying the ref with the ``molt.commit.`` group prefix stripped --
    the same mechanism ``molt.apply.generators`` uses for ``"molt.changelog.default"``. Asserting
    module identity (rather than only "no exception was raised") pins the mechanism itself: a
    special-cased branch keyed on the literal ref would also avoid raising, but would not be this
    resolution path.
    """
    import molt.commit as commit_module
    from molt.commit import load_provider

    provider = load_provider("molt.commit.default", method="get_add_message")

    assert provider is commit_module
