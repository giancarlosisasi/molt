"""PEP 503 distribution-name normalization.

``Foo_Bar``, ``foo-bar`` and ``Foo.Bar`` are one distribution on PyPI. npm has no analogue -- its
names are already case-restricted and carry no punctuation equivalence -- so this is net-new surface
rather than a port (research README section 4.5; research doc 02 section 12.5).

It lives at the package root, not inside ``molt.config`` or ``molt.ecosystem``, because *both* sides
of every name comparison have to go through it and neither subpackage may depend on the other for
something this small. Design D5 of ``openspec/changes/implement-config-and-discovery/design.md``
states the rule the placement enforces: normalize once, at the boundary, so no downstream comparison
site has to remember to.

Standard library only, deliberately: this is imported on paths that must not pay for pydantic.
"""

from __future__ import annotations

import re

__all__ = ["normalize_name"]

#: PEP 503: "the name should be lowercased and all runs of ``-``, ``_`` and ``.`` replaced with a
#: single ``-``". Verbatim from the spec's reference implementation.
_SEPARATOR_RUN = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return _SEPARATOR_RUN.sub("-", name).lower()
