"""The ``file`` version source: a version literal living inside a file the user wrote.

This covers the common modern Python idioms -- hatch's ``[tool.hatch.version] path`` pointing at an
``__about__.py``, setuptools' ``{attr = "pkg.__version__"}`` and ``{file = "VERSION"}``, pdm's file
source -- which research doc 02 section 12.5 names as the ones a Python release tool has to
understand. It is the concentrated risk of the whole seam, because it edits a source file molt did
not create.

**The write splices one located span; it never re-renders** (design D4). Reading and writing use
the *same* located region, exactly as :func:`molt.apply.edit_toml.specifier_region` does for
dependency specifiers, and for the same reason: these are files the user authored, and a release
tool that reformats them is a release tool nobody runs twice. Comments, imports, a second unrelated
assignment and the file's own line endings all survive byte-for-byte.

**Two matches is an error, not a first-wins pick.** A file with two version literals has no single
answer, and picking one silently is how a package ships a version it never declared.

Two fallbacks, in order, and both documented rather than magic:

1. no match with the pattern, but the file's entire stripped content parses as a PEP 440 version ->
   the file **is** the version (setuptools' ``{file = "VERSION"}`` idiom). The write replaces the
   whole content, preserving the trailing newline if there was one.
2. neither -> a located :class:`~molt.errors.MoltError`, which the resolver turns into an
   :class:`~molt.ecosystem.version_sources.protocol.UnresolvedVersion`.

The default pattern recognizes three literal names and was **chosen, not decided** (gap ``VS-5``):
``__version_info__``, a tuple assignment, a version in ``setup.cfg`` and a version spelled some
other way all fall through to unresolved.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from molt.errors import MoltError

if TYPE_CHECKING:
    from molt.ecosystem.version_sources.declaration import VersionSourceOptions
    from molt.ecosystem.version_sources.protocol import VersionWrite

__all__ = ["DEFAULT_VERSION_PATTERN", "FileVersionSource", "build"]

#: ``__version__`` / ``version`` / ``VERSION`` assigned to a single- or double-quoted string, at
#: any indentation, optionally annotated (``__version__: str = "1.2.3"``). Case-sensitive on the
#: name: ``Version`` is a class in half the libraries molt will ever read. The ``version`` group is
#: the span the write replaces, and it is the only part of the file that changes.
DEFAULT_VERSION_PATTERN = (
    r"(?m)^[ \t]*(?:__version__|version|VERSION)[ \t]*(?::[^=\n]+)?=[ \t]*"
    r"(?P<quote>['\"])(?P<version>[^'\"\n]+)(?P=quote)"
)

#: The named group every pattern must produce. Stated once so the config validator and this module
#: agree on the requirement rather than restating it in two spellings.
VERSION_GROUP = "version"


class FileVersionSource:
    """A version located inside ``path``, relative to the package's own directory."""

    kind = "file"

    __slots__ = ("_directory", "_name", "_pattern", "_relative")

    def __init__(
        self, *, name: str, directory: Path, relative: str, pattern: str | None = None
    ) -> None:
        self._name = name
        self._directory = directory
        self._relative = relative
        self._pattern = pattern or DEFAULT_VERSION_PATTERN

    # ----------------------------------------------------------------------------------
    # Reading
    # ----------------------------------------------------------------------------------

    def read(self) -> str:
        """The version located in the file, or a located refusal saying what was tried."""
        text = self._text()
        span = self._locate(text)
        if span is not None:
            return text[span[0] : span[1]]
        whole = self._whole_file_version(text)
        if whole is not None:
            return whole
        raise MoltError(
            f'"{self._name}" keeps its version in {self._relative}, but molt could not find a '
            f"version there. Expected a quoted version literal matching {self._pattern!r}, or a "
            f"file whose entire contents are the version. Set "
            f"[tool.molt.version_source] pattern in {self._name}'s pyproject.toml to say where "
            f"the version is."
        )

    def plan_write(self, new_version: str) -> tuple[VersionWrite, ...] | None:
        """Replace **only** the located version text, keeping every other byte of the file."""
        text = self._text()
        span = self._locate(text)
        if span is not None:
            updated = text[: span[0]] + new_version + text[span[1] :]
        else:
            whole = self._whole_file_version(text)
            if whole is None:
                raise MoltError(
                    f'"{self._name}" keeps its version in {self._relative}, but molt could not '
                    f"find a version there to replace."
                )
            updated = new_version + _trailing_newline(text)
        from molt.ecosystem.version_sources.protocol import VersionWrite

        return (VersionWrite(path=self.path, data=updated.encode("utf-8")),)

    def describe(self) -> str:
        return self._relative

    # ----------------------------------------------------------------------------------
    # The located span, and the whole-file fallback
    # ----------------------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """The absolute path of the version file."""
        return self._directory / self._relative

    def _text(self) -> str:
        """The file's bytes decoded **without** newline translation.

        ``Path.read_text`` would fold CRLF to LF and the write would then land LF-only, silently
        rewriting every line of a Windows-authored file. Reading bytes and decoding by hand is what
        makes "every other byte survives" true rather than approximately true.
        """
        path = self.path
        try:
            return path.read_bytes().decode("utf-8")
        except OSError as error:
            raise MoltError(
                f'"{self._name}" declares its version file as {self._relative}, which molt could '
                f"not read ({error}). Point [tool.molt.version_source] path at a file that exists."
            ) from error
        except UnicodeDecodeError as error:
            raise MoltError(
                f'"{self._name}"\'s version file {self._relative} is not valid UTF-8, so molt '
                f"cannot locate a version in it."
            ) from error

    def _locate(self, text: str) -> tuple[int, int] | None:
        """The ``version`` group's span, ``None`` when nothing matched -- **two matches raise**."""
        matches = list(re.finditer(self._pattern, text))
        if not matches:
            return None
        if len(matches) > 1:
            raise MoltError(
                f'"{self._name}"\'s version file {self._relative} contains '
                f"{len(matches)} version literals, so molt cannot tell which one is the "
                f"version. Keep one, or set [tool.molt.version_source] pattern to a pattern that "
                f"matches exactly one."
            )
        match = matches[0]
        span = match.span(VERSION_GROUP)
        if span == (-1, -1):  # pragma: no cover - the group is required by the config validator
            raise MoltError(
                f'The version pattern for "{self._name}" matched {self._relative} but captured no '
                f'"{VERSION_GROUP}" group, so molt does not know which characters to replace.'
            )
        return span

    def _whole_file_version(self, text: str) -> str | None:
        """The whole stripped file when it parses as PEP 440 -- setuptools' ``{file = ...}``."""
        from packaging.version import InvalidVersion, Version

        candidate = text.strip()
        if not candidate or "\n" in candidate:
            return None
        try:
            Version(candidate)
        except InvalidVersion:
            return None
        return candidate


