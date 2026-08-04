"""`molt doctor` -- Read-only diagnosis of a workspace's molt setup.

molt-native; there is nothing to port. Everything the command knows lives in :mod:`molt.doctor`;
this module is the shell over it. Website docs: ``website/docs/cli/doctor.md``. The conformance
suite is ``tests/cli/test_doctor.py``.

Two contracts this command owns
-------------------------------
**The stream contract** (unchanged from every other command). The rendered report leaves through
the :mod:`molt.ui.console` seam, which writes to **stderr**. The ``--output json`` document is the
only thing written to **stdout**, so ``molt doctor --output json | jq`` reads exactly one document.

**Every heavy import happens inside** :func:`run`. ``doctor`` reads the configuration layer, the
ecosystem backend and the changeset store, which is close to everything molt can import; loading any
of it at module scope would defeat the import-light contract for every *other* command, since
``molt.cli`` would pay for it on ``molt --help``.

Unlike every other verb, ``doctor`` does not refuse an uninitialized project. A missing
``.changeset/`` is a check row here rather than a refusal, because "my project is not set up" is the
exact condition a user runs this command to diagnose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["JSON_OUTPUT", "run"]

if TYPE_CHECKING:
    from pathlib import Path

    from molt.doctor import DoctorReport

#: The one ``--output`` value ``doctor`` accepts. A filename is refused rather than written to:
#: writing a file is exactly what this command promises it does not do.
JSON_OUTPUT = "json"

_CLEAN = "Everything checks out."
_FAILED = "molt doctor found problems. Fix the FAIL rows above."
_WARNED = "molt doctor found nothing that would stop a release."


def run(
    *,
    cwd: Path | None = None,
    online: bool = False,
    output: str | None = None,
    console: Any = None,
    **options: Any,
) -> DoctorReport:
    """Run every check over the workspace containing ``cwd`` and report what was found.

    Returns the :class:`~molt.doctor.DoctorReport` -- the same value ``--output json`` prints, so a
    caller receives what a consumer would parse. Raises :class:`~molt.errors.ExitError` with code 1
    when at least one check failed, **after** the report has already been produced: the run a CI job
    most wants output from is the failing one.

    ``console`` is an injectable seam, defaulting to the module-level
    :data:`molt.ui.console.console`.
    """
    del options  # `--non-interactive` is the shell's global; `doctor` never prompts.

    from pathlib import Path

    from molt.doctor import build_context, render_report, run_checks, summary_line
    from molt.errors import ExitError

    if console is None:
        from molt.ui.console import console as console_

        console = console_

    report = run_checks(build_context(Path(cwd) if cwd is not None else Path.cwd(), online=online))

    if output is None:
        console.info(render_report(report))
    else:
        _emit_json(report, output)

    _report_outcome(report, console=console, summary=summary_line(report))
    if report.failed:
        raise ExitError(1)
    return report


def _report_outcome(report: DoctorReport, *, console: Any, summary: str) -> None:
    """Close with one line at the level the outcome deserves.

    The rows themselves are one block at ``info`` so the report stays a contiguous artifact somebody
    can paste. This line is what makes a failing run *look* failing without breaking that block up.
    """
    from molt.doctor import CheckStatus

    if report.failed:
        console.error(f"{summary} {_FAILED}")
    elif report.counts[CheckStatus.WARN]:
        console.warn(f"{summary} {_WARNED}")
    else:
        console.success(f"{summary} {_CLEAN}")


def _emit_json(report: DoctorReport, output: str) -> None:
    """Write the report to stdout as JSON, and nothing else to stdout.

    ``sys.stdout`` is resolved at call time, never cached: a caller (or a test's capture fixture)
    may have replaced it since import.
    """
    import json
    import sys

    from molt.doctor import report_payload
    from molt.errors import MoltError

    if output.strip().lower() != JSON_OUTPUT:
        raise MoltError(
            f"Unknown --output format {output!r}. `molt doctor --output json` prints the report "
            "to stdout; it is the only accepted value, because `doctor` writes no files."
        )
    sys.stdout.write(json.dumps(report_payload(report), indent=2) + "\n")
    sys.stdout.flush()
