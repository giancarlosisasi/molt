"""picomatch-compatible glob matching, ported once and used twice.

Ports ``@changesets/config``'s ``globMatch`` (``packages/config/src/utils.ts:13-41``) and its
byte-identical twin ``globMatchSome`` (``packages/git/src/index.ts:341-369``). Research doc 02
section 4.3 says "Port this once, use it twice", which is why this module sits at the package root:
``molt.config`` matches package **names** with it, and ``molt.git`` will match **file paths**
relative to a package directory with the same code.

**Do not reach for ``fnmatch``.** Research doc 02 section 12.4 opens the comparison table with the
reason: ``fnmatch``'s ``*`` crosses ``/``, its ``?`` matches ``/``, it has no negation, and its case
folding is platform-dependent via ``os.path.normcase``. Every one of those is a behavior this module
is required to get right, so the pattern -> regex compiler below is hand-written.

The semantics that are load-bearing, all verified upstream (research doc 02 sections 4.2/4.3):

- ``*`` matches within one segment; ``**`` crosses separators; ``?`` is one non-separator character.
- Matching is **order-sensitive**. A negation only subtracts from what an earlier pattern already
  matched, so a *leading* negation is a no-op and a later positive pattern can re-add a name a
  negation removed.
- The result preserves the order of ``paths`` (the package list), not of the patterns.
- ``\\`` is normalized to ``/`` on the **candidate**, never on the pattern.

``key`` is molt-native: package-name matching normalizes both pattern and candidate per PEP 503
before comparing (research doc 02 section 12.4), while file-path matching must not. Passing the
normalizer in keeps one matcher for both callers instead of forking it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

__all__ = ["compile_pattern", "glob_match", "glob_match_some", "unmatched_patterns"]

#: Applied to a candidate before matching, mirroring ``utils.ts:26``. Windows hands us backslashes;
#: patterns are always written with forward slashes.
_BACKSLASH = "\\"

Key = Callable[[str], str]


class _Matcher:
    """A compiled pattern plus picomatch's ``state.negated`` flag.

    ``regex`` is the *inner* pattern, with any leading ``!`` stripped. :meth:`hits` answers "does
    the inner pattern match", which is what both branches of :func:`glob_match` need; picomatch's
    own matcher already folds the negation in, so its callers ask the inverse question and the two
    formulations agree (``not matcher(path)`` == ``inner.match(path)`` for a negated pattern).
    """

    __slots__ = ("negated", "regex", "source")

    def __init__(self, source: str, regex: re.Pattern[str], negated: bool) -> None:
        self.source = source
        self.regex = regex
        self.negated = negated

    def hits(self, candidate: str) -> bool:
        return self.regex.match(candidate) is not None

    def accepts(self, candidate: str) -> bool:
        """Picomatch's own matcher: a negated pattern accepts everything *but* its body."""
        hit = self.hits(candidate)
        return not hit if self.negated else hit


def _translate(pattern: str) -> str:
    """Translate the picomatch subset molt uses (``**``, ``*``, ``?``, ``[...]``) to a regex body.

    Brace expansion (``{a,b}``) is deliberately **not** supported: picomatch has it, no molt
    behavior needs it, and silently mistranslating it would be worse than treating the braces as
    the literal characters they are.
    """
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                # `**/` should also match zero directories, the way picomatch reads `**/x`.
                if pattern.startswith("/", index):
                    out.append("/?")
                    index += 1
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            closing = pattern.find("]", index + 1)
            if closing == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1 : closing]
                if body.startswith(("!", "^")):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                index = closing + 1
                continue
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)


def compile_pattern(pattern: str, key: Key | None = None) -> _Matcher:
    """Compile one pattern, stripping and recording a leading ``!`` as picomatch's ``negated``.

    ``key`` is applied to the pattern *body* so that both sides of the comparison are folded the
    same way; PEP 503 normalization leaves glob metacharacters untouched (``foo-*`` -> ``foo-*``),
    which is what makes normalizing the pattern safe.
    """
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    if key is not None:
        body = key(body)
    return _Matcher(pattern, re.compile(rf"(?s:{_translate(body)})\Z"), negated)


def glob_match(
    paths: Sequence[str], patterns: Sequence[str] | None, *, key: Key | None = None
) -> list[str]:
    """Filter ``paths`` by ``patterns``, preserving ``paths`` order (``utils.ts:13-41``).

    ``patterns is None`` returns everything (upstream's ``patterns === undefined`` branch, never
    reached for ``ignore`` whose default is ``[]``); ``[]`` returns nothing.
    """
    if patterns is None:
        return list(paths)
    matchers = [compile_pattern(pattern, key) for pattern in patterns]
    kept: list[str] = []
    for path in paths:
        if _passes(_candidate(path, key), matchers):
            kept.append(path)
    return kept


def glob_match_some(path: str, patterns: Sequence[str] | None, *, key: Key | None = None) -> bool:
    """The boolean form (``git/src/index.ts:341-369``), used for ``changed_file_patterns``."""
    if patterns is None:
        return True
    matchers = [compile_pattern(pattern, key) for pattern in patterns]
    return _passes(_candidate(path, key), matchers)


def unmatched_patterns(
    patterns: Iterable[str], candidates: Sequence[str], *, key: Key | None = None
) -> list[str]:
    """Patterns that match no candidate, in written order (``rules.ts:6-14``).

    Negation is evaluated the way ``getUnmatchedPatterns`` evaluates it -- through the *matcher*,
    not the inner body -- so ``!pkg-b`` accepts every other name and is never reported unmatched.
    That is upstream behavior worth keeping: a negation names something to remove, and warning that
    it "matched nothing" would fire on every correct use.
    """
    folded = [_candidate(candidate, key) for candidate in candidates]
    unmatched: list[str] = []
    for pattern in patterns:
        matcher = compile_pattern(pattern, key)
        if not any(matcher.accepts(candidate) for candidate in folded):
            unmatched.append(pattern)
    return unmatched


def _candidate(path: str, key: Key | None) -> str:
    folded = path.replace(_BACKSLASH, "/")
    return key(folded) if key is not None else folded


def _passes(candidate: str, matchers: Sequence[_Matcher]) -> bool:
    """Upstream's order-sensitive accumulator, ported verbatim (``utils.ts:27-38``).

    While ``passed`` is false only positive patterns are consulted, which is what makes a leading
    negation inert. Once true, only negations can flip it back -- and a later positive pattern can
    flip it forward again.
    """
    passed = False
    for matcher in matchers:
        if not passed:
            if not matcher.negated and matcher.hits(candidate):
                passed = True
        elif matcher.negated and matcher.hits(candidate):
            passed = False
    return passed
