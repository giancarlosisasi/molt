"""Is this version a PEP 440 prerelease? The flag a host release is published with.

Ported **with a deliberate divergence** from ``changesets/action`` v1.9.0
``src/run.ts::createRelease``, which marks a release with
``prerelease: pkg.packageJson.version.includes("-")``. That is npm semver's rule -- there a
prerelease is spelled ``1.0.0-rc.1`` -- and it is wrong for Python in **both** directions
(research README section 4, Python divergences):

* a PEP 440 prerelease carries **no hyphen** (``1.0.0rc1``, ``1.0.0a1``, ``1.0.0.dev1``, and every
  ``molt version --snapshot`` version, e.g. ``0.0.0.dev20260731120000``), so the literal port
  publishes every one of them as a full release -- which is what a user's subscriptions notify on;
* a local version (``1.0.0+local.build-1``) *does* carry one, so the literal port publishes it as
  a prerelease.

The predicate is ``packaging``'s own :attr:`~packaging.version.Version.is_prerelease`: true for
``a`` / ``b`` / ``rc`` **and** ``.devN``, false for a post-release. That is already the predicate
:mod:`molt.versioning.bump` relies on, with the reasoning written down at
``src/molt/versioning/bump.py:93-95`` -- it is exactly node-semver's ``prerelease.length !== 0``.

This lives in :mod:`molt.versioning` rather than in :mod:`molt.forge` on purpose: this package
*is* molt's PEP 440 layer, and a forge backend must never grow version semantics -- a GitLab
backend would otherwise have to re-implement the same rule (change design D4).
"""

from __future__ import annotations

from packaging.version import Version

__all__ = ["is_prerelease"]


def is_prerelease(version: str | Version) -> bool:
    """Whether ``version`` is an alpha, beta, release candidate or development release.

    A post-release, a local version and a plain release are all ``False``.

    ``str`` and :class:`~packaging.version.Version` are both accepted because both spellings are
    live at the call site: the release plan carries parsed versions, while a version read back
    from a manifest or a git tag is text.

    An unparseable version raises :class:`~packaging.version.InvalidVersion` rather than guessing.
    A version molt itself wrote always parses, so an unparseable one means the caller is passing
    something that is not a version at all -- and guessing would mark a published release wrongly
    and report nothing.
    """
    parsed = version if isinstance(version, Version) else Version(version)
    return parsed.is_prerelease
