"""Engine-local fixtures. Shared, product-code-free fixtures live in ``tests/conftest.py``."""

from __future__ import annotations

import pytest
from tests.engine.fake_state import FakeConfig
from tests.engine.fake_state import default_config as fake_state_default_config


@pytest.fixture
def default_config() -> FakeConfig:
    """The ported changesets ``defaultConfig``, in molt's key names.

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

    :class:`~tests.engine.fake_state.FakeConfig` is frozen, so a test derives a variant instead of
    mutating this one::

        config = dataclasses.replace(default_config, ignore=("pkg-b",))
        config = dataclasses.replace(default_config, fixed=(("pkg-a", "pkg-b"),))

    It is a stand-in for the unbuilt ``molt.config.Config`` (build step 3); when that lands this
    fixture should return ``molt.config.default_config()`` unchanged in shape.
    """
    return fake_state_default_config()
