"""Applying a release plan to a workspace.

:func:`apply_release_plan` is the orchestration: it decides which edits to make, assembles every
changelog entry, and then writes the whole set at once -- or writes none of it. The manifest-editing
primitives it composes (:func:`edit_toml`, :func:`set_dependency_specifier`) live beside it and are
the only things that touch a ``pyproject.toml``'s bytes.

Note that :func:`~molt.apply.edit_toml.edit_toml` is both a module name and a function name this
package re-exports, so ``molt.apply.edit_toml`` resolves to the **function**. That is deliberate --
the function is the module's headline export, and the test contract imports both primitives from the
package root.
"""

from __future__ import annotations

from molt.apply.apply import (
    CHANGELOG_ESCAPE_LINES,
    SIDE_EFFECT_ORDER,
    apply_release_plan,
)
from molt.apply.edit_toml import edit_toml, set_dependency_specifier

__all__ = [
    "CHANGELOG_ESCAPE_LINES",
    "SIDE_EFFECT_ORDER",
    "apply_release_plan",
    "edit_toml",
    "set_dependency_specifier",
]
