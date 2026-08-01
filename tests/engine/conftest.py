"""Engine-local fixtures. Shared, product-code-free fixtures live in ``tests/conftest.py``."""

from __future__ import annotations

import pytest

from molt.config import Config
from molt.config import default_config as real_default_config


@pytest.fixture
def default_config() -> Config:
    """The ported changesets ``defaultConfig`` -- molt's own :func:`molt.config.default_config`.

    Shape from ``packages/config/src/defaults.ts:7-18`` (the *written* defaults: ``baseBranch``,
    ``ignore``, ``fixed``, ``linked``, ``updateInternalDependencies``) plus the schema defaults
    ``normalizeWrittenConfig`` fills in from ``packages/config/src/config.ts`` --
    ``changedFilePatterns`` ``:57-60``, ``privatePackages`` ``:95-105``,
    ``bumpVersionsWithWorkspaceProtocolOnly`` ``:106-110``, ``snapshot.*`` ``:111-131`` and
    ``___experimentalUnsafeOptions.updateInternalDependents`` ``:148-156``. See research doc 01
    section 1 "Default config" and ``roadmap/research/test-suite/02-release-plan-engine.md``
    "Fixtures & tooling to build" item 2.

    Net: empty ``ignore`` / ``fixed`` / ``linked``, ``bump_workspace_sources_only`` off,
    ``update_internal_dependents="out-of-range"``, ``update_internal_dependencies="patch"``,
    snapshot template unset and ``snapshot_use_calculated_version`` off, private packages
    versioned.

    :class:`~molt.config.Config` is a frozen pydantic model, so a test derives a variant instead of
    mutating this one::

        config = default_config.model_copy(update={"ignore": ("pkg-b",)})
        config = default_config.model_copy(update={"fixed": (("pkg-a", "pkg-b"),)})

    **A nested option is derived by replacing its whole sub-model**, never by naming the flat view
    (owner ruling 2026-07-31, closing gap ``RPE-7``). ``snapshot_prerelease_template`` and
    ``snapshot_use_calculated_version`` are ``property`` objects -- data descriptors that shadow the
    instance ``__dict__`` entries ``model_copy(update=)`` writes -- so updating one by name is a
    silent no-op::

        config = default_config.model_copy(update={"snapshot": SnapshotOptions(...)})

    It used to be ``tests.engine.fake_state.FakeConfig``, a stand-in from before ``molt.config``
    existed; the retirement is what ``CLAUDE.md`` had instructed since build step 3.
    """
    return real_default_config()
