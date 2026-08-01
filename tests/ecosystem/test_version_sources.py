"""Conformance rows for ``molt.ecosystem.version_sources`` -- where a package's version lives.

Net-new: there is **no changesets counterpart** to port. ``@manypkg/get-packages`` has no concept
of a version living anywhere but ``package.json``, and ``shouldSkipPackage``'s "no version" arm
(``should-skip-package/src/index.ts:3-22``) is the whole of upstream's answer. Everything asserted
here is guided by research doc 02 section 12.5 and research README sections 4.5 and 5 item 1, plus
the ``implement-version-sources`` design decisions each row cites.

**No row opens a socket, invokes a build backend, installs anything, or runs uv / hatchling /
setuptools.** That is not merely a policy for this file -- it is design D2's whole argument, and
``test_resolution_runs_no_subprocess_and_imports_no_project_module`` asserts it rather than
trusting it.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ProjectBuilder

pytest.importorskip("molt.ecosystem.version_sources")

from molt.ecosystem import (
    DEFAULT_VERSION_PATTERN,
    VERSION_SOURCE_GROUP,
    detect_version_source,
    discover_workspace,
    read_toml,
    resolve_version_source,
    resolve_workspace_versions,
)
from molt.errors import MoltError

# ======================================================================================
# Helpers
# ======================================================================================


def _package(project: ProjectBuilder, name: str) -> Any:
    """The discovered :class:`molt.ecosystem.Package` named ``name``."""
    workspace = discover_workspace(project.root)
    package = workspace.get(name)
    assert package is not None, f"{name} was not discovered"
    return package


def _detect(project: ProjectBuilder, name: str) -> Any:
    """What detection makes of ``name``'s manifest, with no explicit declaration consulted."""
    package = _package(project, name)
    return detect_version_source(package, read_toml(package.manifest_path))


def _resolve(project: ProjectBuilder, name: str) -> Any:
    """The source ``name``'s version comes from, through the full design D1 order."""
    return resolve_version_source(_package(project, name), root=project.root)


# ======================================================================================
# 1. The detection table (design D3)
# ======================================================================================


#: ``(tool tables, [build-system].requires, expected kind, expected path, why)``.
#:
#: Typed ``list[Any]`` rather than the tuple it is: the ``tool_tables`` column holds four different
#: nesting depths and pyrefly infers the literal's type as the union of all five row shapes, which
#: is not assignable to any one annotation. The five columns are named in the parametrize call and
#: in the signature below, which is where a reader looks anyway.
DETECTION_CASES: list[Any] = [
    (
        {"hatch": {"version": {"path": "src/pkg_a/__about__.py"}}},
        None,
        "file",
        "src/pkg_a/__about__.py",
        "hatch names the file holding the version",
    ),
    (
        {"hatch": {"version": {"source": "vcs"}}},
        None,
        "tag",
        None,
        "hatch-vcs computes the version from a git tag",
    ),
    (
        {"setuptools": {"dynamic": {"version": {"attr": "pkg_a.__version__"}}}},
        None,
        "file",
        "src/pkg_a/__init__.py",
        "setuptools' attr form resolves to a file by the import-path convention, never by import",
    ),
    (
        {"setuptools": {"dynamic": {"version": {"file": "VERSION"}}}},
        None,
        "file",
        "VERSION",
        "setuptools' file form names the file directly",
    ),
    (
        {"pdm": {"version": {"source": "file", "path": "src/pkg_a/__version__.py"}}},
        None,
        "file",
        "src/pkg_a/__version__.py",
        "pdm's file source carries its own path",
    ),
    (
        {"pdm": {"version": {"source": "scm"}}},
        None,
        "tag",
        None,
        "pdm's scm source is a git tag",
    ),
    (
        None,
        ["hatchling", "hatch-vcs"],
        "tag",
        None,
        "an scm plugin in [build-system].requires is the weakest signal, and still a tag",
    ),
    (
        {"hatch": {"build": {"targets": {"wheel": {"packages": ["src/pkg_a"]}}}}},
        None,
        None,
        None,
        "nothing detected: a hatch table that says nothing about the version",
    ),
]


