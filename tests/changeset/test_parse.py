r"""Conformance tests for the changeset frontmatter grammar (``parse_changeset``).

Ports ``packages/parse/src/index.test.ts`` **in full**: all 20 reference cases, every one a Port
(``roadmap/research/test-suite/03-config-changeset-io.md``, section
``packages/parse/src/index.test.ts``, rows 1-20; that file's totals table records
20 Port / 0 Adapt / 0 Drop -- there is nothing to drop here). The grammar itself is specified in
``roadmap/research/changesets-02-config-and-data-model.md`` section 5: 5.1 grammar, 5.2 algorithm
(``parse/src/index.ts:51-109``), 5.3 YAML behaviours, 5.4 the exact error text.

Reference regex (``parse/src/index.ts:4``)::

    /\s*---([^]*?)\r?\n\s*---(\s*(?:\n|$)[^]*)/

Python translation (doc 02 section 12.3)::

    re.compile(r"\s*---(.*?)\r?\n\s*---(\s*(?:\n|\Z).*)", re.DOTALL)

Load-bearing properties of that regex, all exercised below: the frontmatter capture is **lazy**
(first closing fence wins, so ``---`` inside a quoted name or inside the body is safe), the closing
fence may be **indented** and may be followed by whitespace but **nothing else** on the same line,
and both ``\n`` and ``\r\n`` are accepted. Valid version types are ``major, minor, patch, none``
(``parse/src/index.ts:8-13``).

Three assertions here are molt's, not upstream's:

* **Markdown ``#`` headings in a summary are PRESERVED.** Upstream strips every line beginning
  with ``#`` during changelog assembly, which destroys Markdown headings in changeset descriptions
  (research README section 3.4, "upstream bugs -- do not port these").
  :func:`test_markdown_headings_in_the_summary_are_preserved` asserts the **corrected** behavior;
  it is deliberately *not* a port of upstream's output. See
  ``tests/changeset/test_deliberately_not_ported.py`` for the audit record of the dropped bug.
* **PEP 503 name normalization is a lookup-time concern, not a parse-time one** (research README
  section 4.5; doc 02 section 12.5 -- "preserve the original spelling for display and for writing
  changeset frontmatter"). ``parse_changeset`` keeps the author's literal spelling.
* **CRLF** (row 13) is promoted to a first-class assertion rather than an afterthought: molt is
  Windows-correct from day one (repo ``CLAUDE.md``).
"""

from __future__ import annotations

import importlib
import re
import textwrap
from typing import Any, Final

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from molt.versioning import BumpType

pytest.importorskip(
    "molt.changeset", reason="build step 3 - changeset IO not yet implemented (TDD target)"
)

from molt.changeset import parse_changeset

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------------------
# The exception under test
# --------------------------------------------------------------------------------------

# Research doc 02 section 12.6 asks for a dedicated parse exception (upstream throws a bare
# `Error` -- doc 02 section 5.4, "no dedicated error class") carrying `path`, `line` and the
# offending text; the name recorded there is `MoltParseError`. `molt.errors` is build step 9, so
# the class is resolved by name with a documented fallback to `Exception`: that keeps these tests
# lighting up the moment `molt.changeset` (step 3) lands, and tightens automatically once
# `molt.errors` does. If the implementation picks a different name, change this constant -- not
# the assertions, which only ever pin a stable substring of the message.
PARSE_ERROR_NAME: Final = "MoltParseError"
_PARSE_ERROR_ALIASES: Final = (PARSE_ERROR_NAME, "ChangesetParseError", "ParseError")


def _resolve_parse_error() -> type[BaseException]:
    try:
        errors_module = importlib.import_module("molt.errors")
    except ModuleNotFoundError:
        return Exception
    for name in _PARSE_ERROR_ALIASES:
        candidate = getattr(errors_module, name, None)
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            return candidate
    return Exception


ParseError: Final = _resolve_parse_error()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def md(text: str) -> str:
    """The Python analogue of the reference fixture ``outdent`` (group file, parse fixtures).

    ``textwrap.dedent`` also normalizes whitespace-only lines to empty, so any case that is
    sensitive to trailing whitespace is built by explicit concatenation instead.
    """
    return textwrap.dedent(text)


def releases_of(changeset: Any) -> list[tuple[str, BumpType]]:
    """Normalize ``Changeset.releases`` to ``(name, type)`` pairs.

    Only ``.name`` and ``.type`` are pinned; the concrete release class stays free (test contract
    section 5). ``BumpType`` is a ``StrEnum``, so this compares equal to plain strings too.
    """
    return [(release.name, release.type) for release in changeset.releases]


