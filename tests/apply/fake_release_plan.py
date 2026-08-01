"""``FakeReleasePlan`` builder + tmp-workspace harness for the ``apply-release-plan`` suite.

Port of the JS test scaffolding that every ``apply`` conformance test is built on:

- ``FakeReleasePlan`` (``apply-release-plan/src/index.test.ts:33-89``) -- a base changeset
  ``quick-lions-devour`` releasing ``pkg-a`` minor ``1.0.0 -> 1.1.0``, plus a config carrying every
  default, accepting extra changesets / releases / config overrides.
- ``testdir`` (``@changesets/test-utils``, used at ``index.test.ts:121``)
  -> :func:`write_workspace`.
- ``testSetup`` (``index.test.ts:91-144``) -> :func:`run_apply`.
- ``@manypkg/get-packages`` (``index.test.ts:19,133``) -> :func:`discover_packages`, a deliberately
  small uv-workspace discovery double.
- ``test-utils/failing-functions.ts`` -> :func:`write_failing_generator`.

Mapped by ``roadmap/research/test-suite/04-apply-changelog.md`` section "Fixtures & tooling to
build"; side-effect ordering and the manifest-mutation rules these fixtures feed come from
``roadmap/research/changesets-04-apply-changelog-git-ci.md`` sections 1.2 and 2.

This module is **not** guarded by ``importorskip``. It is pure test infrastructure and imports no
``molt`` product module except the already-implemented :mod:`molt.versioning` (for
:class:`~molt.versioning.BumpType`), exactly like ``tests/engine/fake_state.py``. The one place it
touches the TDD target -- ``molt.apply.apply_release_plan`` -- is a **late import inside**
:func:`run_apply`, so this file keeps importing while that function does not exist. The
``molt.apply`` package itself already exists: it holds the ``edit_toml`` primitives, which land a
build step earlier.

Naming note
-----------
Upstream's helpers are ``testdir`` and ``testSetup``. Neither name survives here: pytest's default
``python_functions = "test"`` is a *prefix* match, so a helper named ``testdir`` or ``test_setup``
would be collected as a test the moment a test module imports it. They are :func:`write_workspace`
and :func:`run_apply`.

Byte fidelity
-------------
Everything is written with ``Path.write_bytes`` and read with ``Path.read_bytes``. ``write_text`` /
``read_text`` apply newline translation, so on Windows a fixture manifest would land as CRLF and
every byte-exact assertion in ``test_apply.py`` (rows 1-4) would silently be testing something else.
Same reasoning as ``ProjectBuilder.write_changeset`` in ``tests/conftest.py``.

Seams this module assumes (flagged for the owner, see the phase report)
----------------------------------------------------------------------
- ``molt.apply.apply_release_plan(plan, packages, config, *, cwd) -> list[Path]`` (test contract
  section 8). ``snapshot=`` and ``pre=`` are passed **only when set**, because the contract does not
  say where a snapshot/prerelease invocation is signalled to ``apply``; a plan already carries the
  computed versions, but the "snapshot pins exactly and drops range modifiers" rule
  (``version-package.ts:103-105``) needs the flag.
- ``ReleasePlan`` is duck-typed as ``.releases`` / ``.changesets``; ``Release`` as ``.name``,
  ``.type``, ``.old_version``, ``.new_version``, ``.changesets``. Insertion order is significant
  (``molt.engine`` output is insertion-ordered), so every sequence here stays a ``list``.
- ``Packages`` is duck-typed as ``.root_dir`` / ``.root_package`` / ``.packages``, mirroring
  ``@changesets/types`` ``Packages`` and ``tests/engine/fake_state.Packages``.
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from molt.versioning import BumpType

__all__ = [
    "BASE_CHANGESET_ID",
    "BASE_RELEASE_NAME",
    "BASE_SUMMARY",
    "FAILING_GENERATOR_FILENAME",
    "FAILING_GENERATOR_MESSAGE",
    "FakeApplyConfig",
    "FakeChangeset",
    "FakeChangesetRelease",
    "FakePlan",
    "FakeRelease",
    "FakeReleasePlan",
    "PackageInfo",
    "Packages",
    "changeset_body",
    "discover_packages",
    "find_changed",
    "git_status",
    "member_manifest",
    "read",
    "root_manifest",
    "run_apply",
    "write_changeset_files",
    "write_failing_generator",
    "write_workspace",
]

#: ``FakeReleasePlan``'s base changeset (``index.test.ts:43-47``). Distinct from the engine
#: fixture's ``strange-words-combine`` -- the two suites seed different reference builders.
BASE_CHANGESET_ID: Final[str] = "quick-lions-devour"
BASE_SUMMARY: Final[str] = "Hey, let's have fun with testing!"
BASE_RELEASE_NAME: Final[str] = "pkg-a"

#: ``test-utils/failing-functions.ts:8,11`` -- both entry points throw ``no chance``.
FAILING_GENERATOR_MESSAGE: Final[str] = "no chance"
FAILING_GENERATOR_FILENAME: Final[str] = "failing_changelog.py"


# ======================================================================================
# Release-plan value objects (the shapes molt.engine will produce)
# ======================================================================================


@dataclass(frozen=True)
class FakeChangesetRelease:
    """One ``{name, type}`` frontmatter entry (``@changesets/types`` ``Release``)."""

    name: str
    type: BumpType


@dataclass
class FakeChangeset:
    """A parsed changeset: id, summary body, and the packages it releases."""

    id: str
    summary: str
    releases: list[FakeChangesetRelease]


@dataclass
class FakeRelease:
    """One entry of ``ReleasePlan.releases`` (``ComprehensiveRelease``).

    Attribute names are the test contract's ``molt.engine.Release`` surface: ``name``, ``type``,
    ``old_version``, ``new_version``, ``changesets``.
    """

    name: str
    type: BumpType
    old_version: str
    new_version: str
    changesets: list[str] = field(default_factory=list)


@dataclass
class FakePlan:
    """``molt.engine.ReleasePlan`` stand-in: ``.releases`` + ``.changesets``, insertion-ordered."""

    changesets: list[FakeChangeset]
    releases: list[FakeRelease]


# ======================================================================================
# Config
# ======================================================================================


@dataclass(frozen=True)
class FakeApplyConfig:
    """The ported ``defaultConfig`` plus the three keys ``apply`` reads.

    It used to extend ``tests.engine.fake_state.FakeConfig``; that stand-in was retired when the
    engine suite moved onto the real :class:`molt.config.Config` (owner ruling 2026-07-31, closing
    gap ``RPE-7``), so the eleven inherited fields are restated here under the same names, types
    and defaults. This stays a **dataclass** on purpose: ``tests/apply`` drives
    ``apply_release_plan`` structurally through ``molt.apply.apply.ApplyConfigLike``, and a
    structural double is what proves the seam is structural.

    Reference shape (research doc 01 section 1 "Default config"). It is split across two upstream
    files, and only five of the eleven ported fields come from the written defaults:
    ``packages/config/src/defaults.ts:7-18`` (``baseBranch``, ``ignore``, ``fixed``, ``linked``,
    ``updateInternalDependencies``) plus the schema defaults applied by ``normalizeWrittenConfig``
    in ``packages/config/src/config.ts`` -- ``changedFilePatterns`` ``:57-60``,
    ``privatePackages`` ``:95-105``, ``bumpVersionsWithWorkspaceProtocolOnly`` ``:106-110``,
    ``snapshot.useCalculatedVersion`` / ``snapshot.prereleaseTemplate`` ``:111-131``, and
    ``___experimentalUnsafeOptions.updateInternalDependents`` ``:148-156``.

    The three added fields are the ones ``applyReleasePlan`` consumes and the engine does not
    (``index.ts:241``, ``:190-193``, and the CLI's ``commit`` handling at
    ``cli/src/commands/version/index.ts:127-143``):

    - ``changelog`` -- ``False`` disables changelog generation entirely; a bare string names a
      generator entry point; a ``(name, options)`` pair passes options through verbatim
      (``website/docs/extending/changelog-plugins.md``, "Choosing a generator"). Upstream's
      ``FakeReleasePlan`` also defaults it to ``false`` (``index.test.ts:56``).
    - ``commit`` -- same three shapes; ``apply`` never commits either way, the CLI does.
    - ``format`` -- **defaults to ``False``, not upstream's ``"auto"``**. molt emits correct
      Markdown on the first pass and does not shell out to a formatter (research README section 5
      item 13; ``website/docs/config/options.md`` "Changelog and commit"; harness progress log,
      Session 2 decision 1).

    ``private_packages_version`` is the flat spelling of the docs'
    ``private_packages = { version = ... }``.
    """

    ignore: tuple[str, ...] = ()
    fixed: tuple[tuple[str, ...], ...] = ()
    linked: tuple[tuple[str, ...], ...] = ()
    bump_workspace_sources_only: bool = False
    update_internal_dependents: Literal["out-of-range", "always"] = "out-of-range"
    update_internal_dependencies: Literal["patch", "minor"] = "patch"
    snapshot_use_calculated_version: bool = False
    snapshot_prerelease_template: str | None = None
    base_branch: str = "main"
    changed_file_patterns: tuple[str, ...] = ("**",)
    private_packages_version: bool = True

    changelog: bool | str | tuple[str, Mapping[str, Any] | None] = False
    commit: bool | str | tuple[str, Mapping[str, Any] | None] = False
    format: bool | str = False


# ======================================================================================
# FakeReleasePlan -- the workhorse builder
# ======================================================================================


class FakeReleasePlan:
    """Port of ``class FakeReleasePlan`` (``index.test.ts:33-89``).

    Seeds one changeset (``quick-lions-devour``: ``pkg-a`` minor) and the matching release
    (``pkg-a`` ``1.0.0 -> 1.1.0``), then appends whatever the test passes. **Insertion order is
    preserved** -- the base entries come first, exactly as upstream's
    ``[baseChangeset, ...changesets]`` spread does (``index.test.ts:78-79``) -- because the release
    order drives changelog section order and the order of ``changed_files``.

    ``include_base=False`` starts from an empty plan; upstream's equivalent is passing a literal
    ``ReleasePlan`` object instead of using the builder (e.g. ``index.test.ts:704-732``).
    Config overrides are keyword arguments naming :class:`FakeApplyConfig` fields.
    """

    def __init__(
        self,
        changesets: Sequence[FakeChangeset] = (),
        releases: Sequence[FakeRelease] = (),
        *,
        include_base: bool = True,
        **config_overrides: Any,
    ) -> None:
        base_changeset = FakeChangeset(
            id=BASE_CHANGESET_ID,
            summary=BASE_SUMMARY,
            releases=[FakeChangesetRelease(name=BASE_RELEASE_NAME, type=BumpType.MINOR)],
        )
        base_release = FakeRelease(
            name=BASE_RELEASE_NAME,
            type=BumpType.MINOR,
            old_version="1.0.0",
            new_version="1.1.0",
            changesets=[BASE_CHANGESET_ID],
        )
        self.config: FakeApplyConfig = FakeApplyConfig(**config_overrides)
        self.changesets: list[FakeChangeset] = (
            [base_changeset, *changesets] if include_base else list(changesets)
        )
        self.releases: list[FakeRelease] = (
            [base_release, *releases] if include_base else list(releases)
        )

    def plan(self) -> FakePlan:
        """``getReleasePlan()`` (``index.test.ts:82-88``), minus ``preState`` (research 4.2)."""
        return FakePlan(changesets=list(self.changesets), releases=list(self.releases))


# ======================================================================================
# Manifest rendering -- what ProjectBuilder cannot express
# ======================================================================================


def _toml_array(values: Sequence[str]) -> str:
    if not values:
        return "[]"
    body = "".join(f'    "{value}",\n' for value in values)
    return f"[\n{body}]"


def member_manifest(
    name: str,
    version: str,
    *,
    dependencies: Sequence[str] = (),
    dev_dependencies: Sequence[str] = (),
    optional_dependencies: Mapping[str, Sequence[str]] | None = None,
    workspace_sources: Sequence[str] = (),
    private: bool = False,
    extra: str = "",
) -> str:
    """Render a member ``pyproject.toml``.

    The shared ``tmp_project`` / ``ProjectBuilder`` fixture cannot write ``[tool.uv.sources]``, and
    the test contract says not to add methods to ``tests/conftest.py`` for it -- so this renders it
    here. ``workspace_sources`` names the dependencies backed by a uv workspace source
    (``pkg-b = { workspace = true }``), the Python analogue of npm's ``workspace:`` protocol
    (research doc 04 section 2.6; ``website/docs/config/options.md``, on
    ``bump_workspace_sources_only``). The version constraint stays in the PEP 508 string, which is
    the whole point of the analogy.

    ``private=True`` writes the ``Private :: Do Not Upload`` classifier, matching
    ``ProjectBuilder.add_package(private=True)`` (research doc 02 section 12.1).
    """
    lines = ["[project]", f'name = "{name}"', f'version = "{version}"']
    if private:
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    lines.append(f"dependencies = {_toml_array(dependencies)}")
    if optional_dependencies:
        lines.append("")
        lines.append("[project.optional-dependencies]")
        lines.extend(
            f"{extra_name} = {_toml_array(reqs)}"
            for extra_name, reqs in optional_dependencies.items()
        )
    if dev_dependencies:
        lines.append("")
        lines.append("[dependency-groups]")
        lines.append(f"dev = {_toml_array(dev_dependencies)}")
    if workspace_sources:
        lines.append("")
        lines.append("[tool.uv.sources]")
        lines.extend(f"{source} = {{ workspace = true }}" for source in workspace_sources)
    text = "\n".join(lines) + "\n"
    return text + extra


def root_manifest(
    name: str = "workspace-root",
    version: str = "0.0.0",
    *,
    members: Sequence[str] = ("packages/*",),
    dependencies: Sequence[str] = (),
    dev_dependencies: Sequence[str] = (),
    workspace_sources: Sequence[str] = (),
    private: bool = True,
) -> str:
    """Render the workspace root ``pyproject.toml`` (uv workspace, ``members`` globs).

    The root is version-less in spirit -- ``apply`` rewrites its dependency pins but never its own
    ``version`` (``index.ts:174-188``; ``index.test.ts:591-623``) -- but PEP 621 requires a
    ``version`` when ``[project]`` is present, so one is written and asserted to stay put.
    """
    lines = [
        "[project]",
        f'name = "{name}"',
        f'version = "{version}"',
        'requires-python = ">=3.11"',
    ]
    if private:
        lines.append('classifiers = ["Private :: Do Not Upload"]')
    lines.append(f"dependencies = {_toml_array(dependencies)}")
    if dev_dependencies:
        lines.append("")
        lines.append("[dependency-groups]")
        lines.append(f"dev = {_toml_array(dev_dependencies)}")
    lines.append("")
    lines.append("[tool.uv.workspace]")
    lines.append(f"members = {_toml_array(members)}")
    if workspace_sources:
        lines.append("")
        lines.append("[tool.uv.sources]")
        lines.extend(f"{source} = {{ workspace = true }}" for source in workspace_sources)
    return "\n".join(lines) + "\n"


def changeset_body(summary: str = BASE_SUMMARY) -> str:
    """The minimal changeset file upstream's setup functions write (``index.test.ts:3120``).

    Upstream writes ``---\\n---\\n<summary>`` -- empty frontmatter, because ``apply`` only ever
    matches these files by **filename**; the release data comes from the plan, not the file. Kept
    identical so the deletion rows (40-42, 44) exercise the same path.
    """
    return f"---\n---\n{summary}"


# ======================================================================================
# write_workspace -- the testdir() equivalent
# ======================================================================================


def write_workspace(root: Path, files: Mapping[str, str | bytes]) -> Path:
    """Materialize ``{relative posix path: content}`` under ``root``; return ``root``.

    The ``testdir(fixture)`` equivalent (``index.test.ts:121``). Written with ``write_bytes`` so
    newline translation cannot corrupt the byte-exact formatting assertions (rows 1-4).
    """
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


def write_changeset_files(root: Path, plan: FakePlan) -> list[Path]:
    """Write ``.changeset/<id>.md`` for every changeset in ``plan``; return the paths.

    Port of the ``setupFunc`` repeated at ``index.test.ts:3115-3123``, ``3152-3160``,
    ``3189-3197`` and ``3280-3288``.
    """
    paths: list[Path] = []
    for changeset in plan.changesets:
        path = root / ".changeset" / f"{changeset.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(changeset_body(changeset.summary).encode("utf-8"))
        paths.append(path)
    return paths


def write_failing_generator(root: Path, *, filename: str = FAILING_GENERATOR_FILENAME) -> str:
    """Write the changelog generator whose both entry points raise; return its config value.

    Port of ``test-utils/failing-functions.ts``. The returned value is the ``./<file>`` form of
    ``config.changelog``, which ``website/docs/extending/changelog-plugins.md`` ("Choosing a
    generator", resolution order 3) resolves relative to ``.changeset/`` first and then the project
    root -- so it is written into ``.changeset/``.

    The module exposes the two functions **both** at module level and on a ``generator`` object,
    because the docs describe a ``ChangelogGenerator`` protocol object while a file-path generator
    could plausibly be loaded either way. A test double should not force that choice.
    """
    source = '''"""Always-failing changelog generator; port of test-utils/failing-functions.ts."""

from __future__ import annotations

from typing import Any

MESSAGE = "no chance"


def get_release_line(*args: Any, **kwargs: Any) -> str:
    raise RuntimeError(MESSAGE)


def get_dependency_release_line(*args: Any, **kwargs: Any) -> str:
    raise RuntimeError(MESSAGE)


class _FailingGenerator:
    get_release_line = staticmethod(get_release_line)
    get_dependency_release_line = staticmethod(get_dependency_release_line)


generator = _FailingGenerator()
'''
    path = root / ".changeset" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(source.encode("utf-8"))
    return f"./{filename}"


# ======================================================================================
# discover_packages -- the @manypkg/get-packages analogue
# ======================================================================================


@dataclass(frozen=True)
class PackageInfo:
    """One workspace member: its declared name/version and where its manifest lives."""

    name: str
    version: str
    dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class Packages:
    """The discovered workspace, mirroring ``@changesets/types`` ``Packages``.

    Same three fields as ``tests.engine.fake_state.Packages`` minus ``tool``: ``root_dir``,
    ``root_package`` (the root manifest, whose dependency pins are rewritten but whose own version
    is never bumped) and ``packages`` (the members).
    """

    root_dir: Path
    root_package: PackageInfo | None
    packages: list[PackageInfo]


def _read_project_table(manifest_path: Path) -> dict[str, Any]:
    data = tomllib.loads(manifest_path.read_bytes().decode("utf-8"))
    project = data.get("project")
    return project if isinstance(project, dict) else {}


def _package_info(manifest_path: Path) -> PackageInfo | None:
    project = _read_project_table(manifest_path)
    name = project.get("name")
    if not isinstance(name, str):
        return None
    version = project.get("version")
    return PackageInfo(
        name=name,
        version=version if isinstance(version, str) else "",
        dir=manifest_path.parent,
        manifest_path=manifest_path,
    )


def discover_packages(root: Path) -> Packages:
    """Discover a uv workspace under ``root``; the ``getPackages(tempDir)`` analogue.

    Deliberately small and honest: it is a **test double**, not molt's ecosystem backend. It reads
    ``[tool.uv.workspace].members`` globs from the root manifest and resolves each match that has a
    ``[project].name``. With no ``members`` at all the root is the single package -- the shape
    ``@manypkg/get-packages`` returns for a non-monorepo (``index.test.ts:153-259`` and ``:781-843``
    rely on it), and the shape ``website/docs/config/options.md`` describes for
    ``ecosystem = "auto"``.

    Names are returned **exactly as written**; PEP 503 normalization is the product's job and
    happens at comparison time, not parse time (harness progress log, Session 2 decision 4).
    """
    root = Path(root)
    root_manifest_path = root / "pyproject.toml"
    root_package = _package_info(root_manifest_path) if root_manifest_path.is_file() else None

    members: list[str] = []
    if root_manifest_path.is_file():
        data = tomllib.loads(root_manifest_path.read_bytes().decode("utf-8"))
        workspace = data.get("tool", {}).get("uv", {}).get("workspace", {})
        raw = workspace.get("members") if isinstance(workspace, dict) else None
        if isinstance(raw, list):
            members = [entry for entry in raw if isinstance(entry, str)]

    packages: list[PackageInfo] = []
    seen: set[Path] = set()
    for pattern in members:
        for candidate in sorted(root.glob(pattern)):
            manifest = candidate / "pyproject.toml"
            if not manifest.is_file() or manifest in seen:
                continue
            seen.add(manifest)
            info = _package_info(manifest)
            if info is not None:
                packages.append(info)
    if not members and root_package is not None:
        packages.append(root_package)
    return Packages(root_dir=root, root_package=root_package, packages=packages)


# ======================================================================================
# git helpers
# ======================================================================================


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git_status(root: Path) -> str:
    """``git status`` stdout, for the "nothing to commit" / "modified:" assertions."""
    return _git(root, "status").stdout


# ======================================================================================
# run_apply -- the testSetup harness
# ======================================================================================


def read(root: Path, relative: str) -> str:
    """Read a workspace file as text **without** newline translation (Windows-correct)."""
    return root.joinpath(*relative.split("/")).read_bytes().decode("utf-8")


def find_changed(changed_files: Sequence[Path | str], *tail: str) -> Path | None:
    """Return the changed path whose trailing path components are ``tail``, else ``None``.

    The ``changedFiles.find(a => a.endsWith(\\`pkg-a${path.sep}package.json\\`))`` idiom
    (``index.test.ts:279-281``), done on path components so it is separator-agnostic.
    """
    wanted = tuple(tail)
    for entry in changed_files:
        parts = Path(entry).parts
        if len(parts) >= len(wanted) and parts[-len(wanted) :] == wanted:
            return Path(entry)
    return None


def run_apply(
    root: Path,
    files: Mapping[str, str | bytes],
    plan: FakePlan,
    config: FakeApplyConfig | None = None,
    *,
    snapshot: str | None = None,
    pre: str | None = None,
    setup: Callable[[Path], object] | None = None,
) -> tuple[list[Path], Path]:
    """Build a tmp workspace, run ``molt.apply.apply_release_plan`` over it, return the results.

    Port of ``testSetup`` (``index.test.ts:91-144``): materialize the fixture, run an optional setup
    callable, discover packages, apply, return ``(changed_files, root)``.

    Upstream's ``testSetup`` also ``git init``s and commits the fixture. That half is **not**
    reproduced: the tests needing a real repository take the shared ``git_repo`` fixture instead
    (``tests/conftest.py``), which is Windows-safe and already committed. A second git-setup path
    here would be dead weight that could silently drift from it.

    ``apply_release_plan`` is imported **inside** this function so the module stays importable
    while the build-step-5 target does not exist -- the whole point of the phase's importorskip
    discipline.
    ``snapshot`` / ``pre`` are forwarded only when set (see the module docstring).
    """
    write_workspace(root, files)
    if setup is not None:
        setup(root)

    packages = discover_packages(root)

    # Late import, deliberately: keeps this helper module importable while
    # molt.apply.apply_release_plan does not exist (the TDD target of build step 5). The package
    # itself now exists -- it holds the edit_toml primitives. See the module docstring.
    from molt.apply import apply_release_plan  # pyrefly: ignore[missing-module-attribute]

    extra: dict[str, Any] = {}
    if snapshot is not None:
        extra["snapshot"] = snapshot
    if pre is not None:
        extra["pre"] = pre
    changed = apply_release_plan(
        plan,
        packages,
        config if config is not None else FakeApplyConfig(),
        cwd=root,
        **extra,
    )
    return [Path(entry) for entry in changed], root
