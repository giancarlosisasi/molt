"""Running the checks, and building what they read.

**This module is the only place in :mod:`molt.doctor` that catches.** A check that raises becomes
one failed row naming itself and the remaining checks still run, so the number of rows a report
contains is independent of how many checks broke -- a report can never be silently short. A
diagnostic that dies on the broken input it was invoked to diagnose is worse than no diagnostic.

``KeyboardInterrupt`` and ``SystemExit`` derive from ``BaseException`` and therefore pass straight
through the ``except Exception`` below. That is deliberate and load-bearing: Ctrl-C must still
cancel, and the shell's exit-code contract (Ctrl-C exits 0 with "Canceled") must still apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from molt.doctor.protocol import DoctorContext
from molt.doctor.registry import CHECKS
from molt.doctor.report import CheckStatus, DoctorReport, Row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from molt.doctor.protocol import Check

__all__ = ["build_context", "run_checks"]

_MANIFEST_NAME = "pyproject.toml"


def build_context(cwd: Path, *, online: bool = False) -> DoctorContext:
    """Resolve the workspace root, the configuration and the package list, reporting nothing.

    Every failure this can meet becomes a field rather than an exception, because the checks are
    what turn a failure into a row -- and a context builder that raised would take the whole report
    down with it, which is the one thing ``doctor`` may not do.
    """
    from molt.config import load_config
    from molt.ecosystem import AUTO, discover_workspace, find_workspace_root
    from molt.errors import MoltError, MoltParseError

    root = find_workspace_root(cwd)
    config = load_config(root)
    ecosystem = config.config.ecosystem if config.config is not None else AUTO

    workspace = None
    workspace_error = None
    try:
        workspace = discover_workspace(root, ecosystem)
    except MoltParseError as error:
        # `read_manifest` re-raises a decode failure carrying the path, which is the whole reason
        # that primitive exists: `tomllib`'s own error names neither the file nor the project.
        located = f"{error.path}: " if error.path else ""
        workspace_error = f"{located}{error}"
    except MoltError as error:
        workspace_error = str(error)

    return DoctorContext(
        cwd=cwd,
        root=root,
        config=config,
        config_path=_config_source(root),
        workspace=workspace,
        workspace_error=workspace_error,
        online=online,
    )


def run_checks(ctx: DoctorContext, checks: Sequence[Check] = CHECKS) -> DoctorReport:
    """Run every check in order and collect its rows into one report."""
    blocked: set[str] = set()
    rows: list[Row] = []

    for check in checks:
        blocker = next((required for required in check.requires if required in blocked), None)
        if blocker is not None:
            rows.append(_not_applicable(check, blocker))
            # A check nobody could run cannot vouch for its dependants either, so the block
            # propagates rather than letting the next layer re-fail on the same missing input.
            blocked.add(check.id)
            continue

        try:
            produced = list(check.run(ctx))
        # A blanket catch is this loop's entire job -- see the module docstring for what it must
        # NOT catch, and why that follows from `BaseException` rather than from a second clause.
        except Exception as error:
            produced = [_crashed(check, error)]

        if any(row.status is CheckStatus.FAIL for row in produced):
            blocked.add(check.id)
        rows.extend(produced)

    return DoctorReport(rows=tuple(rows))


def _not_applicable(check: Check, blocker: str) -> Row:
    """A check whose input never arrived.

    A **warning**, not a failure: the thing that is actually wrong already failed and already set
    the exit code, and reporting the consequence a second time as a failure would make one broken
    manifest read as five separate problems.
    """
    return Row(
        group=check.group,
        check=check.id,
        status=CheckStatus.WARN,
        subject=check.id,
        message=f"Not checked: `{blocker}` failed, so there was nothing to check against.",
        remedy=f"Fix the `{blocker}` finding above, then run `molt doctor` again.",
    )


def _crashed(check: Check, error: Exception) -> Row:
    """A check that raised. One row, naming the check, so the report cannot come up short."""
    return Row(
        group=check.group,
        check=check.id,
        status=CheckStatus.FAIL,
        subject=check.id,
        message=f"The check itself failed: {type(error).__name__}: {error}",
        remedy="This is a bug in molt rather than a problem with your project; please report it.",
    )


def _config_source(root: Path) -> Path | None:
    """Which file molt's configuration was read from, or ``None`` when there is none.

    ``load_config`` resolves the same two candidates but returns only the parsed result, and the
    report needs to name the file a user must open. A manifest molt cannot parse resolves to
    ``None``: the configuration check reports the parse failure, and pointing at a file whose
    contents were never read would be a guess.
    """
    from molt.config import JSON_CONFIG_PATH
    from molt.ecosystem import read_toml
    from molt.errors import MoltError

    json_path = root / JSON_CONFIG_PATH
    if json_path.is_file():
        return json_path

    manifest = root / _MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        tool = read_toml(manifest).get("tool")
    except MoltError:
        return None
    return manifest if isinstance(tool, dict) and isinstance(tool.get("molt"), dict) else None
