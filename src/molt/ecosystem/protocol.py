"""The swappable package-ecosystem seam: data types plus the backend ``Protocol``.

This is the capability changesets formally rejected in 2020
(`#310 <https://github.com/changesets/changesets/issues/310>`_) and deferred again in June 2026 on
OpenAI's PR #2124 -- research README section 2 and section 5 item 1. There is **no upstream
counterpart**: changesets delegates the whole question to ``@manypkg/get-packages``, which knows
only about JavaScript package managers. Designing the seam on day one instead of retrofitting it is
a core reason molt exists.

**No member here may name a tool** (design D9). A protocol with a ``uv_members()`` method is not a
seam, it is uv with extra steps; a second backend has to be able to satisfy this surface without
adapting anything. Everything uv-specific lives in :mod:`molt.ecosystem.uv`.

The shape mirrors ``@manypkg``'s ``{ rootDir, rootPackage?, packages[], tool }`` (research doc 02
section 12.5, recommendation 4) so that downstream ports stay mechanical, with the Python-specific
fields the port actually needs: PEP 508 requirement strings rather than a name -> range map, a
normalized name alongside the declared one, and the ``Private :: Do Not Upload`` classifier standing
in for npm's ``"private": true``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from molt.names import normalize_name

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "PRIVATE_CLASSIFIER",
    "EcosystemBackend",
    "Package",
    "Workspace",
]

#: The Python analogue of npm's ``"private": true``. A distribution carrying this classifier is
#: rejected by PyPI, which is what makes it the honest marker for "never uploaded" (research doc 02
#: section 12.1). Matched case-insensitively on the normalized-whitespace form.
PRIVATE_CLASSIFIER = "private :: do not upload"


@dataclass(frozen=True)
class Package:
    """One discovered distribution.

    ``name`` is the spelling the manifest declares and is what molt displays and writes back;
    ``normalized_name`` is the PEP 503 form every comparison goes through (design D5). Keeping both
    is research doc 02 section 12.4's instruction to "preserve the original spelling for display".

    ``version`` is ``None`` for a dynamically-versioned distribution (``dynamic = ["version"]``).
    Upstream's ``shouldSkipPackage`` treats a missing version as "skip" silently; for Python that
    would quietly drop half a repo, so the absence is represented rather than collapsed and the
    version-source abstraction that fills it in is a later change.

    ``workspace_sources`` carries the **normalized** form of the tool-specific source table that
    redirects a dependency at another member of this workspace -- ``[tool.uv.sources]`` for uv.
    Each pair is ``(declared name, marker)`` where the marker is the engine's spelling:
    ``workspace:*`` for a bare workspace redirect and ``workspace:<relpath>`` for one written as a
    path into the workspace. Normalizing here rather than in the engine is what keeps the marker
    tool-neutral (design D9): a second backend produces the same two spellings from whatever table
    it reads, and :mod:`molt.engine.graph` never learns the word "uv". A pair list rather than a
    mapping so the record stays hashable, like every other field of this frozen value.
    """

    name: str
    directory: Path
    manifest_path: Path
    version: str | None = None
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    dev_dependencies: tuple[str, ...] = ()
    private: bool = False
    workspace_sources: tuple[tuple[str, str], ...] = ()

    @property
    def normalized_name(self) -> str:
        """The PEP 503 form of :attr:`name`; the only form comparisons may use."""
        return normalize_name(self.name)


@dataclass(frozen=True)
class Workspace:
    """Everything discovery knows about a repository.

    ``backend`` names the implementation that produced this, mirroring ``@manypkg``'s ``tool``
    field: ``"uv"`` for a uv workspace, ``"single"`` for a repository with no workspace declaration
    (design D8 -- that is a first-class outcome, not a failure).
    """

    root: Path
    packages: tuple[Package, ...]
    backend: str
    root_package: Package | None = None
    _by_normalized_name: dict[str, Package] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        index = {package.normalized_name: package for package in self.packages}
        object.__setattr__(self, "_by_normalized_name", index)

    @property
    def names(self) -> tuple[str, ...]:
        """Declared package names, discovery order -- what config globs expand against."""
        return tuple(package.name for package in self.packages)

    def get(self, name: str) -> Package | None:
        """Look a package up by any PEP 503-equivalent spelling of its name."""
        return self._by_normalized_name.get(normalize_name(name))

    def package_for_path(self, path: Path) -> Package | None:
        """The **closest** enclosing package of ``path``, or ``None``.

        Deepest-directory-wins, so a package nested inside another owns its own files and they are
        never counted twice. Upstream reaches the same result by probing longest-dir-first
        (``git/src/index.ts:284-297``); the rule is stated here rather than at each call site
        because "which package does this file belong to" must have exactly one answer.
        """
        try:
            candidate = path.resolve()
        except OSError:  # pragma: no cover - unreadable path, treat as unattributable
            return None
        best: Package | None = None
        best_depth = -1
        for package in self.packages:
            directory = package.directory.resolve()
            if candidate == directory or directory in candidate.parents:
                depth = len(directory.parts)
                if depth > best_depth:
                    best, best_depth = package, depth
        return best


@runtime_checkable
class EcosystemBackend(Protocol):
    """What a packaging tool must implement to be a molt ecosystem backend.

    Two responsibilities only -- deciding whether a repository is one of ours, and turning it into
    a :class:`Workspace`. The dependency **graph** built on top of the result is
    ``molt.engine``'s (change 06), and version *writing* is the apply layer's; keeping both out is
    what stops this protocol from growing into an interface only uv can satisfy.
    """

    #: Stable identifier, matching the ``ecosystem`` config option's value for this backend.
    name: str

    def detect(self, root: Path) -> bool:
        """Whether ``root`` is a workspace this backend owns. Drives ``ecosystem = "auto"``."""
        ...

    def discover(self, root: Path) -> Workspace:
        """Enumerate every package in the workspace rooted at ``root``."""
        ...

    def read_manifest(self, manifest_path: Path) -> Package:
        """Read one member manifest into a :class:`Package`."""
        ...
