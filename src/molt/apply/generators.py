"""Resolving the ``changelog`` config option to a changelog generator object.

Three written shapes, all documented in ``website/docs/extending/changelog-plugins.md`` under
"Choosing a generator", and one of them is a hard "do nothing":

===============================================  ===================================================
``changelog = false``                            No ``CHANGELOG.md`` is written at all
``changelog = "git"``                            A generator, named
``changelog = ["github", { repo = "a/b" }]``     A generator plus the options it is handed verbatim
===============================================  ===================================================

Resolution order for the name, most specific first:

1. an entry point in the ``molt.changelog`` group -- what makes a third-party generator a
   first-class peer of the defaults -- matched on the written name **or** on the written name with
   the ``molt.changelog.`` group prefix stripped, which is how ``molt.config``'s
   ``BUILTIN_CHANGELOG`` (``"molt.changelog.default"``) reaches the entry point registered as
   ``default``. Same shape ``molt.commit.load_provider`` uses for ``"molt.commit.default"``;
   the two defaults are spelled alike on purpose (gap ``AC-7``);
2. a **file path** (``./failing_changelog.py``), resolved against ``.changeset/`` and then the
   project root, so a repository can keep a one-off generator beside its changesets;
3. a **dotted module path** (``molt.changelog.github``), which is the spelling
   ``website/docs/config/options.md`` uses;
4. a bare name, as the module ``molt.changelog.<name>`` -- the built-in defaults.

A loaded module satisfies the seam either through a ``generator`` attribute or by exposing the two
functions at module level. Both are accepted because the docs describe a generator *object* while a
file-path generator is naturally written as a module, and forcing one shape on a plugin author buys
nothing.

Nothing here calls a generator. A generator failure has to escape *before any write*
(``index.ts:314-322``), which is :mod:`molt.apply.apply`'s ordering to enforce, not this module's.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from typing import TYPE_CHECKING, Any

from molt.errors import MoltError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["ResolvedGenerator", "resolve_generator"]

#: The entry-point group a distribution registers a changelog generator in.
_ENTRY_POINT_GROUP = "molt.changelog"

#: The package the built-in generators live in, used for the bare-name fallback.
_BUILTIN_PACKAGE = "molt.changelog"

#: The two methods a generator must offer (design D4 -- four parameters each).
_REQUIRED_METHODS = ("get_release_line", "get_dependency_release_line")


class ResolvedGenerator:
    """A generator plus the options table it was configured with.

    The options are carried alongside rather than bound into the generator, because they are pure
    pass-through: :func:`molt.changelog.get_changelog_entry` hands them to *both* methods on
    *every* call, and a generator that closed over them could not be shared between two
    configurations in one process.
    """

    __slots__ = ("generator", "options")

    def __init__(self, generator: Any, options: Mapping[str, object] | None) -> None:
        self.generator = generator
        self.options = options


def resolve_generator(
    setting: object, *, changeset_dir: Path, project_root: Path
) -> ResolvedGenerator | None:
    """Resolve ``config.changelog`` into a generator, or ``None`` when changelogs are off.

    ``None`` is returned for ``False`` and for an unset value, and the caller then writes no
    ``CHANGELOG.md`` and reports none as touched -- the short-circuit happens before the generator
    is even looked for (``index.ts:241-248``), so a project with changelogs disabled never pays for
    a broken plugin it is not using.

    Raises:
        MoltError: the setting names a generator that cannot be loaded, or one that does not
            satisfy the two-method seam.
    """
    name, options = _split(setting)
    if name is None:
        return None
    module = _load(name, changeset_dir=changeset_dir, project_root=project_root)
    generator = getattr(module, "generator", module)
    missing = [
        method for method in _REQUIRED_METHODS if not callable(getattr(generator, method, None))
    ]
    if missing:
        raise MoltError(
            f'The changelog generator "{name}" does not implement '
            f"{' and '.join(missing)}; a generator must provide "
            "get_release_line(changeset, bump, options, forge) and "
            "get_dependency_release_line(changesets, dependencies, options, forge)"
        )
    return ResolvedGenerator(generator, options)


def _split(setting: object) -> tuple[str | None, Mapping[str, object] | None]:
    """Normalize the three written shapes to ``(name, options)``.

    ``True`` resolves to the default generator rather than being rejected: it is the shape a user
    reaches for when they mean "yes, changelogs", and refusing it would be a papercut with no
    upside.
    """
    if setting is None or setting is False:
        return None, None
    if setting is True:
        return "git", None
    if isinstance(setting, str):
        return setting, None
    if isinstance(setting, (tuple, list)) and setting:
        name = setting[0]
        options = setting[1] if len(setting) > 1 else None
        if not isinstance(name, str):
            raise MoltError(
                f"The changelog generator name must be a string, not {type(name).__name__}"
            )
        if options is not None and not isinstance(options, dict):
            raise MoltError(
                f"A changelog generator's options must be a table, not {type(options).__name__}"
            )
        return name, options
    raise MoltError(
        f"Unsupported changelog setting {setting!r}: expected false, a generator name, or a "
        "[name, options] pair"
    )


def _load(name: str, *, changeset_dir: Path, project_root: Path) -> Any:
    """Import the module behind ``name``, trying each documented resolution form in order."""
    entry_point = _entry_point(name)
    if entry_point is not None:
        return entry_point

    if _looks_like_a_path(name):
        module = _load_from_file(name, changeset_dir=changeset_dir, project_root=project_root)
        if module is not None:
            return module
        raise MoltError(
            f'Could not find the changelog generator "{name}": no such file under '
            f"{changeset_dir} or {project_root}"
        )

    for candidate in (name, f"{_BUILTIN_PACKAGE}.{name}"):
        try:
            return importlib.import_module(candidate)
        except ImportError:
            continue
    raise MoltError(
        f'Could not resolve the changelog generator "{name}": it is not registered in the '
        f'"{_ENTRY_POINT_GROUP}" entry-point group, and neither "{name}" nor '
        f'"{_BUILTIN_PACKAGE}.{name}" could be imported'
    )


def _entry_point(name: str) -> Any:
    """The ``molt.changelog`` entry point ``name`` refers to, loaded, or ``None``.

    Two spellings match: the written name, and the written name with the ``molt.changelog.`` group
    prefix removed. The second is what makes the *default* resolvable at all --
    ``molt.config.BUILTIN_CHANGELOG`` is ``"molt.changelog.default"``, which is neither an
    importable module nor an entry-point name, so before this it could not be loaded by any of the
    four resolution forms and every project on the default configuration failed at ``molt version``.
    Stripping the prefix rather than special-casing the constant keeps the built-in on the same path
    a third-party generator takes.
    """
    candidates = {name}
    prefix = f"{_ENTRY_POINT_GROUP}."
    if name.startswith(prefix):
        candidates.add(name[len(prefix) :])
    for candidate in importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP):
        if candidate.name in candidates:
            return candidate.load()
    return None


def _looks_like_a_path(name: str) -> bool:
    """Whether ``name`` is written as a path rather than as a module or entry-point name.

    Deliberately syntactic. A value is a path when the author wrote one -- a ``./`` or ``../``
    prefix, a separator, or a ``.py`` suffix -- never because a file happens to exist with that
    name, which would make resolution depend on the contents of the working directory.
    """
    return (
        name.startswith(("./", "../", ".\\", "..\\"))
        or name.endswith(".py")
        or "/" in name
        or "\\" in name
    )


def _load_from_file(name: str, *, changeset_dir: Path, project_root: Path) -> Any:
    """Import ``name`` as a file, ``.changeset/`` first, then the project root."""
    for base in (changeset_dir, project_root):
        path = (base / name).resolve()
        if not path.is_file():
            continue
        # A stable, namespaced module name: two projects' `./changelog.py` must not collide in
        # `sys.modules`, and a bare `changelog` would shadow a real installed package.
        module_name = f"_molt_changelog_plugin_{abs(hash(str(path)))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - unreadable source file
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None