def _trailing_newline(text: str) -> str:
    """The newline the file ended with, so a whole-file rewrite keeps the file's own ending."""
    if text.endswith("\r\n"):
        return "\r\n"
    if text.endswith("\n"):
        return "\n"
    return ""


def build(
    *, name: str, directory: Path, version: str | None, options: VersionSourceOptions
) -> FileVersionSource:
    """The entry-point constructor. Refuses a ``path`` that escapes the package directory.

    The escape check is here rather than inside the source because it is a fact about the
    *declaration*, not about a read: a path pointing at ``../../etc/hosts`` must be refused before
    anything opens it, and the message names both the package and the path so the manifest at fault
    is obvious in a thirty-member workspace.
    """
    del version
    relative = options.path
    if relative is None:
        raise MoltError(
            f'"{name}" declares a file version source with no path. Write '
            f'[tool.molt.version_source] path = "src/{name.replace("-", "_")}/__about__.py" '
            f"(or wherever the version literal lives)."
        )
    base = directory.resolve()
    target = (directory / relative).resolve()
    if target != base and base not in target.parents:
        raise MoltError(
            f'"{name}" declares its version file as "{relative}", which resolves outside the '
            f"package directory ({base}). A version source must live inside the package it "
            f"versions."
        )
    return FileVersionSource(
        name=name, directory=directory, relative=relative, pattern=options.pattern
    )