def _pep503(name: str) -> str:
    """PEP 503 normalization, spelled out locally so the test states its own reference.

    Mirrors ``re.sub(r"[-_.]+", "-", name).lower()`` (research README section 4.5).
    """
    return re.sub(r"[-_.]+", "-", name).lower()


MULTILINE_SUMMARY: Final = md(
    """\
    Let us go then you and I,
    When the evening is spread out against the sky
    Like a patient, etherized upon a table.

    - The Lovesong of J Alfred Prufrock, T. S. Eliot"""
)

# Trailing whitespace is load-bearing in rows 9 and 12, and editors/formatters strip it from
# string literals, so those two documents are concatenated rather than written inline.
_FRONTMATTER_ONLY: Final = '---\n"cool-package": minor\n---'


# --------------------------------------------------------------------------------------
# Rows 1-9, 12, 14 -- the homogeneous "parses to (releases, summary)" table
# --------------------------------------------------------------------------------------

PARSE_CASES: list[tuple[str, list[tuple[str, BumpType]], str, str]] = [
    (
        md(
            """\
            ---
            "cool-package": minor
            ---

            Nice simple summary
            """
        ),
        [("cool-package", BumpType.MINOR)],
        "Nice simple summary",
        "row 1: the baseline document; the summary is stripped (index.test.ts:6-19)",
    ),
    (
        md(
            """\
            ---
            "cool-package": minor
            "cool-package2": major
            "cool-package3": patch
            ---

            Nice simple summary
            """
        ),
        [
            ("cool-package", BumpType.MINOR),
            ("cool-package2", BumpType.MAJOR),
            ("cool-package3", BumpType.PATCH),
        ],
        "Nice simple summary",
        "row 2: YAML mapping insertion order is preserved and is load-bearing "
        "(research README section 3.3; index.test.ts:20-39)",
    ),
    (
        md(
            """\
            ---
            "@cool/package": minor
            ---

            Nice simple summary
            """
        ),
        [("@cool/package", BumpType.MINOR)],
        "Nice simple summary",
        "row 3: a quoted name is opaque to the parser. molt has no scopes (flat global "
        "namespace, research README section 4.5) but the grammar must stay name-agnostic "
        "(index.test.ts:40-53)",
    ),
    (
        md(
            """\
            ---
            "cool-package": minor
            ---

            Let us go then you and I,
            When the evening is spread out against the sky
            Like a patient, etherized upon a table.

            - The Lovesong of J Alfred Prufrock, T. S. Eliot
            """
        ),
        [("cool-package", BumpType.MINOR)],
        MULTILINE_SUMMARY,
        "row 4: blank lines and `-` list markers survive verbatim (index.test.ts:54-77)",
    ),
    (
        md(
            """\
            ---
            "cool-package": minor
            "best-package": patch
            ---

            Let us go then you and I,
            When the evening is spread out against the sky
            Like a patient, etherized upon a table.

            - The Lovesong of J Alfred Prufrock, T. S. Eliot
            """
        ),
        [("cool-package", BumpType.MINOR), ("best-package", BumpType.PATCH)],
        MULTILINE_SUMMARY,
        "row 5: rows 2 and 4 combined (index.test.ts:78-105)",
    ),
    (
        md(
            """\
            ---
            "cool---package": minor
            ---

            Nice simple summary
            """
        ),
        [("cool---package", BumpType.MINOR)],
        "Nice simple summary",
        "row 6: the frontmatter capture is lazy and the fence must own its line, so `---` "
        "inside a quoted name is safe (index.test.ts:106-119)",
    ),
    (
        md(
            """\
            ---
            "cool-package": minor
            ---

            ---
            Nice simple summary---that has this

            """
        ),
        [("cool-package", BumpType.MINOR)],
        "---\nNice simple summary---that has this",
        "row 7: only the FIRST delimiter pair is frontmatter; the rest is summary "
        "(index.test.ts:120-138)",
    ),
    (
        _FRONTMATTER_ONLY,
        [("cool-package", BumpType.MINOR)],
        "",
        "row 8: no body at all and no trailing whitespace -> empty summary (index.test.ts:139-149)",
    ),
    (
        _FRONTMATTER_ONLY + " ",
        [("cool-package", BumpType.MINOR)],
        "",
        "row 9: a single trailing space after the closing fence is tolerated "
        "(index.test.ts:150-160)",
    ),
    (
        _FRONTMATTER_ONLY + "  " + "\n\nNice simple summary\n",
        [("cool-package", BumpType.MINOR)],
        "Nice simple summary",
        "row 12: two trailing spaces after the closing fence, then a body (index.test.ts:180-195)",
    ),
    (
        '---\n    pkg: "minor"\n    ---\n\n    something',
        [("pkg", BumpType.MINOR)],
        "something",
        "row 14: YAML tolerance -- bare name, quoted type; the fences may also be indented "
        "(index.test.ts:217-228)",
    ),
]