@pytest.mark.functional
@pytest.mark.parametrize(("tables", "requires", "kind", "path", "why"), DETECTION_CASES)
def test_the_detection_table_reads_each_tool(
    tmp_project: ProjectBuilder,
    tables: dict[str, Any] | None,
    requires: list[str] | None,
    kind: str | None,
    path: str | None,
    why: str,
) -> None:
    """Design D3's table, one row per shape plus "nothing detected".

    Detection is a **heuristic over other tools' tables** and design D2 says so out loud: molt reads
    what hatch, setuptools and pdm already wrote down rather than asking the build backend, because
    asking needs an isolated environment, network access and arbitrary code execution for a value
    molt reads on every ``molt status``.
    """
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        tool_tables=tables,
        build_requires=requires,
    )
    # The attr row's convention target has to exist for the path lookup to find it.
    tmp_project.write_file("packages/pkg-a/src/pkg_a/__init__.py", '__version__ = "1.0.0"\n')

    detected = _detect(tmp_project, "pkg-a")

    if kind is None:
        assert detected is None, why
        return
    assert detected is not None, why
    assert detected.kind == kind, why
    assert detected.path == path, why


# ======================================================================================
# 2-5. The resolution order (design D1) and the three buckets (design D7)
# ======================================================================================


@pytest.mark.functional
def test_an_explicit_declaration_wins_over_detection(tmp_project: ProjectBuilder) -> None:
    """Design D1's order: the declaration the user wrote beats the table molt guessed from.

    Detection is a heuristic; an explicit declaration is the user telling molt the answer. There is
    no merging -- two sources for one package would give molt two versions and no rule for choosing
    between them.

    This is also where the declaration's **location** is pinned (design D5): it is read from the
    package's own manifest, not from the workspace root's ``[tool.molt]`` table.
    """
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        tool_tables={"hatch": {"version": {"path": "detected.py"}}},
        version_source={"kind": "file", "path": "declared.py"},
    )
    tmp_project.write_file("packages/pkg-a/detected.py", '__version__ = "9.9.9"\n')
    tmp_project.write_file("packages/pkg-a/declared.py", '__version__ = "1.2.3"\n')

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.kind == "file"
    assert source.describe() == "declared.py"
    assert source.read() == "1.2.3", "the declared file is the one read, not the detected one"


@pytest.mark.functional
def test_a_declared_version_resolves_to_the_static_source(tmp_project: ProjectBuilder) -> None:
    """``[project].version`` present and no ``dynamic`` -> the ``static`` source.

    ``plan_write`` returns **``None``** here, and that is the sentinel design D9 defines: the apply
    layer does its own ``[project].version`` edit, byte-identical to a run with no version sources
    at all. Making this source return a manifest write instead would move a pinned, byte-identical
    path onto new code for no gain.
    """
    tmp_project.add_package("pkg-a", version="1.0.0")

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.kind == "static"
    assert source.read() == "1.0.0"
    assert source.plan_write("2.0.0") is None, "the None sentinel is design D9's whole point"


@pytest.mark.functional
def test_an_unresolvable_dynamic_package_is_named_with_a_remedy(
    tmp_project: ProjectBuilder,
) -> None:
    """Design D7's third bucket: skipped, and **named**, with the reason and the fix.

    A package declaring ``dynamic = ["version"]`` is by definition a distribution somebody intends
    to build, so silence about it is the defect this whole seam exists to remove. The reason is a
    complete printable sentence, because it is what a user reads on the console.
    """
    tmp_project.add_package("pkg-a", version=None, dynamic_version=True)

    resolution = resolve_workspace_versions(discover_workspace(tmp_project.root))

    assert resolution.version_of("pkg-a") is None
    entries = [entry for entry in resolution.unresolved if entry.name == "pkg-a"]
    assert len(entries) == 1, "named once per run, not once per read"
    reason = entries[0].reason
    assert "pkg-a" in reason, "the message names the package"
    assert "[tool.molt.version_source]" in reason, "and the configuration that would fix it"


@pytest.mark.functional
def test_a_package_with_no_version_and_no_dynamic_is_in_neither_channel(
    tmp_project: ProjectBuilder,
) -> None:
    """Design D7's **middle** bucket -- an application, a docs site, a workspace-only root.

    Not a versioned distribution at all, so it is neither resolved nor named: it is absent from
    both channels and skipped **silently**. A warning per run about a package the user never
    intends to release is noise that trains people to ignore warnings, and this is the row that
    fails if ``dynamic = ["version"]`` and "no version at all" are ever collapsed back together.
    """
    tmp_project.add_package("pkg-a", version=None)

    resolution = resolve_workspace_versions(discover_workspace(tmp_project.root))

    assert resolution.version_of("pkg-a") is None, "not resolved"
    assert [entry.name for entry in resolution.unresolved] == [], "and not named either"


