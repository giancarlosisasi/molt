"""The two CI loops: open a version branch, and publish what was merged.

Ports ``packages/release-utils/src/run.ts`` (research doc 04 sections 6.1 and 6.2). Both loops are
thin: they drive a **script the caller supplies** -- normally ``molt version`` or ``molt publish``
-- and own only the git choreography around it, which is the part a workflow author cannot express
in YAML without getting it subtly wrong.

What each loop owns
-------------------
:func:`run_version` switches to ``changeset-release/<branch>`` (``run.ts:112``), records every
package's version, runs the script, works out what actually moved, commits, and force-pushes
(``run.ts:146``). :func:`run_publish` runs the publish script, pushes the tags the script created,
and reports which packages went out by scraping the ``New tag:`` lines it printed.

Two deliberate divergences from upstream
-----------------------------------------
* **``script`` is an argv list, not a bare string** (design D1). ``run.ts:121`` splits a command
  string on whitespace and runs it with no arguments, so ``molt version --snapshot canary`` is not
  expressible at all (research doc 03 section 9.21). Taking argv also removes every shell-quoting
  question, which is the other half of that bug.
* **The changed set is derived from versions, not from touched files** (design D2). An ignored
  package's dependency pin can be rewritten without the package itself being released, and this
  answer becomes the body of a public pull request -- it has to mean "these were released". The
  before/after version diff gets that for free, which is why neither this module nor upstream's
  ``getChangedPackages`` (``utils.ts:16-31``) takes an ``ignore`` argument.

Everything that touches the remote goes through the injected :class:`~molt.git.Git` seam, so a test
that does not hand one over pushes to whatever ``origin`` its own temporary clone has, and never
anywhere real.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molt.ecosystem import discover_workspace, find_workspace_root, read_toml
from molt.errors import ExitError, MoltError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from molt.ecosystem import Package, Workspace

__all__ = [
    "DEFAULT_COMMIT_MESSAGE",
    "VERSION_BRANCH_PREFIX",
    "ChangedPackage",
    "PublishedPackage",
    "RunPublishResult",
    "RunVersionResult",
    "run_publish",
    "run_version",
]

#: ``run.ts:112`` -- the version branch is always ``changeset-release/<the branch being released>``.
#: A fixed prefix rather than a configurable one: a workflow that has to guess the branch name
#: cannot find the pull request it opened last time.
VERSION_BRANCH_PREFIX = "changeset-release/"

#: ``run.ts:139`` -- the commit the version branch carries when the caller names no other.
DEFAULT_COMMIT_MESSAGE = "Version Packages"

#: ``run.ts:47`` -- a monorepo publish announces ``New tag: <name>@<version>``.
_MONOREPO_TAG_PATTERN = re.compile(r"New tag:\s+(\S+)@(\S+)")

#: ``run.ts:75`` -- a single-package publish announces ``New tag: v<version>``, with no name to
#: match against. The line is a marker, not a source of data: the version comes from the manifest,
#: exactly as it does on the monorepo path.
_SINGLE_TAG_PATTERN = re.compile(r"New tag:")

#: :attr:`molt.ecosystem.Workspace.backend` for a repository that declares no workspace -- the
#: analogue of ``@manypkg``'s ``tool === "root"`` test (``run.ts:44``).
_SINGLE_BACKEND = "single"


@dataclass(frozen=True, slots=True)
class ChangedPackage:
    """A package whose version moved during :func:`run_version`.

    ``manifest`` is the parsed ``pyproject.toml`` document -- the direct analogue of upstream's
    ``packageJson`` field -- read **after** the script ran, so it carries the new version and any
    dependency pin the release rewrote.
    """

    #: Absolute directory, spelled the way the caller spelled ``cwd``.
    dir: Path
    #: The same directory relative to the workspace root; what a pull-request body prints.
    relative_dir: Path
    #: The parsed manifest document.
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunVersionResult:
    """What :func:`run_version` pushed, and where it pushed it."""

    #: ``changeset-release/<branch>`` (``run.ts:112``).
    version_branch: str
    #: Only packages whose **version** changed, in workspace discovery order (design D2).
    changed_packages: tuple[ChangedPackage, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishedPackage:
    """One package a publish script announced with a ``New tag:`` line."""

    name: str
    version: str


@dataclass(frozen=True, slots=True)
class RunPublishResult:
    """What :func:`run_publish` saw go out.

    ``published`` is ``False`` with an empty :attr:`published_packages` when the script announced
    no tags -- the normal "nothing to release" outcome, not a failure (``run.ts:88-97``).
    """

    published: bool
    published_packages: tuple[PublishedPackage, ...] = ()


def run_version(
    *,
    cwd: Path | str,
    script: Sequence[str],
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
    branch: str | None = None,
    git: Any = None,
) -> RunVersionResult:
    """Run the version script on ``changeset-release/<branch>`` and force-push the result.

    The order is upstream's (``run.ts:106-148``) and each step depends on the one before it:

    1. Switch to the version branch, creating it when it does not exist yet, and hard-reset it onto
       the commit being released. The reset is what makes a **re-run** idempotent: the branch left
       over from the previous run is discarded rather than built on, so the pull request always
       shows one release's worth of changes.
    2. Record every discovered package's version.
    3. Run ``script`` in ``cwd``. A non-zero exit raises :class:`~molt.errors.ExitError` rather
       than being logged and stepped over -- research README section 3.4's "silent CI failure" is
       the failure mode this whole tool exists to avoid.
    4. Re-discover, and keep the packages whose version differs (design D2).
    5. Commit everything, unless the script already committed it -- a project configured with
       ``commit = true`` runs ``molt version`` with its own commit step.
    6. Force-push. The version branch is molt's to own; a human who pushed to it will lose that
       edit, which is upstream's rule too (``run.ts:146``) and the only way a re-run converges.

    ``branch`` names the branch being released and defaults to whatever ``cwd`` currently has
    checked out, which is what a CI checkout leaves behind.
    """
    from molt.git import Git

    root = Path(cwd)
    seam = Git(root) if git is None else git

    base_branch = branch if branch is not None else seam.current_branch()
    version_branch = f"{VERSION_BRANCH_PREFIX}{base_branch}"
    head = seam.get_current_commit_id()

    seam.switch_to_maybe_existing_branch(version_branch)
    seam.reset(head)

    workspace_root = find_workspace_root(root)
    before = _versions_by_directory(discover_workspace(workspace_root))
    _run_script(script, cwd=root)
    after = discover_workspace(workspace_root)

    changed = tuple(
        _changed_package(package, workspace_root)
        for package in after.packages
        if before.get(package.directory) != package.version
    )

    if not seam.is_clean():
        seam.add(root)
        seam.commit(commit_message)
    seam.push(version_branch, force=True)

    return RunVersionResult(version_branch=version_branch, changed_packages=changed)


def run_publish(
    *,
    command: Sequence[str],
    cwd: Path | str,
    git: Any = None,
) -> RunPublishResult:
    """Run the publish script, push the tags it created, and report what went out.

    The script owns the upload **and** the tags (``run.ts:41``): molt's own ``molt publish`` writes
    them, and this loop only pushes what it finds, so a script that failed halfway leaves the tags
    it did create pushed and the rest absent -- which is the honest record of a partial publish.

    Which packages went out is scraped from the script's stdout, not from the tag list, because
    only the script knows which uploads the index actually accepted. A monorepo announces
    ``New tag: <name>@<version>`` (``run.ts:47``) and a single-package repository announces
    ``New tag: v<version>`` with no name (``run.ts:75``); the reported version comes from the
    manifest in both cases, so a tag spelled unusually cannot invent a release.
    """
    from molt.git import Git

    root = Path(cwd)
    seam = Git(root) if git is None else git

    stdout = _run_script(command, cwd=root)
    seam.push_tags()

    workspace = discover_workspace(find_workspace_root(root))
    released = (
        _single_package_releases(stdout, workspace)
        if workspace.backend == _SINGLE_BACKEND
        else _monorepo_releases(stdout, workspace)
    )
    return RunPublishResult(published=bool(released), published_packages=released)


# ======================================================================================
# Helpers
# ======================================================================================


def _run_script(argv: Sequence[str], *, cwd: Path) -> str:
    """Run ``argv`` in ``cwd`` and return its stdout, raising on a non-zero exit.

    ``argv`` is a list, never a string, and it is never handed to a shell (design D1). Upstream
    splits a command string on whitespace, which loses every quoted argument and makes
    ``--snapshot canary`` unexpressible.

    Only stdout is captured -- :func:`run_publish` scrapes it. **stderr is inherited on purpose**,
    so the script's own diagnostics reach the workflow log as it runs instead of being swallowed
    into an exception message nobody sees when the job is cancelled. That is also why the raised
    :class:`~molt.errors.ExitError` carries only the status: the operator already has the output.
    """
    if not argv:
        raise MoltError("The script to run must name at least one argument")
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise ExitError(completed.returncode)
    return completed.stdout


def _versions_by_directory(workspace: Workspace) -> dict[Path, str | None]:
    """Every package's version, keyed on its directory (``utils.ts:16-31``).

    Keyed on the directory rather than the name because a release may **rename** nothing but a
    workspace can gain a package mid-run; a directory that was not there before has no previous
    version, so the package reads as changed, which is the answer a pull-request body wants.
    """
    return {package.directory: package.version for package in workspace.packages}


def _changed_package(package: Package, workspace_root: Path) -> ChangedPackage:
    """Read ``package``'s just-written manifest back off disk into a :class:`ChangedPackage`."""
    try:
        relative = package.directory.relative_to(workspace_root)
    except ValueError:  # pragma: no cover - a member outside its own workspace root
        relative = package.directory
    return ChangedPackage(
        dir=package.directory,
        relative_dir=relative,
        manifest=read_toml(package.manifest_path),
    )


