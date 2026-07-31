"""`molt version` -- consume changesets: bump versions, propagate, write changelogs, commit.

Ports ``packages/cli/src/commands/version/index.ts`` @ v3.0.0-next.9 against research doc 03
section 3 (3.1 the order of operations, 3.2 what ``applyReleasePlan`` mutates, 3.3 commit
behaviour, 3.4 ``--snapshot``), section 9.2 (the failure catalogue) and section 11.6 (exit codes).
Website docs: ``website/docs/cli/version.md`` and ``website/docs/cli/pre.md``. The 69-row
conformance suite is ``tests/cli/test_version.py``.

**Orchestration and validation, not algorithms.** Everything this command decides is decided one
layer down and already tested there: :mod:`molt.engine` computes the plan,
:func:`molt.apply.apply_release_plan` writes it under buffer-then-flush atomicity,
:mod:`molt.changelog` assembles the entries. What lives here is the flag surface, the validation
block, and the four steps that only exist at the command level -- resolving the snapshot commit,
reading the changelog template off disk, refreshing the lockfile, and committing. A rule that seems
to need new logic here almost certainly belongs to change 07 or change 10.

The order of operations (``index.ts:30-156``)
---------------------------------------------
Read the configuration, discover the workspace, **validate everything at once**, read the
changesets, assemble, apply, refresh the lockfile, commit. Nothing is read from ``.changeset/*.md``
and nothing is written until validation passes, so a misconfigured run leaves a pristine tree.

Validation accumulates (design D2). Every failure is collected and reported as **one** block:
``--ignore typo-a --ignore typo-b`` names both typos in one run rather than making the user fix
them one at a time.

Three divergences from upstream, each user-visible
--------------------------------------------------
1. **No changesets exits 1.** v3 semantics (``index.ts:92-98``); v2 exited 0. It is a *warning*,
   not an error -- an empty buffer is recoverable -- and the exit code is the contract CI reads.
2. **A failed commit is fatal.** Upstream logs "Changesets ran into trouble committing your files"
   and exits **0** (``version/index.ts:145-147``; research README section 3.4), so a job that
   versioned but could not commit reports success and the next job pushes nothing. The files are
   already written by then, so the exit code is the only signal left.
3. **No ``--allow-empty``.** Upstream's ``git.commit`` always passes it
   (``packages/git/src/index.ts:21``), so a run that wrote nothing still creates a commit. molt
   only commits what it actually wrote.

And two things this command deliberately does not do: it **never prompts** (it is what CI runs
unattended, so a prompt is a six-hour build), and it **never tags** -- that is ``molt git-tag``,
which is what makes "version in one job, tag in another" work at all.

Prerelease is stateless
-----------------------
``--pre {a,b,rc,dev}`` is an invocation flag, not a mode: the counter is derived from the version
on disk and no ``pre.json`` is read, written or deleted (research README section 4.2). The
changesets stay on disk so the next ``--pre`` run can recompute the same target release, and the
plain run that follows consumes them. Only PEP 440's four prerelease spellings are accepted --
``next`` has no PEP 440 rendering at all, which is exactly why upstream's ``1.0.1-next.0`` could
not be ported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path

    from molt.config.models import Config
    from molt.ecosystem import Workspace
    from molt.engine import PlanView

__all__ = ["PRE_PHASES", "run"]

#: The PEP 440 prerelease phases ``--pre`` accepts. ``a``/``b``/``rc`` are the spec's three
#: prerelease spellings and ``dev`` its development-release segment; there is no free-form tag.
#: Checked here as well as by the shell's enum, deliberately: ``run`` is a public seam, and a
#: version string that PyPI silently renormalizes is unrecoverable once uploaded.
PRE_PHASES: Final[tuple[str, ...]] = ("a", "b", "rc", "dev")

#: ``version/index.ts:96``, echoed verbatim by ``website/docs/cli/version.md``.
#: ``website/docs/guides/versioning.md`` used to write "No pending changesets found." instead --
#: corrected by this change, because the CLI reference page is the more specific oracle.
_NO_CHANGESETS: Final = "No unreleased changesets found."

#: The lockfile a uv workspace keeps beside its root manifest. Always part of a release commit:
#: staging the manifests without it commits a workspace whose lockfile is already stale.
_LOCKFILE: Final = "uv.lock"

#: What a snapshot version with a PEP 440 **local** segment cannot do.
#: ``website/docs/concepts/snapshots.md:52-55`` promises molt "warns loudly" about exactly this.
_NOT_UPLOADABLE: Final = (
    "carries a PEP 440 local version segment, so PyPI will not accept it. Publish this snapshot to "
    "an index that allows local versions."
)


def run(
    *,
    cwd: Path | None = None,
    ignore: Sequence[str] | None = None,
    snapshot: str | bool | None = None,
    pre: str | None = None,
    dry_run: bool = False,
    console: Any = None,
    git: Any = None,
    forge: Any = None,
    prompts: Any = None,
    **options: Any,
) -> PlanView:
    """Consume every pending changeset, and return the plan that was applied.

    ``snapshot`` is **one** keyword carrying all three spellings the shell accepts (design D1):
    ``None`` is no snapshot, ``True`` is ``--snapshot`` with no name, and a string is a named one.
    It cannot be a :class:`molt.engine.SnapshotParams` -- resolving its ``commit`` field needs a
    configuration read plus a git call, both of which are this layer's work, so a shell-built
    object would be a half-built one this command had to rebuild.

    ``console`` / ``git`` / ``forge`` are injectable seams; each defaults to the real
    implementation, built lazily so a run that neither commits nor links never pays for it.
    ``prompts`` is accepted and never used: ``version`` is non-interactive by construction, and
    taking the keyword is what lets the conformance suite *prove* the absence rather than merely
    not observe a prompt.

    Returns the applied plan as a :class:`molt.engine.PlanView` -- string versions, the same value
    ``molt status`` reports -- so a caller (and ``--dry-run``) sees exactly what landed.

    Raises:
        ExitError: with code 1 for an empty changeset buffer, any validation failure, an
            unusable configuration, a plan molt refuses to compute, or a failed commit.
    """
    del options, prompts  # `--non-interactive` is the shell's global; `version` never prompts.

    from pathlib import Path

    from molt.changeset import CHANGESET_DIR, read_changesets
    from molt.ecosystem import discover_workspace, find_workspace_root
    from molt.errors import ExitError

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(Path(cwd) if cwd is not None else Path.cwd())
    config = _resolve_config(root, console=console)
    workspace = discover_workspace(root, config.ecosystem)

    failures = _validate(workspace, config, ignore=ignore, snapshot=snapshot, pre=pre)
    if failures:
        # One block, one call (design D2). Reporting per failure is what turns a misconfiguration
        # into a series of round trips.
        console.error("\n".join(failures))
        raise ExitError(1)

    # A missing `.changeset/` is an empty buffer, not a precondition failure: `version` is the
    # command CI runs on every merge, and "there is nothing to release" is the same answer whether
    # the folder is absent or empty. `molt add`, which is about to *write* one, refuses instead.
    changesets = read_changesets(root) if (root / CHANGESET_DIR).is_dir() else []
    if not changesets:
        console.warn(_NO_CHANGESETS)
        raise ExitError(1)

    effective = config if ignore is None else config.model_copy(update={"ignore": tuple(ignore)})
    plan, view = _assemble(
        changesets,
        workspace,
        effective,
        snapshot=snapshot,
        pre=pre,
        git=git,
        root=root,
        console=console,
    )
    if snapshot is not None:
        _warn_if_not_uploadable(view, console=console)

    # Design D9: one report, printed from the same plan either way. A dry run is "everything up to
    # the flush", so the transitions a preview shows are by construction the transitions a real run
    # writes -- a separately rendered preview is how the two drift.
    console.info(_transitions(view, dry_run=dry_run))
    if dry_run:
        return view

    touched = _apply(plan, workspace, effective, root=root, snapshot=snapshot, pre=pre, forge=forge)
    _refresh_lockfile(root, console=console)
    _commit(effective, view, touched, root=root, git=git, console=console, snapshot=snapshot)
    return view


# ======================================================================================
# Configuration
# ======================================================================================


def _resolve_config(root: Path, *, console: Any) -> Config:
    """Load the configuration, rendering **both** channels (``molt.config`` design D3).

    Warnings are printed and the run continues; an unusable configuration prints every error at
    once and exits 1. Reporting one problem per run is precisely what the two-channel result shape
    exists to avoid.
    """
    from molt.config import load_config
    from molt.errors import ExitError

    result = load_config(root)
    for warning in result.warnings:
        console.warn(str(warning))
    if result.config is None:
        console.error("\n".join(str(error) for error in result.errors))
        raise ExitError(1)
    return result.config


# ======================================================================================
# Validation (``index.ts:37-65``) -- everything at once, before anything is read or written
# ======================================================================================


def _validate(
    workspace: Workspace,
    config: Config,
    *,
    ignore: Sequence[str] | None,
    snapshot: str | bool | None,
    pre: str | None,
) -> list[str]:
    """Every reason this invocation cannot proceed, in report order.

    Accumulated rather than raised one at a time (design D2), and computed entirely from the
    configuration, the flags and the workspace -- no changeset has been read at this point, which
    is what makes "validation precedes every write" true by construction.
    """
    failures: list[str] = []
    if ignore and config.ignore:
        failures.append(
            "The `--ignore` flag and the `ignore` option in your molt config cannot be combined; "
            "you can only use one of them at a time. --ignore named "
            f"{', '.join(ignore)}; the config names {', '.join(config.ignore)}."
        )
    failures.extend(_unknown_ignore_names(workspace, ignore))
    if pre is not None and snapshot is not None:
        failures.append(
            "`--pre` and `--snapshot` cannot be combined: a prerelease and a snapshot want "
            "incompatible version shapes, so there is nothing sensible to compose."
        )
    if pre is not None and pre not in PRE_PHASES:
        failures.append(
            f"`--pre {pre}` is not a PEP 440 prerelease phase. Use one of "
            f"{', '.join(PRE_PHASES)} -- a free-form channel name has no PEP 440 rendering, and "
            "PyPI would reject or silently renormalize it."
        )
    effective = config if ignore is None else config.model_copy(update={"ignore": tuple(ignore)})
    failures.extend(_unskipped_dependents(workspace, effective))
    return failures


def _unknown_ignore_names(workspace: Workspace, ignore: Sequence[str] | None) -> list[str]:
    """One failure per ``--ignore`` name the workspace does not contain (``index.ts:159-180``).

    A typo in ``--ignore`` must not silently release the package the user meant to skip, so an
    unmatched name is fatal here -- in contrast with the *config* ``ignore`` list, where an
    unmatched entry only warns: the flag is typed once by a human, the config list is a checked-in
    glob set. ``Workspace.get`` is PEP 503-aware, so ``--ignore foo-bar`` finds a member declaring
    ``Foo_Bar``.
    """
    return [
        f'The package "{name}" was passed to `--ignore` but it is not found in the project.'
        for name in (ignore or ())
        if workspace.get(name) is None
    ]


def _unskipped_dependents(workspace: Workspace, config: Config) -> list[str]:
    """Skipped packages whose published dependents are not also skipped (``index.ts:182-235``).

    Publishing a package whose dependency is frozen ships a broken constraint, so molt refuses and
    names the package to add. Two exemptions, both upstream's:

    * a **dev-group-only** dependent -- the graph is built with ``ignore_dev=True``, because a
      stale dev range on a skipped package cannot break a consumer's install. Note this is a
      *different* graph from the one the release engine walks, which does include dev edges because
      it still has to rewrite the range (``version/index.ts:191-194``);
    * a **private** dependent -- it never uploads, so it can safely depend on a skipped package
      (``:215-221``), and the check is on the dependent's own privacy rather than on whether this
      configuration versions it. That is what lets a *versioned* private application depend on an
      ignored library.
    """
    from molt.engine import get_dependents_graph, skipped_package_names, to_engine_config
    from molt.engine import to_engine_packages as to_packages
    from molt.names import normalize_name

    skipped = skipped_package_names(workspace, config)
    if not skipped:
        return []
    graph, _valid, _errors = get_dependents_graph(
        to_packages(workspace), to_engine_config(config, workspace), ignore_dev=True
    )
    dependents = {normalize_name(name): names for name, names in graph.items()}
    exempt = {normalize_name(name) for name in skipped}
    failures: list[str] = []
    for name in skipped:
        for dependent in dependents.get(normalize_name(name), ()):
            package = workspace.get(dependent)
            if normalize_name(dependent) in exempt or (package is not None and package.private):
                continue
            failures.append(
                f'The package "{dependent}" depends on the skipped package "{name}", but '
                f'"{dependent}" is not being skipped. Add it to `--ignore` too, or stop skipping '
                f'"{name}" -- releasing "{dependent}" now would publish a stale constraint.'
            )
    return failures


# ======================================================================================
# The plan
# ======================================================================================


def _assemble(
    changesets: Sequence[Any],
    workspace: Workspace,
    config: Config,
    *,
    snapshot: str | bool | None,
    pre: str | None,
    git: Any,
    root: Path,
    console: Any,
) -> tuple[Any, PlanView]:
    """Compute the release plan, and its reportable view.

    A plan molt refuses to compute -- an incoherent snapshot template, a changeset naming a package
    that is not in the workspace, a mixed changeset -- arrives as a :class:`~molt.errors.MoltError`
    and becomes exit 1 with the message on the console. Upstream lets those escape to its top-level
    funnel; converting here is what keeps the sentence in front of the user without a traceback
    (``tests/cli/test_version.py`` seam 4).
    """
    from molt.engine import (
        UNVERSIONED_PLACEHOLDER,
        assemble_release_plan,
        plan_view,
        to_engine_config,
        to_engine_packages,
    )
    from molt.errors import ExitError, MoltError

    try:
        plan = assemble_release_plan(
            changesets,
            # Every versionless member is already in the effective `ignore`, so the placeholder is
            # never a version molt plans from -- see `to_engine_packages`.
            to_engine_packages(workspace, placeholder_version=UNVERSIONED_PLACEHOLDER),
            to_engine_config(config, workspace),
            pre=_pre_phase(pre),
            snapshot=_snapshot_params(snapshot, config, git=git, root=root),
        )
    except MoltError as error:
        console.error(str(error))
        raise ExitError(1) from error
    return plan, plan_view(plan)


def _pre_phase(pre: str | None) -> Any:
    """``pre`` as the engine's ``PrePhase``; validation has already proved it is one of the four."""
    return pre