@pytest.mark.parametrize(("source", "releases", "summary", "why"), PARSE_CASES)
def test_parse_changeset(
    source: str, releases: list[tuple[str, BumpType]], summary: str, why: str
) -> None:
    """Rows 1-9, 12 and 14 of ``packages/parse/src/index.test.ts`` (all Port)."""
    changeset = parse_changeset(source)
    assert releases_of(changeset) == releases, why
    assert changeset.summary == summary, why


# --------------------------------------------------------------------------------------
# Rows 10, 11 -- the empty changeset (`molt changeset add --empty`)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "why"),
    [
        ("---\n---\n\n", "row 10: what `changeset add --empty` writes (index.test.ts:161-172)"),
        ("---\n---", "row 11: the minimal valid document (index.test.ts:173-179)"),
    ],
)
def test_an_empty_changeset_yields_no_releases_and_an_empty_summary(source: str, why: str) -> None:
    """An empty frontmatter block is legal, not an error (doc 02 section 5 gotcha 6).

    ``yaml.parse("")`` is falsy -> ``releases = []`` (``parse/src/index.ts:90-91``).
    """
    changeset = parse_changeset(source)
    assert releases_of(changeset) == [], why
    assert changeset.summary == "", why


# --------------------------------------------------------------------------------------
# Row 13 -- CRLF. First-class for molt (repo CLAUDE.md: Windows-correct from day one)
# --------------------------------------------------------------------------------------


def test_windows_line_endings_parse_identically_to_unix_ones() -> None:
    """Row 13 (``index.test.ts:196-215``): ``\\r?\\n`` in the regex makes CRLF a non-event.

    Upstream treats this as a curiosity; molt treats it as a platform guarantee, because on
    Windows a checkout with ``core.autocrlf=true`` hands the parser CRLF for *every* changeset.
    """
    unix = md(
        """\
        ---
        "cool-package": minor
        "best-package": patch
        ---

        Nice simple summary
        """
    )
    windows = unix.replace("\n", "\r\n")

    expected = [("cool-package", BumpType.MINOR), ("best-package", BumpType.PATCH)]
    assert releases_of(parse_changeset(windows)) == expected
    assert parse_changeset(windows).summary == "Nice simple summary"
    assert releases_of(parse_changeset(windows)) == releases_of(parse_changeset(unix))
    assert parse_changeset(windows).summary == parse_changeset(unix).summary


# --------------------------------------------------------------------------------------
# Rows 15-20 -- the raising cases. Assertions pin a stable substring, never the whole
# English sentence; the verbatim upstream templates live in the group file, lines 160-167.
# --------------------------------------------------------------------------------------


def test_non_whitespace_after_the_closing_fence_is_rejected() -> None:
    """Row 15 (``index.test.ts:230-254``): group 2 of the regex allows whitespace only.

    Message shape: ``could not parse changeset - missing or invalid frontmatter.`` +
    ``Changesets must start with frontmatter delimited by "---".`` + the example +
    ``Received content:`` + ``truncate(trimmedContents)`` (group file line 163).
    """
    source = md(
        """\
        ---
        "cool-package": minor
        ---  fail

        Nice simple summary
        """
    )
    with pytest.raises(ParseError, match="missing or invalid frontmatter"):
        parse_changeset(source)


def test_invalid_yaml_structure_is_rejected_with_the_empty_name_message() -> None:
    """Row 16 (``index.test.ts:256-274``): ``: minor`` yields the key ``""``.

    ``validateReleases`` rejects a name whose ``.strip()`` is empty
    (``parse/src/index.ts:20-29``); the message echoes ``JSON.stringify(name)``, i.e. ``""``.
    """
    source = md(
        """\
        ---
        : minor
        ---

        Nice simple summary
        """
    )
    with pytest.raises(ParseError, match="invalid package name in frontmatter") as exc_info:
        parse_changeset(source)
    assert '""' in str(exc_info.value)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("", id="zero-length"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("\n\n", id="newlines-only"),
    ],
)
def test_a_completely_empty_file_is_rejected(source: str) -> None:
    """Row 17 (``index.test.ts:276-307``): the check is ``contents.strip()`` emptiness.

    Message shape: ``could not parse changeset - file is empty.`` + the example
    (group file line 162; ``parse/src/index.ts:55-63``).
    """
    with pytest.raises(ParseError, match="file is empty"):
        parse_changeset(source)