# ======================================================================================
# 6-11. The file source: reading, decoys, and the splice (design D4)
# ======================================================================================


@pytest.mark.functional
@pytest.mark.parametrize(
    ("literal", "why"),
    [
        ('__version__ = "1.2.3"\n', "dunder, double quoted"),
        ("__version__ = '1.2.3'\n", "dunder, single quoted"),
        ('version = "1.2.3"\n', "bare name, double quoted"),
        ("version = '1.2.3'\n", "bare name, single quoted"),
        ('VERSION = "1.2.3"\n', "shouting, double quoted"),
        ("VERSION = '1.2.3'\n", "shouting, single quoted"),
    ],
)
def test_the_default_pattern_reads_the_three_spellings(
    tmp_project: ProjectBuilder, literal: str, why: str
) -> None:
    """``__version__`` / ``version`` / ``VERSION``, single or double quoted (gap ``VS-5``).

    The set was **chosen, not decided**: ``__version_info__``, a tuple assignment and a version in
    ``setup.cfg`` all fall through to unresolved. Nothing outside molt's own tests pins it.
    """
    _file_sourced(tmp_project, literal)

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.read() == "1.2.3", why


@pytest.mark.functional
@pytest.mark.parametrize(
    ("decoy", "why"),
    [
        ('# Copyright 2019 Acme. version = "0.0.1"\n', "a version-like comment"),
        ("__version_tuple__ = (1, 2, 3)\n", "a tuple sibling with a longer name"),
        ('"""Docs for version 0.0.9 of the library."""\n', "a version mentioned in a docstring"),
    ],
)
def test_a_decoy_does_not_change_what_is_read(
    tmp_project: ProjectBuilder, decoy: str, why: str
) -> None:
    """Other version-like text beside the version itself must not be mistaken for it."""
    _file_sourced(tmp_project, f'{decoy}__version__ = "1.2.3"\n')

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.read() == "1.2.3", why


@pytest.mark.functional
def test_two_version_literals_in_one_file_is_an_error(tmp_project: ProjectBuilder) -> None:
    """Design D4: more than one match is an **error**, never a first-wins pick.

    A file with two version literals has no single answer, and picking one silently is how a
    package ships a version it never declared.
    """
    _file_sourced(tmp_project, '__version__ = "1.2.3"\nVERSION = "4.5.6"\n')

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    with pytest.raises(MoltError) as excinfo:
        source.read()
    assert "2 version literals" in str(excinfo.value)


@pytest.mark.functional
def test_a_whole_file_version_is_read_and_rewritten(tmp_project: ProjectBuilder) -> None:
    """Design D4's first fallback: setuptools' ``{file = "VERSION"}`` idiom.

    No pattern match, but the entire stripped content parses as PEP 440 -> the file **is** the
    version. The write replaces the whole content and preserves the trailing newline, because a
    file that gained or lost one would show up in every diff of every release.
    """
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        version_source={"kind": "file", "path": "VERSION"},
    )
    path = tmp_project.write_file("packages/pkg-a/VERSION", "1.2.3\n")

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.read() == "1.2.3"
    writes = source.plan_write("1.3.0")
    assert writes is not None
    assert [write.path for write in writes] == [path]
    assert writes[0].data == b"1.3.0\n", "the trailing newline survives"


@pytest.mark.functional
def test_an_explicit_pattern_overrides_the_default(tmp_project: ProjectBuilder) -> None:
    """A declared ``pattern`` wins, and only its ``version`` group is the version."""
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        version_source={
            "kind": "file",
            "path": "meta.py",
            "pattern": r"RELEASE\s*=\s*\"(?P<version>[^\"]+)\"",
        },
    )
    tmp_project.write_file("packages/pkg-a/meta.py", '__version__ = "9.9.9"\nRELEASE = "1.2.3"\n')

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    assert source.read() == "1.2.3", "the declared pattern decides, not the default trio"