def _monorepo_releases(stdout: str, workspace: Workspace) -> tuple[PublishedPackage, ...]:
    """``New tag: <name>@<version>`` lines, resolved against the workspace (``run.ts:44-64``).

    The announced name is matched **PEP 503-normalized** through
    :meth:`molt.ecosystem.Workspace.get`, so a script that prints ``Foo_Bar`` finds the package
    declaring ``foo-bar``. A name that matches nothing is a real error: it means the publish script
    and molt disagree about what this repository contains, and guessing would report a release
    nobody can find.
    """
    released: list[PublishedPackage] = []
    for line in stdout.splitlines():
        match = _MONOREPO_TAG_PATTERN.search(line)
        if match is None:
            continue
        package = workspace.get(match.group(1))
        if package is None:
            raise MoltError(f"Package {match.group(1)} was published but is not in this workspace")
        released.append(PublishedPackage(name=package.name, version=package.version or ""))
    return tuple(released)


def _single_package_releases(stdout: str, workspace: Workspace) -> tuple[PublishedPackage, ...]:
    """The one root package, if any ``New tag:`` line was printed at all (``run.ts:67-85``).

    A single-package tag carries no name to match on, so the first announcement is enough and the
    rest are the same release; upstream breaks out of the loop for the same reason.
    """
    if not workspace.packages:
        return ()
    if not any(_SINGLE_TAG_PATTERN.search(line) for line in stdout.splitlines()):
        return ()
    package = workspace.packages[0]
    return (PublishedPackage(name=package.name, version=package.version or ""),)
