"""The outer release loop: pick a phase, then keep the release pull request or cut the releases.

Ports ``changesets/action`` v1.9.0 ``src/index.ts:57-176`` and ``src/run.ts:106-149``,
``:189-243``, ``:333-407`` (fetched and read 2026-07-31). This is **composition**: every part it
drives is already shipped and pinned --

* :func:`molt.action.run_version` and :func:`molt.action.run_publish` (build step 22) own the git
  choreography and the scripts,
* :func:`molt.action.get_changelog_entry` slices a written ``CHANGELOG.md``,
* :func:`molt.action.sort_changelog_entries` orders the sections,
* :func:`molt.action.select_mode` decides which phase this run is,
* :func:`molt.action.build_pull_request_body` renders the body,
* the forge seam opens, finds and updates the pull request and creates the releases.

-- and nothing here re-derives any of it.

What this module owes the reader that upstream does not
--------------------------------------------------------
**The existing-pull-request lookup happens before the version script runs**, not between the script
and the push (design D3). Upstream's comment states the reason exactly: fetch open pull requests
"before we push any changes that may inadvertently close the existing PRs". molt cannot reproduce
upstream's exact position without splitting :func:`~molt.action.run_version`, which owns branch
prep, the script, the commit *and* the force-push and whose five rows are pinned -- so the lookup
moves **earlier**, which is strictly further from the push and preserves the invariant the ordering
exists for.

**The publish phase pushes no tags** (design D6). Upstream pushes each tag inside the release loop,
one ``git push origin <tag>`` per package, then creates that package's release.
:func:`~molt.action.run_publish` already pushed **all** of them in one ``git push origin --tags``
before it returned, so the ordering guarantee upstream buys per tag -- the tag exists before the
release references it -- molt gets in bulk, one step earlier.

**There is no pre mode.** Upstream suffixes the title and the commit with ``(<pre tag>)`` and
prepends a warning banner when ``.changeset/pre.json`` says ``mode: pre``. molt has no such state
and never will: ``molt pre`` is a command whose whole body is a refusal pointing at
``molt version --pre {a,b,rc,dev}``, because changesets' ``1.0.1-next.0`` is not a legal PEP 440
version. Deferred rather than invented (``openspec/GAPS.md`` ``AP-1``).

Every side effect goes through an injected seam -- the forge, and :class:`molt.git.Git` -- so no
test can reach a real remote or a real API. :mod:`molt.forge` is resolved **inside** the call, never
imported at module scope: it costs ``httpx``, and ``tests/cli/test_cli.py`` pins that cost out of
the CLI's import graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molt.action.body import DEFAULT_PR_TITLE, build_pull_request_body, pull_request_entry
from molt.action.mode import Mode, mode_for, read_pending_changesets
from molt.action.run import VERSION_BRANCH_PREFIX, run_publish, run_version
from molt.action.utils import get_changelog_entry
from molt.ecosystem import discover_workspace, find_workspace_root, is_private
from molt.errors import MoltError
from molt.publish import tag_name
from molt.versioning import is_prerelease

if TYPE_CHECKING:
    from collections.abc import Sequence

    from molt.action.run import ChangedPackage, PublishedPackage
    from molt.ecosystem import Workspace

__all__ = [
    "CHANGELOG_FILENAME",
    "DEFAULT_VERSION_SCRIPT",
    "ActionResult",
    "run_action",
]

#: What a package's changelog is called. A package that has none is a package whose project
#: switched changelogs off, which is a supported configuration and not an error.
CHANGELOG_FILENAME = "CHANGELOG.md"

#: The version script the loop runs when the workflow names none -- molt's own version command.
#: Upstream's equivalent default is ``changeset version`` (``index.ts``). An argv list, never a
#: string: :func:`molt.action.run_version` takes argv precisely so a script with arguments is
#: expressible (build step 22 design D1).
DEFAULT_VERSION_SCRIPT: tuple[str, ...] = ("molt", "version")

#: :attr:`molt.ecosystem.Workspace.backend` for a repository that declares no workspace -- the
#: analogue of ``@manypkg``'s ``tool === "root"`` (``run.ts:44``). It selects the tag shape, which
#: is why the value is named here rather than tested for inline.
_SINGLE_BACKEND = "single"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """The four outputs a run reports (``website/docs/guides/ci-github-action.md``).

    Spelled in **snake_case**, a deliberate divergence from upstream's camelCase ``action.yml``
    outputs: every machine-readable payload molt emits -- ``molt status --output json``, the
    publish plan, the NDJSON tag stream -- is snake_case, and the composite action can rename them
    once, at the boundary, where the renaming is visible.

    Nothing here is GitHub-specific. :attr:`pull_request_number` is ``None`` for a run that opened
    or updated no pull request, which is every publish run and every run that did nothing.
    """

    #: Whether anything reached the package index.
    published: bool = False
    #: The packages that went out, name and version, as :func:`molt.action.run_publish` reported
    #: them -- not re-derived from tags or from the workspace.
    published_packages: tuple[PublishedPackage, ...] = ()
    #: Whether ``.changeset/`` holds anything at all. Reported in **every** case, the
    #: "changesets present but all empty" one included (design D1).
    has_changesets: bool = False
    #: The release pull request this run opened or updated.
    pull_request_number: int | None = None


def run_action(
    *,
    cwd: Path | str,
    publish: Sequence[str] | None = None,
    version_script: Sequence[str] | None = None,
    title: str | None = None,
    commit_message: str | None = None,
    base_branch: str | None = None,
    create_releases: bool = True,
    forge: Any = None,
    git: Any = None,
) -> ActionResult:
    """Run one half of the release loop, whichever half this repository is in.

    ``publish`` is the publish command as argv, or ``None`` for a workflow that only maintains the
    release pull request. Its presence is what makes the publish phase reachable at all
    (``index.ts:73-149``), and it also changes what the pull-request body promises the reader.

    ``base_branch`` names the branch being released and defaults to whatever ``cwd`` has checked
    out -- what a CI checkout leaves behind. It is resolved **before** the version phase runs,
    because :func:`~molt.action.run_version` switches branches and the answer afterwards would be
    the release branch.

    ``forge`` and ``git`` are seams. They default to a real :class:`~molt.forge.GitHubForge` and
    :class:`molt.git.Git`, resolved lazily inside the call.
    """
    root = Path(cwd)
    changesets = read_pending_changesets(root)
    has_changesets = bool(changesets)
    mode = mode_for(changesets, publish=publish is not None)

    if mode is Mode.VERSION:
        return _version_phase(
            root,
            has_publish_command=publish is not None,
            version_script=version_script,
            title=title,
            commit_message=commit_message,
            base_branch=base_branch,
            forge=forge,
            git=git,
        )
    # `publish is not None` is the condition that selected this mode in the first place; it is
    # repeated rather than asserted because a type checker cannot see that far, and an `assert` in
    # product code is a statement that vanishes under `python -O`.
    if mode is Mode.PUBLISH and publish is not None:
        return _publish_phase(
            root, command=publish, create_releases=create_releases, forge=forge, git=git
        )
    return ActionResult(has_changesets=has_changesets)


# ======================================================================================
# The version phase (index.ts:153-175; run.ts:106-148, :333-407)
# ======================================================================================


def _version_phase(
    root: Path,
    *,
    has_publish_command: bool,
    version_script: Sequence[str] | None,
    title: str | None,
    commit_message: str | None,
    base_branch: str | None,
    forge: Any,
    git: Any,
) -> ActionResult:
    """Version the repository and keep its release pull request up to date.

    The order is the whole point of this function and one row asserts it:

    1. **Look the open release pull request up** -- before anything is pushed (design D3).
    2. Run the version script and force-push the release branch
       (:func:`~molt.action.run_version`).
    3. Slice each changed package's changelog entry back out of the file the run just wrote.
    4. Build the body.
    5. Update the pull request that was found, or open one.

    Step 5 **reuses** the found pull request rather than opening a second: the release pull request
    accumulates changesets over days, and its number, its review comments and its subscribers have
    to survive every update (``run.ts:361-374``).
    """
    from molt.git import Git

    seam = Git(root) if git is None else git
    forge_seam = _resolve_forge(forge)

    base = base_branch if base_branch is not None else seam.current_branch()
    version_branch = f"{VERSION_BRANCH_PREFIX}{base}"

    existing = forge_seam.find_open_pull_request(version_branch, base=base)

    result = run_version(
        cwd=root,
        script=tuple(version_script) if version_script is not None else DEFAULT_VERSION_SCRIPT,
        commit_message=commit_message if commit_message is not None else DEFAULT_PR_TITLE,
        branch=base,
        git=seam,
    )

    body = build_pull_request_body(
        entries=[_body_entry(changed) for changed in result.changed_packages],
        base_branch=base,
        has_publish_command=has_publish_command,
    )
    pull_title = title if title is not None else DEFAULT_PR_TITLE

    if existing is None:
        pull = forge_seam.create_pull_request(
            version_branch, base=base, title=pull_title, body=body
        )
    else:
        pull = forge_seam.update_pull_request(existing.number, title=pull_title, body=body)

    return ActionResult(has_changesets=True, pull_request_number=pull.number)


def _body_entry(changed: ChangedPackage) -> dict[str, Any]:
    """One changed package as a pull-request body entry (``run.ts:333-344``).

    The manifest is the one the version run just wrote, so the version here is the **new** one --
    which is what the section heading and the changelog lookup both need.

    A package whose ``CHANGELOG.md`` is missing contributes an empty section rather than being
    dropped: the sections are a permutation of the packages the version run reported, never a
    filtered subset (design D4). Filtering here would silently drop a release from a public
    document, and a project that switched changelogs off still releases packages.
    """
    project = changed.manifest.get("project", {})
    name = str(project.get("name", changed.dir.name))
    version = str(project.get("version", ""))
    classifiers = project.get("classifiers") or []
    content, level = _changelog_entry(changed.dir, version, package=name, required=False)
    return pull_request_entry(
        name=name,
        version=version,
        content=content,
        highest_level=level,
        private=is_private(list(classifiers)),
    )


# ======================================================================================
# The publish phase (index.ts:73-149; run.ts:30-58, :118-149)
# ======================================================================================


def _publish_phase(
    root: Path,
    *,
    command: Sequence[str],
    create_releases: bool,
    forge: Any,
    git: Any,
) -> ActionResult:
    """Publish, then create one host release per published package.

    :func:`~molt.action.run_publish` raises on a non-zero exit from the publish command, which
    fails the run -- reporting a publish that half-failed as a success is the silent-CI-failure
    mode this whole tool exists to avoid (research README section 3.4). It raises **before** it
    pushes tags or reads the command's output, so molt cannot enumerate what a failing publish
    managed to upload; that divergence from upstream is recorded rather than papered over
    (``openspec/GAPS.md`` ``AP-14``).

    ``create_releases`` is upstream's ``createGithubReleases`` input and defaults to on. Switched
    off, the publish still happens and is still reported in full -- the switch is about the host,
    not about the release.
    """
    from molt.git import Git

    seam = Git(root) if git is None else git
    result = run_publish(command=list(command), cwd=root, git=seam)

    if create_releases and result.published_packages:
        workspace = discover_workspace(find_workspace_root(root))
        forge_seam = _resolve_forge(forge)
        for package in result.published_packages:
            _create_release(package, workspace=workspace, forge=forge_seam)

    return ActionResult(
        published=result.published,
        published_packages=result.published_packages,
        has_changesets=False,
    )


def _create_release(package: PublishedPackage, *, workspace: Workspace, forge: Any) -> None:
    """Create one host release for a published package (``run.ts:118-149``).

    Two per-package rules are ported from ``run.ts:30-58``, and they pull in opposite directions on
    purpose:

    * **No ``CHANGELOG.md`` at all -> skip silently.** That is what "the project switched changelogs
      off" looks like on disk, and failing the release loop over it would make changelogs
      effectively mandatory.
    * **A ``CHANGELOG.md`` with no section for the published version -> fail.** The file exists, so
      somebody meant to write release notes and they are not there. Publishing a release with an
      empty body would bury that.

    A **duplicate** release resolves to ``None`` and the loop carries on quietly (forge design D2,
    owner ruling 2026-07-31): re-running a publish that half-failed must complete the missing
    releases, not fail on the finished ones.

    No tag is pushed here. :func:`~molt.action.run_publish` already pushed all of them
    (design D6).
    """
    discovered = workspace.get(package.name)
    directory = discovered.directory if discovered is not None else workspace.root
    changelog = directory / CHANGELOG_FILENAME
    if not changelog.is_file():
        return

    content, _level = _changelog_entry(
        directory, package.version, package=package.name, required=True
    )
    tag = _release_tag(package, single_package=workspace.backend == _SINGLE_BACKEND)
    forge.create_release(
        tag,
        name=tag,
        body=content,
        prerelease=is_prerelease(package.version),
    )


def _release_tag(package: PublishedPackage, *, single_package: bool) -> str:
    """The tag the release points at, in the shape molt's own tagging wrote it.

    ``<pep503-name>@<version>`` for a workspace member through
    :func:`molt.publish.tag_name`, and ``v<version>`` for a single-package repository -- the branch
    ``molt git-tag`` design D2 defines, because there the version alone identifies the release.
    A release pointing at a tag that does not exist is a broken link on the host.

    This is the **third** copy of that branch (``molt.commands.git_tag``,
    ``molt.publish.publish._publish_tag``, here), which is one too many; the extraction is recorded
    as ``openspec/GAPS.md`` ``AP-5`` rather than performed inside a change that must not disturb
    ``tests/publish``.
    """
    if single_package:
        return f"v{package.version}"
    return tag_name(package.name, package.version)


# ======================================================================================
# Shared helpers
# ======================================================================================


def _changelog_entry(
    directory: Path, version: str, *, package: str, required: bool
) -> tuple[str, int]:
    """``(content, highest bump level)`` for ``version`` out of ``directory``'s ``CHANGELOG.md``.

    ``required`` is the difference between the two callers, and it is the rule from ``run.ts:30-58``
    read from both ends: the release loop must not create a release with notes nobody wrote, while
    the pull-request body must list every package the version run released even if one of them has
    no changelog to quote.

    :func:`molt.action.get_changelog_entry` raises for a missing section naming the version;
    re-raised here naming the **package** as well, because "no section for 1.4.0" in a monorepo
    release of thirty packages is not an actionable sentence on its own.
    """
    changelog = directory / CHANGELOG_FILENAME
    try:
        text = changelog.read_text(encoding="utf-8")
    except OSError:
        if required:
            raise
        return "", 0
    try:
        entry = get_changelog_entry(text, version)
    except MoltError as exc:
        if required:
            raise MoltError(
                f"{package} was published as {version} but {changelog} has no section for that "
                f"version. molt will not create a release with no notes; add the section, or "
                f"remove the changelog if this project does not keep one."
            ) from exc
        return "", 0
    return entry.content, entry.highest_level


def _resolve_forge(forge: Any) -> Any:
    """The injected forge, or a real GitHub backend built from the environment.

    Imported **inside** the function on purpose: :mod:`molt.forge` costs ``httpx``, and it stays
    off every module-scope import path molt has (``tests/cli/test_cli.py::HEAVY_MODULE_PREFIXES``).
    """
    if forge is not None:
        return forge
    from molt.forge import GitHubForge

    return GitHubForge()
