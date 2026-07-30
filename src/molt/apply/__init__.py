"""Applying a release plan to a workspace.

Today this package holds the manifest-editing primitives only. The orchestration that decides
*which* edits to make, writes them atomically, refreshes ``uv.lock`` and writes changelog files is
``apply_release_plan``, still owed (``openspec/changes/implement-apply-release-plan``).

Note that :func:`~molt.apply.edit_toml.edit_toml` is both the module name and the function name
this package re-exports, so ``molt.apply.edit_toml`` resolves to the **function**. That is
deliberate -- the function is the module's headline export, and the test contract imports both
primitives from the package root.
"""

from __future__ import annotations

from molt.apply.edit_toml import edit_toml, set_dependency_specifier

__all__ = ["edit_toml", "set_dependency_specifier"]
