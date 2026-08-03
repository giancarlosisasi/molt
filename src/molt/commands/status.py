"""`molt status` -- Report the pending release plan; the CI gate for 'you forgot a changeset'.

Ports ``packages/cli/src/commands/status/index.ts`` @ v3.0.0-next.9 against the behaviour spec in
``roadmap/research/changesets-03-cli-and-ux.md`` section 5 -- 5.1 (flow), 5.2 (the exit-code
contract), 5.3 (the ``--output`` payload) and 5.4 (human output). Website docs:
``website/docs/cli/status.md`` and ``website/docs/guide/status.md``. The conformance suite is
``tests/cli/test_status.py``.

``status`` writes nothing. It is the read-only face of the computation ``molt version`` performs,
which is what makes it both the "what am I about to release?" check and the pull-request gate.

Three contracts this command owns
---------------------------------
**The stream contract** (design D1). Every human-facing byte -- the banner, the plan preview, the
gate's guidance -- leaves through the :mod:`molt.ui.console` seam, which writes to **stderr**. The
``--output json`` payload is the only thing written to **stdout**, directly and alone. Without that
split ``molt status --output json | jq`` -- the recipe ``guides/status.md`` tells CI to run -- dies
on the banner, because ``json.loads`` sees it first.

**The payload outlives the gate** (design D2, research doc 03 section 11.7 item 4). Upstream
evaluates the CI gate at ``status/index.ts:43-51`` and only honours ``--output`` at ``:53``, so the
one run a CI job most wants machine-readable output from -- the failing one -- produces none. molt
emits the plan first and trips the gate second.

**snake_case payload keys** (design D3). ``old_version`` / ``new_version``, not upstream's
camelCase. The spelling and its rationale live in :mod:`molt.engine.view`, which owns the payload.

The CI gate, exactly
--------------------
Exit 1 **iff** at least one *versionable* package changed since the comparison ref **and** no
changeset is pending (``status/index.ts:43-51``). "Versionable" is upstream's ``shouldSkipPackage``
read for Python: not ``ignore``d, and not private while ``private_packages.version`` is false --
:func:`molt.engine.is_versionable`. Everything else exits 0, including the four negative cases
research doc 03 section 5.2 spells out: nothing changed, only ignored packages changed, only
non-versionable private packages changed, and changes that miss ``changed_file_patterns``.

Two comparison refs, and they are **not** the same thing (upstream's shape, ported):
``--since`` filters which *changesets* count as new, and is absent by default -- with no flag every
pending changeset is reported. The *changed-package* query falls back to the configured
``base_branch`` when ``--since`` is absent. Collapsing the two would make ``molt status`` on a
feature branch silently hide changesets that landed on the base branch.

One addition to upstream's output: the gate names the packages that changed without a changeset.
Upstream prints only the two guidance sentences, which leaves a contributor in a thirty-package
monorepo to work out which package it means.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["JSON_OUTPUT", "run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.engine.view import PlanView, ReleaseView

#: The one ``--output`` value ``status`` accepts. Upstream's ``--output <file>`` wrote a JSON file
#: (``status/index.ts:53-58``); molt prints to stdout instead, so the plan can be piped without the
#: command choosing a filename -- and so ``status`` keeps its promise to write nothing at all.
JSON_OUTPUT = "json"

#: The header, printed even when nothing is pending (research doc 03 section 5.4;
#: ``guides/status.md``, "When there is nothing pending, the header prints with an empty body").
#: Silence would be indistinguishable from a crash.
_HEADER = "Packages to be bumped:"

#: What ``--verbose`` prints in the source position of a release nobody wrote a changeset for
#: (``guides/status.md``). Reading it off the plan's empty ``changesets`` list is design D6: a
#: package can have both a changeset *and* a propagated bump, so inferring propagation by asking
#: whether a changeset names the package is wrong for exactly the interesting case.
_DEPENDENCY_BUMP = "(dependency bump)"

#: Upstream's two guidance sentences (``status/index.ts:44-49``), with the command renamed. Pinned
#: by the conformance suite: they are the only thing that tells a contributor what to do next.
_GATE_GUIDANCE = (
    "Some packages have been changed but no changesets were found. "
    "Run `molt add` to resolve this error."
)
_GATE_EMPTY_HINT = "If this change doesn't need a release, run `molt add --empty`."


def run(
    *,
    cwd: Path | None = None,
    since: str | None = None,
    verbose: bool = False,
    output: str | None = None,
    console: Any = None,
    git: Any = None,
    **options: Any,
) -> PlanView:
    """Report the pending release plan, and act as the CI gate.

    Returns the plan as a :class:`molt.engine.view.PlanView` -- string versions, snake_case fields
    and no prerelease-state field -- so the value a caller receives is the value ``--output json``
    prints. Raises :class:`~molt.errors.ExitError` with code 1 when the gate trips, **after** the
    plan has already been reported.

    ``console`` and ``git`` are injectable seams. ``console`` defaults to the module-level
    :data:`molt.ui.console.console`; ``git`` defaults to a :class:`molt.git.Git` bound to the
    workspace root, and is a real repository in the conformance suite because the since-ref
    comparison has nothing to fake.

    Every heavy import happens here rather than at module scope -- ``tests/cli/test_cli.py``'s
    import-light assertion pins that for every ``molt.commands.*`` module (design D7).
    """
    del options  # `--non-interactive` is the shell's global; `status` never prompts.

    from pathlib import Path

    from molt.changeset import read_changesets
    from molt.ecosystem import discover_workspace, find_workspace_root, resolve_workspace_versions
    from molt.engine import (
        assemble_release_plan,
        plan_view,
        to_engine_config,
        to_engine_packages,
    )
    from molt.errors import ExitError

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(Path(cwd) if cwd is not None else Path.cwd())
    config = _resolve_config(root, console=console)
    workspace = discover_workspace(root, config.ecosystem)
    # One resolution pass for the whole command (version-sources design D1), so a package whose
    # version lives in a file is previewed at its **real** current version. `status` and `version`
    # must drive the engine from identical inputs or the preview and the release disagree.
    resolution = resolve_workspace_versions(workspace)
    for entry in resolution.unresolved:
        console.warn(f"{entry.reason} It is skipped; the rest of the plan is unaffected.")

    # `--since` filters the changesets; its absence means "every pending changeset", which is
    # upstream's shape (`getReleasePlan(cwd, since, config)`, `status/index.ts`) and not the same
    # default the changed-package query below uses.
    changesets = read_changesets(root, since_ref=since)
    plan = assemble_release_plan(
        changesets,
        to_engine_packages(workspace, resolution=resolution),
        to_engine_config(config, workspace, resolution),
    )
    view = plan_view(plan)

    # Design D2: report before deciding the exit code, so the failing CI run still produces a plan.
    if output is None:
        console.info(_render(view, verbose=verbose))
    else:
        _emit_json(view, output)

    changed = _changed_versionable_packages(
        workspace, config, since=since, git=git, root=root, resolution=resolution
    )
    if changed and not view.changesets:
        console.error(_GATE_GUIDANCE)
        console.error(_GATE_EMPTY_HINT)
        console.error(f"Changed without a changeset: {', '.join(changed)}.")
        raise ExitError(1)
    return view


# ======================================================================================
# Configuration
# ======================================================================================


def _resolve_config(root: Path, *, console: Any) -> Any:
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
        for error in result.errors:
            console.error(str(error))
        raise ExitError(1)
    return result.config


# ======================================================================================
# Human output (design D1: through the console seam, which writes to stderr)
# ======================================================================================


def _render(view: PlanView, *, verbose: bool) -> str:
    """Render the plan as the grouped list ``guides/status.md`` documents.

    Groups run highest bump first and a group with no members is omitted, so the shape of the
    output is the shape of the release. Without ``--verbose`` a release is bare -- name only:
    asserting that absence is what stops "verbose" from quietly becoming the only mode.
    """
    from molt.changeset import CHANGESET_DIR
    from molt.versioning import BumpType

    lines = [_HEADER]
    for bump in sorted(BumpType, key=lambda member: -member.rank):
        group = [release for release in view.releases if release.type is bump]
        if not group:
            continue
        lines.append(f"- {bump.value}")
        for release in group:
            lines.extend(_release_lines(release, verbose=verbose, changeset_dir=CHANGESET_DIR))
    return "\n".join(lines)


def _release_lines(release: ReleaseView, *, verbose: bool, changeset_dir: str) -> list[str]:
    """One release's lines: the name, and under ``--verbose`` its version and its sources.

    The changeset path is composed from the id rather than carried from the filesystem, which keeps
    it POSIX on every platform -- ``.changeset/tidy-eels-return.md`` is what the docs show and what
    a reader pastes into an editor.
    """
    if not verbose:
        return [f"  - {release.name}"]
    lines = [f"  - {release.name} -> {release.new_version}"]
    if release.changesets:
        lines.extend(f"    - {changeset_dir}/{changeset}.md" for changeset in release.changesets)
    else:
        lines.append(f"    - {_DEPENDENCY_BUMP}")
    return lines


# ======================================================================================
# Machine output (design D1: stdout, alone, so `json.loads` over the whole stream works)
# ======================================================================================


def _emit_json(view: PlanView, output: str) -> None:
    """Write the plan to stdout as JSON, and nothing else to stdout.

    ``sys.stdout`` is resolved at call time, never cached: a caller (or a test's capture fixture)
    may have replaced it since import.

    Any ``--output`` value other than ``json`` is refused rather than treated as a filename.
    Upstream's ``--output <file>`` wrote the plan to disk, and ``status`` writing to disk is exactly
    the promise this command makes it does not do.
    """
    import json
    import sys

    from molt.engine import plan_payload
    from molt.errors import MoltError

    if output.strip().lower() != JSON_OUTPUT:
        raise MoltError(
            f"Unknown --output format {output!r}. `molt status --output json` prints the release "
            "plan to stdout; it is the only accepted value, because `status` writes no files."
        )
    sys.stdout.write(json.dumps(plan_payload(view), indent=2) + "\n")
    sys.stdout.flush()


# ======================================================================================
# The CI gate (research doc 03 section 5.2)
# ======================================================================================


def _changed_versionable_packages(
    workspace: Any,
    config: Any,
    *,
    since: str | None,
    git: Any,
    root: Path,
    resolution: Any = None,
) -> list[str]:
    """The packages that changed since the comparison ref **and** that this configuration releases.

    Asked unconditionally, even when a changeset is already pending. Skipping the query in that
    case would be free, and would also make the comparison ref unobservable in every row that has a
    changeset -- which is most of them, and is exactly the blind spot design D4 was written about.
    Upstream asks both questions in parallel and pays for the diff either way.

    The ref is ``--since`` when given and the configured ``base_branch`` otherwise
    (``cli/status.md``: "``--since <ref>`` ... default: base branch"). Those two must stay
    independently observable: a fixture that gives them the same value cannot tell an
    implementation that reads the flag from one that hardcodes ``main``.
    """
    from molt.engine import is_versionable
    from molt.git import Git

    client = Git(root) if git is None else git
    changed = client.get_changed_packages_since_ref(
        since if since is not None else config.base_branch,
        changed_file_patterns=list(config.changed_file_patterns),
        ecosystem=config.ecosystem,
    )
    return [
        name
        for name in changed
        if _versionable(workspace, config, name, is_versionable, resolution)
    ]


def _versionable(
    workspace: Any, config: Any, name: str, is_versionable: Any, resolution: Any = None
) -> bool:
    """Whether the changed package ``name`` is one this configuration would release.

    ``Workspace.get`` is PEP 503-aware, so a name spelled differently by discovery and by
    configuration still resolves to one package. A name discovery cannot resolve is treated as
    versionable: it changed, molt cannot prove it is exempt, and the gate's job is to fail loudly.

    ``resolution`` is what makes a file-sourced package count as versionable here: without it the
    gate would report "no changeset for pkg-a" and then ``molt version`` would refuse to release
    pkg-a, which is the worst of both answers.
    """
    package = workspace.get(name)
    return True if package is None else is_versionable(package, config, resolution)