@pytest.mark.functional
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_the_write_replaces_only_the_located_span(
    tmp_project: ProjectBuilder, newline: str
) -> None:
    """Design D4: a spliced span, never a re-render -- and the file's line endings survive.

    This is the concentrated risk of the whole change: the ``file`` source edits a source file molt
    did not create. Comments, imports, a second unrelated assignment carrying the *same* text, and
    the file's own newline convention all have to come back byte-for-byte. Both newline shapes are
    driven because a Windows-authored file rewritten as LF would show every line as changed.
    """
    lines = [
        "# Metadata for pkg-a. Do not reformat.",
        "import os",
        "",
        'BUILD_ID = "1.2.3"',
        '__version__ = "1.2.3"',
        'RELEASE_NOTES = "see CHANGELOG.md"',
        "",
    ]
    original = newline.join(lines)
    _file_sourced(tmp_project, original, pattern=r"__version__ = \"(?P<version>[^\"]+)\"")

    source = _resolve(tmp_project, "pkg-a")

    assert source is not None
    writes = source.plan_write("2.0.0")
    assert writes is not None
    updated = writes[0].data.decode("utf-8")
    expected = original.replace('__version__ = "1.2.3"', '__version__ = "2.0.0"')
    assert updated == expected
    assert 'BUILD_ID = "1.2.3"' in updated, "the decoy assignment is untouched"
    assert (newline.encode("utf-8") in writes[0].data) or newline == "\n"
    assert writes[0].data.count(b"\r\n") == original.count("\r\n")


# ======================================================================================
# 12-13. ``{attr = "..."}`` by path convention, and the escape refusal
# ======================================================================================


@pytest.mark.functional
@pytest.mark.parametrize(
    ("layout", "expected", "why"),
    [
        ("pkg_a/__init__.py", "pkg_a/__init__.py", "a flat package"),
        ("src/pkg_a/__init__.py", "src/pkg_a/__init__.py", "a src layout"),
        ("pkg_a.py", "pkg_a.py", "a single-module distribution"),
        ("lib/pkg_a/version.py", None, "a layout none of the three conventions finds"),
    ],
)
def test_an_attr_resolves_by_path_convention_and_never_by_import(
    tmp_project: ProjectBuilder, layout: str, expected: str | None, why: str
) -> None:
    """Design D3 / gap ``VS-6``: three conventions, in order, and no import ever happens.

    Importing a package to learn its version runs its top-level code, which is design D2's
    objection at a smaller scale. A layout the convention misses falls through to unresolved with
    the explicit ``path`` declaration as the remedy -- never to a guess.
    """
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        tool_tables={"setuptools": {"dynamic": {"version": {"attr": "pkg_a.__version__"}}}},
    )
    tmp_project.write_file(f"packages/pkg-a/{layout}", '__version__ = "1.2.3"\n')

    detected = _detect(tmp_project, "pkg-a")

    if expected is None:
        assert detected is None, why
        return
    assert detected is not None, why
    assert detected.path == expected, why


@pytest.mark.functional
def test_a_path_outside_the_package_is_refused(tmp_project: ProjectBuilder) -> None:
    """A version source must live inside the package it versions, and the refusal names both."""
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        version_source={"kind": "file", "path": "../../secrets.py"},
    )

    with pytest.raises(MoltError) as excinfo:
        _resolve(tmp_project, "pkg-a")

    message = str(excinfo.value)
    assert "pkg-a" in message and "../../secrets.py" in message


# ======================================================================================
# 14-15. The tag source: detected, and refused (design D6)
# ======================================================================================


@pytest.mark.functional
@pytest.mark.parametrize(
    ("tables", "requires", "why"),
    [
        ({"hatch": {"version": {"source": "vcs"}}}, None, "hatch-vcs, declared in its own table"),
        ({"pdm": {"version": {"source": "scm"}}}, None, "pdm's scm source"),
        (None, ["setuptools>=68", "setuptools-scm>=8"], "setuptools-scm as a build requirement"),
        (None, ["hatchling", "versioningit"], "versioningit as a build requirement"),
    ],
)
def test_a_tag_source_is_detected_for_each_scm_shape(
    tmp_project: ProjectBuilder,
    tables: dict[str, Any] | None,
    requires: list[str] | None,
    why: str,
) -> None:
    """Detected, so the refusal can name what molt saw rather than saying nothing at all."""
    tmp_project.add_package(
        "pkg-a", version=None, dynamic_version=True, tool_tables=tables, build_requires=requires
    )

    detected = _detect(tmp_project, "pkg-a")

    assert detected is not None, why
    assert detected.kind == "tag", why


