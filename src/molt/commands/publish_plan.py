"""`molt publish-plan` -- Emit the machine-readable plan of what `publish` would upload.

Ports ``packages/cli/src/commands/publish-plan/index.ts`` @ v3.0.0-next.9, the thin wrapper over
``getPublishPlan``. Everything that decides anything lives in :mod:`molt.publish.plan`; this module
is the shell over it -- resolve the cwd, supply the git seam, report the plan, write the envelope.
Website doc: ``website/docs/cli/publish.md`` ("``molt publish-plan``"). The conformance suite for
the computation is ``tests/publish/test_plan.py``.

**Nothing here uploads, and nothing here builds.** That separation is the point: PyPI is immutable,
so every decision that can be made before the irreversible step is made in this command, written to
a versioned document, and handed to a later, credentialed job unchanged.

The plan is returned as well as printed, so a caller inside molt gets the same value the file would
have carried.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.publish.plan import Plan

#: Printed when the plan is empty, so "nothing to publish" cannot be mistaken for a crash. Same
#: reasoning as ``molt status``'s header, which prints even with an empty body.
_NOTHING = "No packages to publish or tag."


def run(
    *,
    cwd: Path | None = None,
    filter: list[str] | None = None,
    repository: str | None = None,
    index_url: str | None = None,
    output: str | None = None,
    console: Any = None,
    git: Any = None,
    **options: Any,
) -> Plan:
    """Compute the publish plan, report it, and optionally write the versioned envelope.

    ``--repository`` and ``--index-url`` are two spellings of one question -- which index is this
    release aimed at -- so an explicit ``--index-url`` wins and a bare ``--repository`` is used
    otherwise. Naming any index other than the public PyPI clears the snapshot guardrail and stops
    molt querying pypi.org behind the user's back.

    ``--output`` is a **filename**, not a format (upstream's ``publish-plan --output <file>``). The
    envelope is written even when there is nothing to publish, because the CI job downstream reads
    that file unconditionally.

    ``filter`` shadows the builtin because the shell passes one keyword per long flag and the flag
    is ``--filter``; nothing in this module needs the builtin.

    Every heavy import happens inside this function: ``tests/cli/test_cli.py``'s import-light
    assertion lists ``molt.publish`` among the modules ``import molt.cli`` must not pull in.
    """
    del options  # `--non-interactive` is the shell's global; `publish-plan` never prompts.

    from pathlib import Path as _Path

    from molt.ecosystem import find_workspace_root
    from molt.publish import build_publish_plan

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(_Path(cwd) if cwd is not None else _Path.cwd())
    if git is None:
        from molt.git import Git

        git = Git(root)

    plan = build_publish_plan(
        cwd=root,
        console=console,
        git=git,
        repository=index_url or repository,
        output=_Path(output) if output else None,
        filter=filter,
    )
    console.info(_render(plan))
    if output:
        console.success(f"Wrote the publish plan to {output}.")
    return plan


def _render(plan: Plan) -> str:
    """The human view: one line per chunk, naming what publishes and what is only tagged.

    Chunk boundaries are shown rather than flattened away, because they *are* the publish order --
    a reader checking a release wants to see that a dependency goes up before the release that pins
    it, and a flat list cannot show that.
    """
    if not plan:
        return _NOTHING
    lines = ["Publish plan:"]
    for index, chunk in enumerate(plan, start=1):
        lines.append(f"- chunk {index}")
        for entry in chunk:
            suffix = "" if entry["kind"] == "publish" else "  (tag only, never uploaded)"
            lines.append(f"  - {entry['name']} {entry['version']}{suffix}")
    return "\n".join(lines)