def _snapshot_params(snapshot: str | bool | None, config: Config, *, git: Any, root: Path) -> Any:
    """Build the engine's ``SnapshotParams``, resolving ``{commit}`` only if the template asks.

    Upstream calls ``getCurrentCommitId()`` **only** when the configured template mentions
    ``{commit}`` or ``{commit-short}`` (``index.ts:105-114``), and that conditional is why this
    object cannot be built at the shell boundary: it needs a configuration read and a git call. A
    run whose template needs no commit never shells out.
    """
    from molt.engine import SnapshotParams

    if snapshot is None:
        return None
    template = config.snapshot.prerelease_template or ""
    commit = None
    if "{commit}" in template or "{commit-short}" in template:
        commit = _git(root, git).get_current_commit_id()
    return SnapshotParams(tag=None if snapshot is True else str(snapshot), commit=commit)


def _warn_if_not_uploadable(view: PlanView, *, console: Any) -> None:
    """Warn once per distinct snapshot version that PyPI will refuse.

    ``website/docs/concepts/snapshots.md`` is explicit that a ``+local`` segment is "not
    uploadable" to PyPI and that molt warns loudly about it. Gated on the version actually carrying
    one: warning on a bare ``0.0.0.dev<datetime>``, which the same page calls uploadable
    everywhere, would be crying wolf.
    """
    from packaging.version import Version

    seen: set[str] = set()
    for release in view.releases:
        version = release.new_version
        if version in seen or Version(version).local is None:
            continue
        seen.add(version)
        console.warn(f"The snapshot version {version} {_NOT_UPLOADABLE}")