@pytest.mark.functional
def test_reading_a_tag_source_refuses_and_names_the_remedy(tmp_project: ProjectBuilder) -> None:
    """Design D6: detected and **refused**, never silently skipped.

    The tag *is* the version, so there is nothing on disk to write, and every mechanism that would
    work needs its own change. The refusal is the honest interim answer and it is a strict
    improvement on silence: the user is told molt saw the package, identified its version source as
    a git tag, and cannot release it. The change that lifts it is named and committed --
    ``implement-tag-version-injection`` (owner ruling 2026-08-01).
    """
    tmp_project.add_package(
        "pkg-a", version=None, dynamic_version=True, build_requires=["hatchling", "hatch-vcs"]
    )

    source = _resolve(tmp_project, "pkg-a")
    assert source is not None
    assert source.kind == "tag"

    with pytest.raises(MoltError) as excinfo:
        source.read()
    message = str(excinfo.value)
    assert "pkg-a" in message, "the package"
    assert "git tag" in message, "what molt detected"
    assert "[tool.molt.version_source]" in message or "[project].version" in message, "a remedy"

    with pytest.raises(MoltError):
        source.plan_write("1.0.0")


# ======================================================================================
# 16-17. The resolution pass itself
# ======================================================================================


@pytest.mark.functional
def test_resolution_never_raises_and_reports_every_failure_in_one_pass(
    tmp_project: ProjectBuilder,
) -> None:
    """Two unresolvable members produce two entries, and nothing is raised.

    The same two-channel shape ``molt.config.load_config`` uses, and for the same reason: a caller
    that had to fix one problem per run to discover the next is the failure this shape removes.
    Warn and carry on is an owner ruling (2026-08-01): one unreleasable package must not block
    every other release.
    """
    tmp_project.add_package("pkg-a", version=None, dynamic_version=True)
    tmp_project.add_package(
        "pkg-b", version=None, dynamic_version=True, build_requires=["hatchling", "hatch-vcs"]
    )
    tmp_project.add_package("pkg-c", version="1.0.0")

    resolution = resolve_workspace_versions(discover_workspace(tmp_project.root))

    assert sorted(entry.name for entry in resolution.unresolved) == ["pkg-a", "pkg-b"]
    assert resolution.version_of("pkg-c") == "1.0.0", "the healthy member is unaffected"


