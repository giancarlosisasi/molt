"""`molt build` -- Build sdists and wheels for the distributions a release covers.

Ports ``packages/cli/src/commands/pack/index.ts`` @ v3.0.0-next.9. The command is ``build`` and the
library it calls is :func:`molt.pack.pack`; the names differ deliberately, because ``molt.build``
would read as a build backend sitting next to ``python -m build`` (design D7). There is **no**
``pack`` alias -- ``tests/cli/test_cli.py::test_pack_is_not_a_command`` pins that. Website doc:
``website/docs/cli/pack.md``. The conformance suite is ``tests/publish/test_pack.py``.

``--from-publish-plan`` is what makes the pipeline splittable across CI jobs: compute the plan once,
build from it on a machine with a toolchain, upload from a machine with credentials. Without it the
command computes the plan itself, which means querying the index.

A build failure surfaces and **writes no plan** (design D8): a plan claiming artifacts that do not
exist would fail partway through an irreversible upload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["DEFAULT_OUT_DIR", "run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.publish.plan import Plan

#: Where artifacts and the enriched ``publish-plan.json`` land when ``--out-dir`` is not given
#: (``website/docs/cli/pack.md``). ``dist`` is what every Python build tool already writes to.
DEFAULT_OUT_DIR = "dist"


def run(
    *,
    cwd: Path | None = None,
    out_dir: Path | str | None = None,
    from_publish_plan: Path | str | None = None,
    dry_run: bool = False,
    console: Any = None,
    builder: Any = None,
    **options: Any,
) -> Plan:
    """Build the distributions the plan covers and write the enriched plan into ``out_dir``.

    ``--dry-run`` reports what would be built and builds nothing, so it never writes a plan either.

    Every heavy import happens inside this function: ``tests/cli/test_cli.py``'s import-light
    assertion lists ``molt.publish`` among the modules ``import molt.cli`` must not pull in, and
    :mod:`molt.pack` imports it.
    """
    del options  # `--non-interactive` is the shell's global; `build` never prompts.

    from pathlib import Path as _Path

    from molt.ecosystem import find_workspace_root
    from molt.pack import pack
    from molt.publish import build_publish_plan, read_publish_plan

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    root = find_workspace_root(_Path(cwd) if cwd is not None else _Path.cwd())
    destination = _Path(out_dir) if out_dir is not None else root / DEFAULT_OUT_DIR

    if dry_run:
        plan = (
            read_publish_plan(from_publish_plan)
            if from_publish_plan is not None
            else build_publish_plan(cwd=root, console=console, git=None)
        )
        console.info(_render_dry_run(plan, destination))
        return plan

    return pack(
        cwd=root,
        out_dir=destination,
        from_publish_plan=from_publish_plan,
        console=console,
        builder=builder,
    )


def _render_dry_run(plan: Plan, out_dir: Path) -> str:
    """What ``--dry-run`` reports: the releases that would be built, and where they would land."""
    releases = [entry for chunk in plan for entry in chunk if entry.get("kind") == "publish"]
    if not releases:
        return "Nothing to build: the publish plan lists no publish releases."
    lines = [f"Would build into {out_dir}:"]
    lines.extend(f"  - {entry['name']} {entry['version']}" for entry in releases)
    return "\n".join(lines)
