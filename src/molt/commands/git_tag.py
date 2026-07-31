"""`molt git-tag` -- Create the git tags for the versions the last `version` run produced.

Ports ``packages/cli/src/commands/git-tag/index.ts`` @ v3.0.0-next.9 against the behaviour spec in
``roadmap/research/changesets-03-cli-and-ux.md`` section 4.6 (tag naming, annotated tags, the NDJSON
event) plus gotchas 10.12 and 10.19. Website doc: ``website/docs/cli/git-tag.md``. The conformance
suite is ``tests/cli/test_git_tag.py``.

``git-tag`` is deliberately a separate verb from ``molt version`` (which never tags -- see
``molt.commands.version``): tagging after a successful publish, in a different CI job, is exactly
the workflow that requires them to be independent.

Four rules this command owns
-----------------------------
**Tag naming branches on project shape, not package count** (design D2). A single-package project
(``workspace.backend == "single"``) uses a ``v<version>`` tag; a workspace uses
``<name>@<version>``, with the name **PEP 503 normalized** (design D3) -- a tag is a durable public
identifier, so ``Foo_Bar`` and ``foo-bar`` must produce the same one.

**The workspace root is a container, not a release** (design D4). ``molt.ecosystem.uv.UvBackend``
deliberately makes the workspace root a member of ``workspace.packages`` (unlike upstream's
``@manypkg``, which keeps it out) so other commands can version and publish it. ``git-tag`` undoes
that for its own purposes: in a workspace, ``workspace.root_package`` is never tagged, independent
of its privacy -- it is the workspace declaration, not a release. In a single-package project the
root *is* the only possible release, so there privacy is what decides: a private root is skipped,
exactly as upstream's (now-dropped) ``privatePackages.tag`` did by default. There is no
configuration for either rule; ``tag`` is in ``DROPPED_KEYS`` (``tests/config/test_parse.py``).

**Existing tags are looked up once, in memory** (design D1). ``git.get_all_tags()`` is called
exactly once regardless of workspace size; membership is a set lookup, not a subprocess per package
(``tests/cli/test_git_tag.py::test_existing_tag_lookup_is_batched``).

**The NDJSON stream is unconditional** (design D5). The output file -- when ``--output`` is given
-- is written whether or not there is anything to tag, so a consumer can tell "ran and found
nothing" from "did not run". Keys are snake_case (design D6): ``{"type": "git-tag", "tag": ...,
"package_name": ...}``, not upstream's ``packageName`` (``utils/output.ts:6-10``).

``--dry-run`` reports the same plan it would otherwise create -- research README section 5 item 3's
"plan, print, do not execute" -- and still writes the output stream (design D7): a dry run is a
faithful preview, not an approximation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

__all__ = ["run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.ecosystem import Package, Workspace


@dataclass(frozen=True)
class _TagItem:
    """One tag this run would create: its name, and the package it belongs to."""

    tag: str
    package_name: str

    def event(self) -> dict[str, Any]:
        """The NDJSON event for this tag, snake_case keys (design D6)."""
        return {"type": "git-tag", "tag": self.tag, "package_name": self.package_name}


def run(
    *,
    cwd: Path | None = None,
    output: str | Path | None = None,
    dry_run: bool = False,
    console: Any = None,
    git: Any = None,
    **options: Any,
) -> list[dict[str, Any]]:
    """Tag every taggable workspace package, and return the events that describe what happened.

    Returns the list of NDJSON event dicts -- the same value written to ``output``, if given -- so a
    caller can inspect the outcome without re-reading the file. Skips packages whose tag already
    exists (idempotent re-runs) and packages with no known version (``dynamic = ["version"]``; the
    version-source abstraction that would resolve one is a later change -- CLAUDE.md's shared-
    primitives table).

    ``console`` and ``git`` are injectable seams. ``console`` defaults to the module-level
    :data:`molt.ui.console.console`; ``git`` defaults to a :class:`molt.git.Git` bound to the
    workspace root.

    Every heavy import happens here rather than at module scope -- ``tests/cli/test_cli.py``'s
    import-light assertion pins that for every ``molt.commands.*`` module (design D7 of
    ``adopt-typer-cli-shell``).
    """
    del options  # `--non-interactive` is the shell's global; `git-tag` never prompts.

    from pathlib import Path as _Path

    from molt.ecosystem import discover_workspace, find_workspace_root

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(_Path(cwd) if cwd is not None else _Path.cwd())
    config = _resolve_config(root, console=console)
    workspace = discover_workspace(root, config.ecosystem)

    if git is None:
        from molt.git import Git

        git = Git(root)

    plan = _plan(workspace)
    existing = git.get_all_tags()
    to_create = [item for item in plan if item.tag not in existing]

    if output is not None:
        _write_ndjson(output, to_create)

    if dry_run:
        for item in to_create:
            console.info(f"{item.tag}")
    else:
        for item in to_create:
            git.tag(item.tag, item.tag)
            console.success(f"New tag: {item.tag}")

    return [item.event() for item in to_create]


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
# The tag plan -- shape, naming, and the root-exclusion rule (design D2/D3/D4)
# ======================================================================================


def _plan(workspace: Workspace) -> list[_TagItem]:
    """Every package this run would tag, in workspace discovery order."""
    return [
        _TagItem(tag=_tag_name(package, workspace), package_name=package.name)
        for package in _taggable_packages(workspace)
    ]


def _taggable_packages(workspace: Workspace) -> list[Package]:
    """Workspace packages eligible for a tag: versioned, and not an excluded root (design D4).

    A package with no known version (``dynamic = ["version"]``) is skipped -- there is nothing yet
    to compose a tag name from. The workspace root is excluded unconditionally when the project is a
    genuine workspace (it is the workspace declaration, not a release); in a single-package project
    the root *is* the only candidate, so there only its own privacy excludes it.
    """
    root = workspace.root_package
    packages: list[Package] = []
    for package in workspace.packages:
        if package.version is None:
            continue
        is_root = root is not None and package.normalized_name == root.normalized_name
        if is_root:
            if workspace.backend != "single":
                continue
            if package.private:
                continue
        packages.append(package)
    return packages


def _tag_name(package: Package, workspace: Workspace) -> str:
    """The tag name for ``package``, shaped by the workspace's detected ecosystem (design D2/D3).

    A single-package project's tag carries no name at all -- there is only one candidate, so the
    version alone identifies the release. A workspace's tag names the package, PEP 503 normalized
    (``Foo_Bar`` and ``foo-bar`` collide on purpose): a tag is a durable, publicly visible
    identifier and must match the distribution name it was published under.
    """
    from molt.names import normalize_name

    if workspace.backend == "single":
        return f"v{package.version}"
    return f"{normalize_name(package.name)}@{package.version}"


# ======================================================================================
# The NDJSON event stream (design D5/D6)
# ======================================================================================


def _write_ndjson(path: str | Path, items: list[_TagItem]) -> None:
    """Write one NDJSON event per item, LF-terminated, created even when ``items`` is empty.

    An empty file (rather than no file) is the signal that the command ran and found nothing to do
    (design D5). Written as bytes so no platform newline translation can turn the LF-only stream a
    line-oriented consumer expects into CRLF.
    """
    from pathlib import Path as _Path

    target = _Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(item.event(), separators=(",", ":")) + "\n" for item in items)
    target.write_bytes(lines.encode("utf-8"))
