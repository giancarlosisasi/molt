"""molt's exception vocabulary.

Ports ``packages/errors/src/index.ts:1-33`` -- five classes, each extending the bare JS ``Error``
with no shared base -- and adds three things upstream has no equivalent for: the :class:`MoltError`
root, and the two molt-native classes (:class:`MoltParseError`, :class:`MoltKeyPathError`) that the
changeset grammar and the TOML editing layer raise.

Faithfulness stops at the message strings. The five ported classes reproduce
``errors/src/index.ts`` formats exactly, because the CLI error funnel renders them verbatim to the
user; everything about their *structure* (a shared base, ``.code`` as an attribute) is Pythonic.

:class:`InternalError` is what guards the fixed/linked glob regression (research README
section 3.4), so the diagnostic it is raised with must survive to the user unchanged.

This module imports nothing outside the standard library, and must stay that way: ``molt --help``
and shell completion import the error vocabulary without paying pydantic's or rich's import cost
(research README section 6).
"""

from __future__ import annotations

__all__ = [
    "ExitError",
    "GitError",
    "InternalError",
    "MoltError",
    "MoltForgeError",
    "MoltForgeRepoError",
    "MoltKeyPathError",
    "MoltParseError",
    "MoltTemplateError",
    "PreEnterButInPreModeError",
    "PreExitButNotInPreModeError",
]


class MoltError(Exception):
    """The root of every exception molt itself raises.

    ``@changesets/errors`` has no equivalent -- its classes all extend the bare JS ``Error`` with
    no shared marker. molt needs one because the CLI error funnel has to separate "an expected,
    molt-raised failure" (friendly message, specific exit code) from "a bug" (traceback plus the
    issue-report URL). ``except MoltError`` is not a usable contract without a single root to catch.

    This is a real subclass of ``Exception``, never an alias for it. ``MoltError = Exception``
    satisfies every ``isinstance`` assertion while making ``except MoltError`` equivalent to
    ``except Exception``, which sends every bug down the friendly branch and prints no traceback.
    """


class GitError(MoltError):
    """A git subprocess failed.

    ``errors/src/index.ts:4`` -- the string form is ``"{message}, exit code: {code}"``. ``.code``
    is kept separately so callers can branch on the git exit status rather than parse the message.
    """

    code: int

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message}, exit code: {code}")
        self.code = code


class ExitError(MoltError):
    """A child process exited non-zero and molt is propagating its status.

    ``errors/src/index.ts:9-15``. The message is a contract, pinned exactly rather than searched
    for the digit: the funnel renders this sentence to the user.
    """

    code: int

    def __init__(self, code: int) -> None:
        super().__init__(f"The process exited with code: {code}")
        self.code = code


class PreExitButNotInPreModeError(MoltError):
    """``pre exit`` was requested outside pre mode (``errors/src/index.ts:17-22``).

    molt drops ``pre.json`` -- ``--pre`` is a stateless flag (research README section 4.2) -- so
    this guards no mode file. It is kept because ``molt version --pre`` still has misuse cases that
    want exactly this sentence. Do not delete it as unreferenced.
    """

    def __init__(self) -> None:
        super().__init__("pre mode cannot be exited when not in pre mode")


class PreEnterButInPreModeError(MoltError):
    """``pre enter`` was requested while already in pre mode (``errors/src/index.ts:23-27``).

    See :class:`PreExitButNotInPreModeError` on why this survives the ``pre.json`` drop.
    """

    def __init__(self) -> None:
        super().__init__("pre mode cannot be entered when in pre mode")


class InternalError(MoltError):
    """An internal invariant was violated -- a bug in molt, reported with its diagnostic.

    ``errors/src/index.ts:29-33``. A plain message passthrough: this is what guards the
    fixed/linked glob regression (research README section 3.4), and the text it is raised with is
    the whole diagnostic, so nothing here may rewrite it.
    """


class MoltParseError(MoltError):
    """A changeset frontmatter, grammar, or manifest document could not be parsed.

    molt-native (research doc 02 section 12.6); nothing upstream corresponds. ``path`` and ``line``
    are keyword-only so no call site can pass a location positionally and have it read as a second
    message, and both default to ``None`` because a failure caught deep inside a helper -- a
    malformed TOML document with no changeset file on disk -- may know neither.

    The string form is the message alone; the location fields are for callers to render, not part
    of the sentence.
    """

    path: str | None
    line: int | None

    def __init__(self, message: str, *, path: str | None = None, line: int | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.line = line


class MoltKeyPathError(MoltError):
    """A TOML key path could not be resolved or edited.

    molt-native. Raised by the TOML editing layer for a missing key path, an unsplice-able
    dependency section, or a direct-reference requirement with no specifier region. The message
    must name the failing key path or dependency so callers stay debuggable.

    A **sibling** of :class:`MoltParseError`, deliberately -- neither subclasses the other. The
    editing layer branches on a grammar failure and a key-path failure separately, and nesting
    would let the broader ``except`` silently swallow the narrower case.
    """


class MoltForgeError(MoltError):
    """A forge backend could not answer a request.

    molt-native; ``get-github-info`` raises bare ``Error``s. Covers every way a code host can fail
    molt: no token configured, a permanent HTTP status, a transient one that outlived the retry
    budget, an exhausted rate limit, and the GraphQL failures GitHub reports **inside** a 200
    response (``forge-seam`` spec, "Errors inside successful responses are raised").

    The message is the whole diagnostic and is rendered to the user by the CLI error funnel, so
    every raise site names the status, the endpoint or the variable the operator has to act on.
    Attribution is a nice-to-have, but a silent failure here publishes a changelog with missing
    credits, which nobody notices until it is released.
    """


class MoltForgeRepoError(MoltForgeError, ValueError):
    """A repository identifier is not of the form ``userOrOrg/repoName``.

    Ports ``get-github-info/src/utils.ts:1-9``, which validates the slug before issuing any
    request. molt keeps that ordering: a malformed slug is interpolated into a URL, so validating
    late would leak the token to whatever host the interpolation produced
    (``tests/forge/test_github.py::test_an_invalid_repo_is_rejected_before_any_network_call``
    asserts no request is made).

    **Both bases are load-bearing**, the same arrangement :class:`MoltTemplateError` uses.
    :class:`MoltForgeError` is what the CLI funnel catches to print a friendly message.
    ``ValueError`` is what the conformance suite asserts, because a bad slug is bad *input* and a
    backend that
    reported it as anything else would also satisfy a bare ``except Exception``.
    """


class MoltTemplateError(MoltError, ValueError):
    """A changelog template could not be compiled or rendered.

    molt-native. Raised by :mod:`molt.changelog.template` and :mod:`molt.changelog.render` for an
    unknown release-line token, an undefined name, and a malformed template. A silently empty
    changelog is worse than a failed run, because it is published before anybody notices, so every
    one of those is a hard error (``changelog-templating`` spec, "Template failures are hard
    errors").

    **The two bases are both load-bearing, and neither is decoration.** :class:`MoltError` is what
    the CLI error funnel catches to print a friendly message instead of a traceback -- a user's
    template typo is a configuration mistake, not a bug in molt. :class:`ValueError` is what the
    two conformance suites assert (``tests/changelog/test_render_template.py`` and
    ``test_render_changelog.py``), and asserting it is how they detect a raw ``jinja2`` exception
    leaking out of molt: :class:`jinja2.TemplateError` derives from neither base, so an unwrapped
    Jinja2 failure fails those tests rather than passing quietly. It also keeps this class
    consistent with :func:`molt.changelog.generate_markdown_for_version_type`, which already
    reports a bad bump type as a ``ValueError``.
    """
