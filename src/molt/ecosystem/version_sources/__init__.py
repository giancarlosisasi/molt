"""Where a package's version lives -- the ecosystem seam's missing half.

:mod:`molt.ecosystem` answers *which packages exist*; this package answers *where each one's
version lives*, how molt reads it, how molt writes a new one back, and what happens when molt
cannot. Research doc 02 section 12.5 names the abstraction "the single largest piece of new design"
in the port and research README section 5 item 1 lists it as differentiator number 1 -- the
capability changesets rejected in 2020 (#310) and deferred again in June 2026 (#2124).

Read :mod:`~molt.ecosystem.version_sources.protocol` for the seam, ``resolve`` for the order the
sources are chosen in, and ``detect`` for the tool tables molt reads. Nothing here runs a build,
installs anything, executes project code or opens a socket (design D2).
"""

from __future__ import annotations

from molt.ecosystem.version_sources.declaration import (
    VersionSourceOptions,
    parse_version_source_options,
)
from molt.ecosystem.version_sources.detect import detect_version_source
from molt.ecosystem.version_sources.file import DEFAULT_VERSION_PATTERN
from molt.ecosystem.version_sources.protocol import (
    ResolvedVersion,
    UnresolvedVersion,
    VersionResolution,
    VersionSource,
    VersionWrite,
)
from molt.ecosystem.version_sources.resolve import (
    VERSION_SOURCE_GROUP,
    load_version_source_builder,
    resolve_version_source,
    resolve_workspace_versions,
)

__all__ = [
    "DEFAULT_VERSION_PATTERN",
    "VERSION_SOURCE_GROUP",
    "ResolvedVersion",
    "UnresolvedVersion",
    "VersionResolution",
    "VersionSource",
    "VersionSourceOptions",
    "VersionWrite",
    "detect_version_source",
    "load_version_source_builder",
    "parse_version_source_options",
    "resolve_version_source",
    "resolve_workspace_versions",
]