def test_missing_frontmatter_is_rejected() -> None:
    """Row 18 (``index.test.ts:309-323``): no ``---`` delimiters at all."""
    with pytest.raises(ParseError, match="missing or invalid frontmatter"):
        parse_changeset("Just some content without frontmatter")


def test_an_invalid_version_type_is_rejected_and_lists_the_valid_ones() -> None:
    """Row 19 (``index.test.ts:325-343``): the four-type enum is enforced.

    Message shape: ``could not parse changeset - invalid version type "<type>" for package
    "<name>".`` + ``Valid version types are: major, minor, patch, none`` (group file line 166;
    ``parse/src/index.ts:39-47``).
    """
    source = md(
        """\
        ---
        "cool-package": invalid-type
        ---

        Nice simple summary
        """
    )
    with pytest.raises(ParseError, match="invalid version type") as exc_info:
        parse_changeset(source)
    message = str(exc_info.value)
    assert '"invalid-type"' in message
    assert "cool-package" in message
    assert "Valid version types are: major, minor, patch, none" in message


def test_an_empty_package_name_is_rejected() -> None:
    """Row 20 (``index.test.ts:345-363``): an explicitly quoted empty name."""
    source = md(
        """\
        ---
        "": minor
        ---

        Nice simple summary
        """
    )
    with pytest.raises(ParseError, match="invalid package name in frontmatter"):
        parse_changeset(source)


# --------------------------------------------------------------------------------------
# Present in `parse/src/index.ts` but untested upstream -- the group file (line 167) says
# "port for completeness".
# --------------------------------------------------------------------------------------


def test_invalid_yaml_in_the_frontmatter_is_reported_as_such() -> None:
    """``parse/src/index.ts:77-88`` -- the YAML parser's own failure gets its own message.

    Shape: ``could not parse changeset - invalid YAML in frontmatter.`` + ``The frontmatter
    between the "---" delimiters must be valid YAML.`` + ``YAML error: <...>`` + the raw
    frontmatter (doc 02 section 5.4; note that this one message echoes the frontmatter
    **untruncated**). The unterminated quote below is a scanner error in every YAML
    implementation, JS or Python.
    """
    source = md(
        """\
        ---
        "cool-package: minor
        ---

        Nice simple summary
        """
    )
    with pytest.raises(ParseError, match="invalid YAML in frontmatter"):
        parse_changeset(source)


@pytest.mark.parametrize(
    "frontmatter",
    [
        pytest.param("- a\n- b", id="yaml-sequence"),
        pytest.param("hello", id="bare-scalar"),
    ],
)
def test_frontmatter_that_is_not_a_mapping_is_rejected(frontmatter: str) -> None:
    """``parse/src/index.ts:92-98`` -- the frontmatter must be an object, never an array/scalar.

    Shape: ``could not parse changeset - frontmatter must be an object mapping package names to
    version types.`` + ``Expected format:`` + the example + ``Received:`` + the raw frontmatter.
    """
    with pytest.raises(ParseError, match="frontmatter must be an object"):
        parse_changeset(f"---\n{frontmatter}\n---\n\nNice simple summary\n")


def test_echoed_contents_are_truncated_to_200_characters_plus_an_ellipsis() -> None:
    """``truncate(s, max=200)`` at ``parse/src/index.ts:15-17``; doc 02 section 5.2.

    Every message that echoes user content runs it through ``truncate`` (the sole exception is
    the YAML-error message, which echoes the frontmatter raw -- doc 02 section 5.4). Without this
    a 5 MB pasted changeset dumps 5 MB into the terminal.
    """
    filler = "x" * 500
    with pytest.raises(ParseError, match="missing or invalid frontmatter") as exc_info:
        parse_changeset(filler)
    message = str(exc_info.value)
    assert "x" * 200 + "..." in message
    assert "x" * 201 not in message


# --------------------------------------------------------------------------------------
# molt-specific: the upstream `#`-stripping bug is NOT reproduced
# --------------------------------------------------------------------------------------


def test_markdown_headings_in_the_summary_are_preserved() -> None:
    """molt PRESERVES Markdown headings. This is a deliberate, corrected divergence.

    Upstream's summary post-processing **strips every line starting with** ``#``, which destroys
    Markdown headings in changeset descriptions -- research README section 3.4 lists it under
    "upstream bugs -- do not port these". The bug lives in changelog assembly rather than in
    ``parse``, so the group file records **zero drops** for this file; molt's fix is asserted
    here, at the parser, where the summary is first materialized, and again as an audit record in
    ``tests/changeset/test_deliberately_not_ported.py``.

    If this test ever goes green by stripping ``#`` lines, molt has re-imported the bug.
    """
    source = md(
        """\
        ---
        "cool-package": minor
        ---

        # Heading

        body
        """
    )
    assert parse_changeset(source).summary == "# Heading\n\nbody"