def _transitions(view: PlanView, *, dry_run: bool) -> str:
    """The run's report: every version transition, highest bump first.

    One renderer for both modes, differing only in its first line. "Writes nothing" is half of a
    dry run's contract and a run that printed nothing would satisfy it; rendering the preview from
    a *second* code path is the other way to fail it, because the two then drift. Each release has
    to be legible, including one that is in the plan only because a dependency moved out of range.
    """
    from molt.versioning import BumpType

    header = "Packages to be bumped:" if not dry_run else "Packages that would be bumped:"
    lines = [header]
    for bump in sorted(BumpType, key=lambda member: -member.rank):
        for release in view.releases:
            if release.type is bump:
                lines.append(
                    f"  - {release.name} {release.old_version} -> {release.new_version} "
                    f"({release.type.value})"
                )
    if dry_run:
        lines.append("Dry run: nothing was written.")
    return "\n".join(lines)


# ======================================================================================
# Applying, and the three side effects apply does not own
# ======================================================================================


def _apply(
    plan: Any,
    workspace: Workspace,
    config: Config,
    *,
    root: Path,
    snapshot: str | bool | None,
    pre: str | None,
    forge: Any,
) -> list[Path]:
    """Write the plan, and return every path that changed.

    The changelog layer's three inputs are resolved here because each of them is filesystem,
    configuration or network work that a pure writer must not do: the template is read off disk
    (gap ``CT-3``), the date is one timestamp for the whole run, and the forge is **one** instance
    for the whole release so its attribution cache survives across packages (forge design D2).
    """
    from molt.apply import apply_release_plan, to_apply_packages
    from molt.engine import to_engine_config

    return apply_release_plan(
        plan,
        to_apply_packages(workspace),
        to_engine_config(config, workspace),
        cwd=root,
        snapshot=snapshot,
        pre=pre,
        forge=forge if forge is not None else _forge(config),
        changelog_template=_changelog_template(root, config),
        date=_release_date(config),
    )


