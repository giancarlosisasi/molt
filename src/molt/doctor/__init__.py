"""`molt doctor` -- is this workspace correctly set up, and which packages will molt release?

molt-native; changesets has no equivalent command, so nothing here ports. The relevant upstream fact
is the *absence*: changesets diagnoses a configuration by failing inside ``version``, which research
doc 06 records as a recurring complaint. molt's layers were built with the opposite shape --
:func:`molt.config.load_config` never raises and returns errors and warnings together,
:func:`molt.ecosystem.resolve_workspace_versions` never raises and returns three buckets -- and both
were designed for a caller that reports rather than executes. This package is that caller.

Read in this order:

- :mod:`~molt.doctor.report` -- what a finding is, and why the row model has the fields it has;
- :mod:`~molt.doctor.protocol` -- the check seam and what every check is handed;
- :mod:`~molt.doctor.runner` -- the only place that catches, and why;
- :mod:`~molt.doctor.registry` -- which checks run, in which order;
- :mod:`~molt.doctor.render` -- the human report and the machine-readable document, from one value.

Three promises this package keeps, each of them structural rather than a convention:

**It writes nothing.** No file, no git mutation, no upload. Every run is already a dry run, which is
why the command declares no ``--dry-run``.

**It never dies on the input it was invoked to diagnose.** A check that raises becomes one failed
row naming itself and the rest still run.

**It never emits a secret.** The credential check reads a name and a boolean, never a value, and
:class:`~molt.doctor.report.Row` has no field a value could travel in.
"""

from __future__ import annotations

from molt.doctor.protocol import Check, CheckBase, DoctorContext
from molt.doctor.registry import CHECKS
from molt.doctor.render import render_report, report_payload, summary_line
from molt.doctor.report import CheckGroup, CheckStatus, DoctorReport, Row
from molt.doctor.runner import build_context, run_checks

__all__ = [
    "CHECKS",
    "Check",
    "CheckBase",
    "CheckGroup",
    "CheckStatus",
    "DoctorContext",
    "DoctorReport",
    "Row",
    "build_context",
    "render_report",
    "report_payload",
    "run_checks",
    "summary_line",
]