def test_every_markdown_heading_level_and_hash_shaped_line_survives() -> None:
    """The stronger form of the previous test: nothing that starts with ``#`` is touched.

    Covers ATX levels 1-3, a ``#``-prefixed line inside a fenced code block (a shell comment --
    the case where stripping is most obviously wrong), and a bare ``#``.
    Research README section 3.4.
    """
    summary = md(
        """\
        # Title

        ## Section

        ### Subsection

        ```sh
        # this is a shell comment, not a heading
        molt version
        ```

        #"""
    )
    source = f'---\n"cool-package": minor\n---\n\n{summary}\n'
    assert parse_changeset(source).summary == summary


# --------------------------------------------------------------------------------------
# molt-specific: PEP 503 normalization is a LOOKUP-time concern (research README 4.5)
# --------------------------------------------------------------------------------------

PEP503_SPELLINGS: Final = ("Foo_Bar", "foo-bar", "foo.bar", "Foo--Bar")


@pytest.mark.parametrize("spelling", PEP503_SPELLINGS)
def test_parse_preserves_the_authors_literal_package_name(spelling: str) -> None:
    """DECISION: ``parse_changeset`` does **not** normalize; it preserves the literal spelling.

    ``Foo_Bar``, ``foo-bar``, ``foo.bar`` and ``Foo--Bar`` are one distribution under PEP 503
    (research README section 4.5), but doc 02 section 12.5 is explicit: build a
    ``normalized_name -> Package`` index and *"preserve the original spelling for display and for
    writing changeset frontmatter"*. So normalization belongs to **lookup/comparison** -- the
    engine, ``ignore``/``fixed``/``linked`` matching, dependency-graph edges -- and the parser
    stays a faithful reader. A changeset written by hand as ``Foo_Bar`` must still round-trip
    through ``write_changeset`` as ``Foo_Bar``.
    """
    changeset = parse_changeset(f'---\n"{spelling}": patch\n---\n\nSummary\n')
    assert releases_of(changeset) == [(spelling, BumpType.PATCH)]


def test_all_pep503_spellings_collapse_to_one_name_at_comparison_time() -> None:
    """The other half of the decision above: the four literals are one distribution.

    Whoever resolves changeset names against the workspace must compare
    ``re.sub(r"[-_.]+", "-", name).lower()`` (research README section 4.5), not the raw string.
    Asserted here so the parse-time/lookup-time split is stated in one place.
    """
    parsed = [parse_changeset(f'---\n"{s}": patch\n---\n\nS\n') for s in PEP503_SPELLINGS]
    literals = [releases_of(changeset)[0][0] for changeset in parsed]
    assert literals == list(PEP503_SPELLINGS), "the parser must not collapse the spellings"
    assert {_pep503(name) for name in literals} == {"foo-bar"}


# --------------------------------------------------------------------------------------
# Property -- parse is total over well-formed documents. Pairs with the write round-trip
# property in tests/changeset/test_write.py.
# --------------------------------------------------------------------------------------

package_names = st.from_regex(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,15}\Z")
bump_types = st.sampled_from(list(BumpType))
# Deliberately includes `#`, `-` (so `---` shows up), blank lines and spaces: exactly the
# characters the upstream `#`-stripping bug and the `---` delimiter would trip over.
summaries = st.text(alphabet=st.sampled_from("abXY 019-#*.:\n"), max_size=60).map(str.strip)


@pytest.mark.property
@given(name=package_names, bump=bump_types, summary=summaries)
@settings(deadline=None)
def test_a_rendered_changeset_always_parses_back_to_its_inputs(
    name: str, bump: BumpType, summary: str
) -> None:
    """Parse is total over well-formed documents, for any valid name/type/summary.

    The rendering here is the writer's template (doc 02 section 5.5), so this is the parse half
    of the write/parse round-trip guarded in ``tests/changeset/test_write.py``. Summaries are
    generated pre-stripped because ``summary.trim()`` makes round-tripping lossy for
    leading/trailing whitespace (doc 02 section 5.5) -- that lossiness is upstream behavior, not
    a bug to fix.
    """
    document = f'---\n"{name}": {bump.value}\n---\n\n{summary}\n'
    changeset = parse_changeset(document)
    assert releases_of(changeset) == [(name, bump)]
    assert changeset.summary == summary