def _changelog_template(root: Path, config: Config) -> str | None:
    """Read the configured changelog-entry template, or ``None`` when there is none.

    The config value is a **filename** and a relative one resolves against the **workspace root**
    (owner ruling 2026-07-30, closing gap ``CT-3``): that is the directory the configuration itself
    lives in, it is the only anchor every package shares, and it is what makes one template
    describe a whole monorepo. :func:`molt.changelog.render_changelog` keeps taking Jinja2 *source
    text*, so the filesystem stops here.

    CRLF is folded to LF on read, for the same reason the built-in asset is: a template edited on
    Windows must not put a stray carriage return into every changelog the project publishes.
    """
    from pathlib import Path

    from molt.errors import MoltError

    name = config.changelog_template
    if not name:
        return None
    path = Path(name)
    resolved = path if path.is_absolute() else root / path
    if not resolved.is_file():
        raise MoltError(
            f'The changelog template "{name}" was not found (looked in {resolved}). '
            "`changelog_template` is a filename; a relative one is resolved against the "
            "workspace root."
        )
    return resolved.read_text(encoding="utf-8").replace("\r\n", "\n")


def _release_date(config: Config) -> datetime | None:
    """The single timestamp a dated template renders, or ``None`` when dates are off.

    Read through the :mod:`molt.clock` seam and read **once**: a template that stamps two packages
    in one release with different dates would be reporting the duration of the run.
    """
    from molt import clock

    return clock.now() if config.changelog_dates else None