@pytest.mark.functional
def test_resolution_runs_no_subprocess_and_imports_no_project_module(
    tmp_project: ProjectBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design D1 and D2 together: discovery reads manifests, resolution reads files. Nothing else.

    Three properties, and the first is what fails if resolution is ever folded back into
    ``discover_workspace``:

    1. **discovery opens only manifests** -- no version file is read while enumerating packages;
    2. resolution spawns **no subprocess** (no git, no build backend, no uv);
    3. resolution **imports no project module** -- ``{attr = ...}`` is resolved by path convention,
       because importing runs the project's top-level code.
    """
    tmp_project.add_package(
        "pkg-a",
        version=None,
        dynamic_version=True,
        tool_tables={
            "setuptools": {"dynamic": {"version": {"attr": "molt_fixture_pkg.__version__"}}}
        },
    )
    tmp_project.write_file(
        "packages/pkg-a/molt_fixture_pkg/__init__.py",
        "raise RuntimeError('importing a project module to read a version is design D2's "
        "objection')\n__version__ = '1.2.3'\n",
    )

    # Both readers are recorded, deliberately: `molt.ecosystem.read_toml` uses `Path.open("rb")`
    # while the file version source uses `Path.read_bytes`, so watching only one of them would
    # make this assertion vacuous rather than strict.
    opened: list[str] = []
    path_type = type(tmp_project.root)
    real_read_bytes = path_type.read_bytes
    real_open = path_type.open

    def recording_read_bytes(self: Path) -> bytes:
        opened.append(self.name)
        return real_read_bytes(self)

    def recording_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_bytes", recording_read_bytes)
    monkeypatch.setattr(path_type, "open", recording_open)

    workspace = discover_workspace(tmp_project.root)
    assert opened, "the recorder is wired up: discovery does read the manifests"
    assert set(opened) <= {"pyproject.toml"}, (
        "discovery is a pure manifest read (design D1) -- it must not open a version file"
    )

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("resolution must not spawn a subprocess (design D2)")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    resolution = resolve_workspace_versions(workspace)

    assert resolution.version_of("pkg-a") == "1.2.3"
    assert "molt_fixture_pkg" not in sys.modules, (
        "the version is located by path convention; importing would run the project's code"
    )


# ======================================================================================
# 18-19. The entry-point group (design D8)
# ======================================================================================


@pytest.mark.unit
def test_the_built_in_sources_are_registered_as_entry_points() -> None:
    """molt's own three sources resolve through the public mechanism, with no privileged path.

    Same shape ``tests/changelog/test_changelog.py`` uses for the changelog generators, and the
    same design D1 argument applied again: if the built-ins were special-cased by name in code, the
    plugin path would be the only untested one. **Requires ``uv sync``** -- entry points come from
    *installed* distribution metadata, not from ``pyproject.toml``.

    Gap ``VS-11``: this proves the mechanism works for molt's own sources; it does not prove the
    protocol is sufficient for a source molt did not write.
    """
    from importlib.metadata import entry_points

    registered = {point.name for point in entry_points(group=VERSION_SOURCE_GROUP)}

    assert {"static", "file", "tag"} <= registered, (
        f"run `uv sync`: {VERSION_SOURCE_GROUP} entry points come from installed metadata"
    )


@pytest.mark.unit
def test_an_unknown_source_names_the_requested_and_the_available_ones() -> None:
    """A source that is not installed fails with a message a user can act on."""
    from molt.ecosystem.version_sources.resolve import load_version_source_builder

    with pytest.raises(MoltError) as excinfo:
        load_version_source_builder("bespoke")

    message = str(excinfo.value)
    assert "bespoke" in message, "the source that was asked for"
    assert "file" in message and "static" in message, "and the ones that are installed"


@pytest.mark.unit
def test_the_group_prefix_is_stripped_like_every_other_molt_reference() -> None:
    """``molt.version_source.file`` reaches the entry point registered as ``file``.

    The sanctioned mechanism, owner ruling ``AC-7`` (2026-07-30): ``molt.commit.load_provider`` and
    ``molt.apply.generators`` already share it, so a built-in reaches its implementation by the
    same path a third-party source would.
    """
    from molt.ecosystem.version_sources.resolve import load_version_source_builder

    assert load_version_source_builder(f"{VERSION_SOURCE_GROUP}.file") is (
        load_version_source_builder("file")
    )


# ======================================================================================
# The default pattern is exported, because config validation and the source must agree
# ======================================================================================


@pytest.mark.unit
def test_the_default_pattern_captures_the_version_group() -> None:
    """The group name is the contract between the config validator and the splice (design D4).

    ``molt.config.rules.check_version_source`` refuses a declared pattern with no ``version`` group
    because that group **is** the span the write replaces. molt's own default has to satisfy the
    same rule, or the built-in path would be the one nothing checks.
    """
    import re

    assert "version" in re.compile(DEFAULT_VERSION_PATTERN).groupindex


# ======================================================================================
# Fixture helper
# ======================================================================================


def _file_sourced(project: ProjectBuilder, contents: str, *, pattern: str | None = None) -> Path:
    """A ``pkg-a`` whose version lives in ``about.py``, with ``contents`` in that file."""
    declaration: dict[str, Any] = {"kind": "file", "path": "about.py"}
    if pattern is not None:
        declaration["pattern"] = pattern
    project.add_package("pkg-a", version=None, dynamic_version=True, version_source=declaration)
    return project.write_file("packages/pkg-a/about.py", contents)


# ======================================================================================
# The two adapter code paths (design D10) must agree where they overlap
# ======================================================================================


@pytest.mark.functional
def test_the_two_adapter_paths_agree_for_a_statically_versioned_workspace(
    tmp_project: ProjectBuilder,
) -> None:
    """Design D10's named risk, driven both ways over one workspace.

    Every adapter grew a second code path -- ``resolution=None`` keeps the pre-seam behaviour and a
    supplied resolution takes the new one -- and two code paths can drift. For a workspace where
    every member is statically versioned the two must produce **identical** answers, because the
    ``static`` source resolves exactly what the manifest already said. This row is what fails when
    they stop agreeing.
    """
    from molt.config.models import default_config
    from molt.engine import skipped_package_names, to_engine_packages

    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "2.3.4", private=True)
    workspace = discover_workspace(tmp_project.root)
    config = default_config()
    resolution = resolve_workspace_versions(workspace)

    assert skipped_package_names(workspace, config) == skipped_package_names(
        workspace, config, resolution
    )
    assert to_engine_packages(workspace) == to_engine_packages(workspace, resolution=resolution)
