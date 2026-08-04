"""One module per check group. Adding a check is adding an object to :mod:`molt.doctor.registry`.

Nothing here reports; every module turns what an already-shipped seam returns into rows. That is
the whole of ``doctor``'s design: the diagnoses were always computed, and until now they were
discarded unless a command happened to fail on one.
"""

from __future__ import annotations

from molt.doctor.checks.changesets import DirectoryCheck, PendingCheck
from molt.doctor.checks.configuration import ParseCheck, SourceCheck
from molt.doctor.checks.environment import EncodingCheck, RuntimeCheck
from molt.doctor.checks.groups import FiltersCheck
from molt.doctor.checks.publishing import CredentialsCheck, IndexCheck, ToolsCheck
from molt.doctor.checks.versions import ResolvableCheck
from molt.doctor.checks.workspace import PackagesCheck, RootCheck

__all__ = [
    "CredentialsCheck",
    "DirectoryCheck",
    "EncodingCheck",
    "FiltersCheck",
    "IndexCheck",
    "PackagesCheck",
    "ParseCheck",
    "PendingCheck",
    "ResolvableCheck",
    "RootCheck",
    "RuntimeCheck",
    "SourceCheck",
    "ToolsCheck",
]