def _forge(config: Config) -> Any:
    """One forge for the whole release, or ``None`` when the configured backend has no client.

    Constructed unconditionally for a GitHub project rather than only when the generator looks like
    it needs one: the generator contract passes ``forge`` to both methods on every call, a
    forge-free generator ignores it, and construction is total -- it makes no request and reads no
    network. What it must **not** be is one instance per package: the attribution cache is scoped
    per instance (forge design D2), so a fresh forge per release would re-fetch every shared commit.

    A project configured with ``changelog = "github"`` and no token still fails loudly rather than
    writing a linkless changelog (gap ``CG-3``, ratified 2026-07-30) -- the refusal lives in the
    generator, and passing a real forge is what lets it get as far as telling the user which
    credential is missing.
    """
    if config.changelog is False or config.forge != "github":
        return None
    from molt.forge import GitHubForge

    return GitHubForge()


def _refresh_lockfile(root: Path, *, console: Any) -> None:
    """Re-resolve ``uv.lock`` when the workspace has one (``cli/version.md``: "one `uv lock` call").

    Cosmetic in npm, **load-bearing in Python**: ``uv.lock`` records every member's version and its
    ``requires-dist`` metadata, so a release that left it stale makes the very next
    ``uv sync --locked`` fail. :func:`molt.apply.apply_release_plan` has already patched the
    recorded *versions* in place, inside its atomic flush; this call is what also refreshes the
    metadata the in-place edit cannot reach (gap ``ARP-2``).

    A workspace with no lockfile gets no lockfile: creating one is a decision a release command has
    no business making. A failed resolve is reported and the run continues -- the release itself is
    already on disk and correct, and the in-place patch keeps the lockfile's versions honest, so
    aborting here would trade a stale metadata table for a half-reported release.

    One case needs a stronger move. ``uv lock`` **refuses to run at all** when the file in its way
    is not a lockfile it can parse ("TOML parse error ... missing field `version`"), which would
    leave a corrupt or truncated ``uv.lock`` corrupt forever. A lockfile is a generated artifact,
    not authored content, so molt moves it aside, regenerates, and says so. The original bytes are
    held in memory and put back verbatim if the resolve then fails, so the destructive half only
    survives a success.
    """
    lockfile = root / _LOCKFILE
    if not lockfile.is_file():
        return
    original = lockfile.read_bytes()
    replaced = not _is_lockfile(original)
    if replaced:
        console.warn(
            f"{_LOCKFILE} is not a readable uv lockfile, so it is being regenerated from the "
            "released manifests."
        )
        lockfile.unlink()
    completed = _uv_lock(root, console=console)
    if completed is None or completed.returncode != 0:
        if replaced:
            lockfile.write_bytes(original)
        if completed is not None:
            console.warn(
                f"Could not refresh {_LOCKFILE} (`uv lock` exited {completed.returncode}). The "
                "released versions were still written into it; run `uv lock` yourself before "
                f"committing.\n{completed.stderr.strip()}"
            )


