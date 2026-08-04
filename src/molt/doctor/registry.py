"""The checks molt runs, in the order they are reported.

Order matters twice. It is the order the human report reads in -- environment first because it is
the cheapest context, publishing last because it is the furthest from "does my project even load"
-- and it is the order ``requires`` resolves in: a check must appear after everything it names, or
its blocker has not run yet and the dependency silently does nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from molt.doctor.checks import (
    CredentialsCheck,
    DirectoryCheck,
    EncodingCheck,
    FiltersCheck,
    IndexCheck,
    PackagesCheck,
    ParseCheck,
    PendingCheck,
    ResolvableCheck,
    RootCheck,
    RuntimeCheck,
    SourceCheck,
    ToolsCheck,
)

if TYPE_CHECKING:
    from molt.doctor.protocol import Check

__all__ = ["CHECKS"]

CHECKS: Final[tuple[Check, ...]] = (
    RuntimeCheck(),
    EncodingCheck(),
    RootCheck(),
    PackagesCheck(),
    SourceCheck(),
    ParseCheck(),
    ResolvableCheck(),
    DirectoryCheck(),
    PendingCheck(),
    FiltersCheck(),
    ToolsCheck(),
    CredentialsCheck(),
    IndexCheck(),
)