def _uv_lock(root: Path, *, console: Any) -> Any:
    """Run ``uv lock`` in ``root``, or report that uv could not be started and return ``None``."""
    import subprocess

    try:
        return subprocess.run(
            ["uv", "lock"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as error:  # uv is not installed, or not on PATH
        console.warn(f"Could not refresh {_LOCKFILE}: {error}")
        return None


def _is_lockfile(data: bytes) -> bool:
    """Whether ``data`` is a uv lockfile at all -- TOML carrying a top-level ``version``.

    Deliberately the weakest possible check. Anything stronger would be molt second-guessing uv's
    own format, and the only decision resting on it is "may this file be regenerated".
    """
    import tomllib

    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    return isinstance(document.get("version"), int)


# ======================================================================================
# The commit (``index.ts:132-156``)
# ======================================================================================


def _commit(
    config: Config,
    view: PlanView,
    touched: Sequence[Path],
    *,
    root: Path,
    git: Any,
    console: Any,
    snapshot: str | bool | None,
) -> None:
    """Stage and commit the release when ``commit`` is configured; otherwise do nothing.

    Three rules and two divergences.

    * **Nothing without configuration.** ``commit`` defaults to false and the default path ends
      with "review them and commit at your leisure"; a release tool that commits unasked is one
      people stop trusting.
    * **A snapshot never commits**, even when commit is configured (``index.ts:56``): a snapshot
      burns a throwaway version on a throwaway checkout, and committing it would poison the branch
      it was cut from.
    * **The lockfile is part of the release commit**, staged alongside the manifests, changelogs
      and consumed changesets. Its own ``git add`` is tolerant, because a workspace that has no
      lockfile is not an error and must not fail the release.

    The divergences: no ``--allow-empty`` (upstream always passes it, so a run that wrote nothing
    still commits), and a **failed commit is fatal** (upstream logs and exits 0 -- research README
    section 3.4). Both are cited in the module docstring; do not "fix" them back toward upstream.
    """
    from molt.commit import load_provider, normalize_skip_ci
    from molt.errors import ExitError

    setting = config.commit
    if setting is False or snapshot is not None:
        return
    ref, options = setting
    provider = load_provider(ref, method="get_version_message")
    skip_ci = normalize_skip_ci((options or {}).get("skip_ci"), command="version")
    message = provider.get_version_message(view, skip_ci)  # pyrefly: ignore[missing-attribute]

    client = _git(root, git)
    try:
        client.add(*_staged(touched, root=root))
        _stage_lockfile(client, root=root)
        client.commit(message)
    # Broad on purpose: a `GitError`, an injected double raising something of its own and an OS
    # error all mean the same thing to the user -- the release landed and the commit did not.
    except Exception as error:
        console.error(
            f"molt versioned the packages but could not commit the result: {error}\n"
            "The release itself is already written to disk; commit it yourself, or fix the "
            "problem and re-run with `commit` disabled."
        )
        raise ExitError(1) from error


def _staged(touched: Sequence[Path], *, root: Path) -> list[str]:
    """Every path the release wrote, **relative to the workspace root** (``index.ts:137``).

    Relative because that is what upstream stages and because an absolute path is not a portable
    thing to put in a repository's index. Deleted changeset files are included: a deletion that is
    never staged is a release commit that silently keeps the consumed changeset.
    """
    import os

    paths: list[str] = []
    for path in touched:
        try:
            paths.append(str(path.relative_to(root)))
        except ValueError:  # outside the workspace root: stage it as written
            paths.append(str(path))
    return [path.replace(os.sep, "/") for path in paths]


def _stage_lockfile(client: Any, *, root: Path) -> None:
    """Stage ``uv.lock``, tolerating its absence.

    Its own call, deliberately: the lockfile belongs in the release commit whether or not this
    particular run rewrote it (a workspace can be locked and unchanged), but a repository that has
    no lockfile at all must not have its release fail on a pathspec that matches nothing.
    """
    from molt.errors import MoltError

    try:
        client.add(_LOCKFILE)
    except MoltError:
        if (root / _LOCKFILE).is_file():
            raise


def _git(root: Path, git: Any) -> Any:
    """The injected git client, or a real one bound to the workspace root."""
    if git is not None:
        return git
    from molt.git import Git

    return Git(root)
