"""Conformance tests for molt's format-preserving ``pyproject.toml`` edit primitives.

Ports ``packages/apply-release-plan/src/edit-json.test.ts`` -- the 7-row section of
``roadmap/research/test-suite/04-apply-changelog.md`` (0 Port / 7 Adapt / 0 Drop) -- and then
goes well past it, because this is the highest-risk surface in phase P4 and the upstream rows
only exercise a JSON value-map.

Why every row is an Adapt
-------------------------
Upstream ``editJson`` (``edit-json.ts:19-54``) is ``jsonc-parser`` byte-offset surgery over a
JSON object tree: a dependency range is a **map value**, so a rewrite is
``{keys: ["dependencies", "pkg-b"], value: "^2.0.0"}``. molt replaces that wholesale with
``tomlkit``, and in ``pyproject.toml`` dependencies are a **list of PEP 508 strings**
(``dependencies = ["pkg-b>=1.0.0"]``). A range rewrite is therefore a **substring splice of the
specifier inside the original requirement string**, not a map-key set -- research doc 04 section
2.6, ``roadmap/tech-stack.md`` section 8. Re-serialising a whole
``packaging.requirements.Requirement`` would normalise its spacing and is explicitly wrong.

The two seams under test (P4 shared brief section 8)
-----------------------------------------------------
``edit_toml(text, key_path, value) -> str``
    The direct ``editJson`` analogue: replace the value at ``key_path``, byte-preserving
    everything else. Like upstream it can never *create* a key -- a missing path is a hard error
    (``edit-json.ts:42-44``).

``set_dependency_specifier(text, *, section, name, specifier) -> str``
    The deps-as-list splice primitive, with no upstream counterpart. ``section`` is
    ``("project", "dependencies")``, ``("project", "optional-dependencies", "<extra>")`` or
    ``("dependency-groups", "<group>")`` (PEP 735).

Both are imported from ``molt.apply``; the registry lists the implementation module as
``molt.apply.edit_toml``, so a re-export from the package root is assumed.

The assertion shape
-------------------
Upstream's strongest row is #5, ``expect(result).toEqual(json.replace(old, new))``
(``edit-json.test.ts:76``). That shape is used here almost everywhere as
``assert result == original.replace(old, new)``: it proves the target changed *and* that not one
other byte moved, which is strictly stronger than eyeballing a snapshot -- and, unlike a syrupy
baseline, it can be reviewed in the diff and cannot be silently re-recorded. This module
therefore carries no snapshot assertions (same rationale as ``tests/engine/test_assemble.py``,
which also declined syrupy: a guarded module never runs, so no baseline can be generated).

Contracts this file *chooses* (flagged for owner confirmation)
---------------------------------------------------------------
1. **The specifier region.** A PEP 508 requirement is treated as
   ``<name><extras><gap><specifier><marker>``. The splice replaces exactly the specifier region;
   the name, the extras, the gap before the specifier, and the marker are preserved byte-for-byte.
   Whitespace *inside* the old specifier is not preserved -- the region is replaced wholesale.
   When a requirement has no specifier, the region is the empty span right after ``<extras>``, so
   a specifier can be *inserted* there. That insertion is load-bearing: an unconstrained internal
   dependency must be pinned when the new version is a prerelease (shared brief section 9).
2. **The primitive is strict; the skip rules live in the caller.** Absent section, absent
   dependency name, a non-list target, and a direct reference (``pkg-b @ file:///...``) all raise
   ``molt.errors.MoltKeyPathError`` rather than returning the text unchanged. This mirrors
   upstream, where ``editJson`` is strict and ``version-package.ts:48-102`` does the skipping.
3. **Every entry matching the name is rewritten**, not just the first: a marker-split dependency
   (``pkg-b>=1.0.0 ; python_version < "3.12"`` plus its ``>=`` twin) has two live constraints and
   both go stale together.
4. ``molt.errors.MoltKeyPathError`` is an assumed seam. ``MoltParseError`` already appears in
   ``tests/test_errors.py`` as the changeset-grammar error; it is reused here for malformed TOML.

Divergences pinned as assertions
--------------------------------
- **No leading-operator flattening.** Upstream's ``getVersionRangeType``
  (``version-package.ts:116-125``) keeps only the leading operator, so ``">=1.0.0 <2.0.0"``
  becomes ``">=1.0.4"`` -- the constraint-widening bug, already a 6-row Drop in
  ``tests/versioning/test_deliberately_not_ported.py``. molt carries the whole ``SpecifierSet``:
  ``>=1.0.0,<2.0.0`` becomes ``>=1.0.4,<2.0.0``. Asserted, not just noted.
- **Upstream row 7's empty-input case inverts.** ``allowEmptyContent: false`` makes ``""`` a parse
  error in JSON; an empty TOML document is *valid*, so it becomes a missing-key-path error.
- **Upstream row 7's comment case inverts.** ``disallowComments: true`` makes a commented JSON
  document a parse error; TOML comments are legal and must survive the edit.

research doc 04 sections 2.1-2.6; research README sections 3.3, 3.4 and 4.5.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

pytest.importorskip("molt.apply", reason="build step 5 - apply not yet implemented (TDD target)")
pytest.importorskip(
    "molt.errors", reason="build step 9 - molt.errors not yet implemented (TDD target)"
)

from molt.apply import edit_toml, set_dependency_specifier
from molt.errors import MoltKeyPathError, MoltParseError

pytestmark = pytest.mark.unit

RUNTIME: tuple[str, ...] = ("project", "dependencies")


def deps_doc(*requirements: str, quote: str = '"') -> str:
    """A minimal manifest whose ``[project].dependencies`` array holds ``requirements``."""
    entries = ", ".join(f"{quote}{req}{quote}" for req in requirements)
    return f"[project]\ndependencies = [{entries}]\n"


# ======================================================================================
# Row 1 -- a direct scalar edit, everything else byte-identical
# ======================================================================================

SIMPLE = '[project]\nname = "pkg-a"\nversion = "1.0.0"\n'


def test_updates_a_direct_value() -> None:
    """Row 1 (edit-json.test.ts:4-15) -- the ``[project].version`` write.

    Upstream sets ``version`` to ``"^2.0.0"``, which is a range rather than a version; that is
    only possible because ``editJson`` never validates. The molt analogue writes a real PEP 440
    version, since the one caller is the version field (research doc 04 section 2.3,
    ``index.ts:153``).
    """
    result = edit_toml(SIMPLE, ("project", "version"), "1.1.0")
    assert result == SIMPLE.replace('"1.0.0"', '"1.1.0"')


def test_a_longer_replacement_does_not_disturb_later_bytes() -> None:
    """Row 3's ``2.0.0-longer-than-before.0`` respelled in PEP 440.

    Upstream's point is that a value longer than the one it replaces must not corrupt the offsets
    of subsequent edits. Written as a single edit here; the batching case is below.
    """
    result = edit_toml(SIMPLE, ("project", "version"), "2.0.0rc0")
    assert result == SIMPLE.replace('"1.0.0"', '"2.0.0rc0"')


def test_an_array_element_can_be_addressed_by_index() -> None:
    """``key_path`` is ``Sequence[str | int]``, so a list index is a legal step.

    This is the mechanical equivalent of upstream's nested-map access. It exists for completeness;
    callers should prefer :func:`set_dependency_specifier`, which finds the entry by name and does
    not require the caller to know an index that shifts whenever a dependency is added.
    """
    doc = deps_doc("pkg-b>=1.0.0", "pkg-c~=2.1.0")
    result = edit_toml(doc, ("project", "dependencies", 0), "pkg-b>=1.0.4")
    assert result == doc.replace("pkg-b>=1.0.0", "pkg-b>=1.0.4")


# ======================================================================================
# Row 2 -- THE range-rewrite primitive, across all three dependency sections
# ======================================================================================

#: One manifest carrying the same dependency in all three sections. Each occurrence uses a
#: *different* pinned version so that ``.replace()`` in the expectations is unambiguous, which in
#: turn proves that editing one section leaves the other two untouched.
THREE_SECTIONS = (
    "[project]\n"
    'name = "pkg-a"\n'
    'version = "1.0.0"\n'
    'dependencies = ["pkg-b>=1.0.0", "pkg-c~=2.1.0"]\n'
    "\n"
    "[project.optional-dependencies]\n"
    'plot = ["pkg-b>=1.1.0", "matplotlib>=3.9"]\n'
    "\n"
    "[dependency-groups]\n"
    'dev = ["pkg-b>=1.2.0", "pytest>=9.0"]\n'
)

SECTION_CASES = [
    # (section, old_requirement, new_specifier, new_requirement, why)
    (
        ("project", "dependencies"),
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "PEP 621 runtime deps -- the analogue of upstream's `dependencies` map",
    ),
    (
        ("project", "optional-dependencies", "plot"),
        "pkg-b>=1.1.0",
        ">=1.1.4",
        "pkg-b>=1.1.4",
        "extras -- the closest analogue of npm optionalDependencies (research README 4.5)",
    ),
    (
        ("dependency-groups", "dev"),
        "pkg-b>=1.2.0",
        ">=1.2.4",
        "pkg-b>=1.2.4",
        "PEP 735 dependency-groups -- molt's devDependencies (research doc 04 section 2.6)",
    ),
]


@pytest.mark.parametrize(("section", "old", "specifier", "new", "why"), SECTION_CASES)
def test_updates_a_dependency_specifier_in_each_section(
    section: Sequence[str], old: str, specifier: str, new: str, why: str
) -> None:
    """Row 2 (edit-json.test.ts:17-30) -- the range-rewrite primitive.

    Upstream sets a map value at ``["dependencies", "pkg-b"]``. molt finds the PEP 508 string
    whose distribution name is ``pkg-b`` and splices its specifier, leaving every other byte of
    the document -- including the two sibling sections -- alone.
    """
    result = set_dependency_specifier(
        THREE_SECTIONS, section=section, name="pkg-b", specifier=specifier
    )
    assert result == THREE_SECTIONS.replace(old, new), why


# ======================================================================================
# Row 3 -- multiple edits in one pass (molt: buffer, then flush once)
# ======================================================================================


def test_batched_edits_match_applying_them_one_at_a_time() -> None:
    """Row 3 (edit-json.test.ts:32-49) -- two edits, one output document.

    Upstream takes an operation *list* and splices all of them in a single ``applyEdits`` call.
    The registry seam is single-edit, so molt composes; what has to hold is that composition is
    order-independent and equals the independently-computed expectation. That is precisely the
    property that makes ``apply``'s buffer-then-flush safe (shared brief section 9): every edit
    for a manifest is computed against the same source and none of them can disturb another.
    """
    expected = THREE_SECTIONS.replace('version = "1.0.0"', 'version = "2.0.0rc0"').replace(
        "pkg-b>=1.0.0", "pkg-b>=1.0.4"
    )
    version_first = set_dependency_specifier(
        edit_toml(THREE_SECTIONS, ("project", "version"), "2.0.0rc0"),
        section=RUNTIME,
        name="pkg-b",
        specifier=">=1.0.4",
    )
    dependency_first = edit_toml(
        set_dependency_specifier(
            THREE_SECTIONS, section=RUNTIME, name="pkg-b", specifier=">=1.0.4"
        ),
        ("project", "version"),
        "2.0.0rc0",
    )
    assert version_first == expected
    assert dependency_first == expected


def test_edits_to_three_sections_compose() -> None:
    """The whole-manifest case: a version write plus one splice per dependency section."""
    result = edit_toml(THREE_SECTIONS, ("project", "version"), "1.1.0")
    for section, _old, specifier, _new, _why in SECTION_CASES:
        result = set_dependency_specifier(
            result, section=section, name="pkg-b", specifier=specifier
        )
    expected = (
        THREE_SECTIONS.replace('version = "1.0.0"', 'version = "1.1.0"')
        .replace("pkg-b>=1.0.0", "pkg-b>=1.0.4")
        .replace("pkg-b>=1.1.0", "pkg-b>=1.1.4")
        .replace("pkg-b>=1.2.0", "pkg-b>=1.2.4")
    )
    assert result == expected


# ======================================================================================
# Rows 4-5 -- formatting fidelity. `result == original.replace(old, new)` throughout.
# ======================================================================================

#: Upstream row 5's "bizarre formatting" document, restated in TOML with a nested key path.
BIZARRE = '\n\n\n[a.b]\n\n\nc\t=\t\t"1.0.0"\n\n\n\n'

SCALAR_FIDELITY_CASES = [
    # (document, key_path, value, old, new, why)
    (
        '[project]\nname="pkg-a"\nversion="1.0.0"\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 4: no whitespace around `=` at all",
    ),
    (
        '[project]\nname   =   "pkg-a"\nversion\t=\t"1.0.0"\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 4: run-on spaces and a tab around `=`",
    ),
    (
        BIZARRE,
        ("a", "b", "c"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 5: extreme leading/interior/trailing blank lines and tabs, nested key path",
    ),
    (
        '[project]\n# the released version -- keep this comment\nversion = "1.0.0"\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "comment on the line above the target survives",
    ),
    (
        '[project]\nversion = "1.0.0"  # bumped by molt\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "comment beside the target survives, and keeps its two-space gap",
    ),
    (
        '[project]\n\tversion = "1.0.0"\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 2 of index.test.ts (tab indentation): TOML indentation is free trivia, keep it",
    ),
    (
        '[project]\nversion = "1.0.0"',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 3 of index.test.ts: an absent trailing newline is not added",
    ),
    (
        '[project]\nversion = "1.0.0"\n\n\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "row 4 of index.test.ts: existing trailing newlines are not removed",
    ),
    (
        '[project]\r\nname = "pkg-a"\r\nversion = "1.0.0"\r\n',
        ("project", "version"),
        "1.1.0",
        '"1.0.0"',
        '"1.1.0"',
        "CRLF: molt is Windows-correct from day one, so line endings survive a rewrite",
    ),
    (
        "[project]\nversion = '1.0.0'\n",
        ("project", "version"),
        "1.1.0",
        "'1.0.0'",
        "'1.1.0'",
        "TOML literal string: quoting style is preserved, so `'` is NOT rewritten to `\"`",
    ),
]


@pytest.mark.parametrize(("doc", "key_path", "value", "old", "new", "why"), SCALAR_FIDELITY_CASES)
def test_scalar_edit_changes_only_the_target_bytes(
    doc: str, key_path: Sequence[str | int], value: str, old: str, new: str, why: str
) -> None:
    """Rows 4-5 (edit-json.test.ts:51-77) plus the four ``index.test.ts:153-259`` shapes.

    research doc 04 section 2.2 lists what upstream test-locks; section 2.6 warns that tomlkit is
    *not* byte-perfect in every case and asks for exactly these golden shapes. The literal-string
    row is the sharp one: a naive ``document["project"]["version"] = value`` silently rewrites
    ``'1.0.0'`` to ``"1.0.0"``, so the implementation has to carry the original quote style over.
    """
    assert edit_toml(doc, key_path, value) == doc.replace(old, new), why


ARRAY_FIDELITY_CASES = [
    # (document, old_requirement, new_specifier, new_requirement, why)
    (
        "[project]\ndependencies = [\n"
        "    # the core runtime\n"
        '    "pkg-b>=1.0.0",  # pin: see issue 42\n'
        '    "pkg-c",\n'
        "]\n",
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "multi-line array is not collapsed; both the standalone and the trailing comment survive",
    ),
    (
        '[project]\ndependencies = [\n\t"pkg-b  >=  1.0.0" ,\n]\n',
        "pkg-b  >=  1.0.0",
        ">=1.0.4",
        "pkg-b  >=1.0.4",
        "tab indent and a space before the array comma are trivia and stay put",
    ),
    (
        "[project]\ndependencies = ['pkg-b>=1.0.0']\n",
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "a literal-string array entry keeps its single quotes",
    ),
    (
        '[project]\r\ndependencies = ["pkg-b>=1.0.0"]\r\n',
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "CRLF survives a dependency splice too",
    ),
    (
        '[project]\ndependencies = ["pkg-b>=1.0.0"]',
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "no trailing newline before the splice, none after",
    ),
    (
        '[project]\ndependencies = ["pkg-b>=1.0.0",]\n',
        "pkg-b>=1.0.0",
        ">=1.0.4",
        "pkg-b>=1.0.4",
        "a trailing comma inside the array is legal TOML and is not normalised away",
    ),
]


@pytest.mark.parametrize(("doc", "old", "specifier", "new", "why"), ARRAY_FIDELITY_CASES)
def test_dependency_splice_changes_only_the_target_bytes(
    doc: str, old: str, specifier: str, new: str, why: str
) -> None:
    """Formatting fidelity for the deps-as-list surface, which upstream has no rows for.

    Comment preservation through a dependency rewrite is called out as a net-new molt test in the
    group file's "molt-specific NEW tests" section; ``roadmap/tech-stack.md`` section 8 is blunt
    about why (a comment-eating rewrite of every ``pyproject.toml`` is an instant-uninstall bug).
    """
    result = set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=specifier)
    assert result == doc.replace(old, new), why


def test_a_uv_workspace_source_table_is_not_touched_by_a_splice() -> None:
    """``[tool.uv.sources]`` is the ``workspace:`` protocol analogue and is never rewritten.

    research doc 04 section 2.6 and ``website/docs/guide/dependency-propagation.md``: the
    constraint lives in the PEP 508 string, the workspace marker lives in
    ``[tool.uv.sources]``. Only the former moves.
    """
    doc = (
        "[project]\n"
        'name = "acme-cli"\n'
        'version = "0.5.0"\n'
        'dependencies = ["acme-core==1.2.0"]\n'
        "\n"
        "[tool.uv.sources]\n"
        "acme-core = { workspace = true }\n"
    )
    result = set_dependency_specifier(doc, section=RUNTIME, name="acme-core", specifier="==1.3.0")
    assert result == doc.replace("acme-core==1.2.0", "acme-core==1.3.0")


@pytest.mark.parametrize(
    "doc", [case[0] for case in SCALAR_FIDELITY_CASES], ids=lambda d: repr(d)[:40]
)
def test_writing_a_value_back_over_itself_is_a_no_op(doc: str) -> None:
    """Upstream writes ``version`` even for a ``none`` release, expecting a byte-identical file.

    research doc 04 section 2.3 (``index.ts:153``). If an edit that changes nothing still reflows
    the document, ``molt version`` produces noise diffs on every unrelated package.
    """
    key_path = ("a", "b", "c") if "[a.b]" in doc else ("project", "version")
    assert edit_toml(doc, key_path, "1.0.0") == doc


# ======================================================================================
# Row 6 -- a key path that does not exist is a hard error
# ======================================================================================

MISSING_PATH_CASES = [
    # (document, key_path, expected_message_fragment, why)
    (
        '[project]\nname = "pkg-a"\n',
        ("project", "version"),
        "project.version",
        "row 6 verbatim: the leaf is missing. `edit_toml` can never CREATE a key, so a manifest "
        "with no [project].version is a loud failure rather than a silent insert "
        "(edit-json.ts:42-44; research doc 04 section 2.1)",
    ),
    (
        '[project]\nname = "pkg-a"\n',
        ("tool", "molt", "version"),
        "tool.molt.version",
        "an intermediate table is missing, not just the leaf",
    ),
    (
        '[project]\nname = "pkg-a"\n',
        ("project", "name", "nested"),
        "project.name.nested",
        "the path runs through a scalar, so there is nothing left to index into",
    ),
    (
        '[project]\ndependencies = ["pkg-b>=1.0.0"]\n',
        ("project", "dependencies", 7),
        "project.dependencies.7",
        "an array index past the end of the list",
    ),
]


@pytest.mark.parametrize(("doc", "key_path", "expected", "why"), MISSING_PATH_CASES)
def test_missing_key_path_raises(
    doc: str, key_path: Sequence[str | int], expected: str, why: str
) -> None:
    """Row 6 (edit-json.test.ts:79-90) -- upstream message ``Key path "version" not found``.

    Upstream asserts the **message**, not just the class (``edit-json.test.ts:89``), and it has to:
    the caller is ``version-package``, which edits a path it computed, so an error that does not
    name the path it failed on is unactionable. The message must carry the dotted path -- including
    the failing *step*, not merely the root -- for every shape of missing path.
    """
    with pytest.raises(MoltKeyPathError, match=re.escape(expected)) as excinfo:
        edit_toml(doc, key_path, "1.1.0")
    assert expected in str(excinfo.value), why


def test_missing_key_path_error_names_the_path() -> None:
    """The dotted path has to reach the user; ``version-package`` edits are otherwise opaque."""
    with pytest.raises(MoltKeyPathError, match=r"project\.version"):
        edit_toml('[project]\nname = "pkg-a"\n', ("project", "version"), "1.1.0")


SPLICE_TARGET_CASES = [
    # (document, section, name, expected_message_fragment, why)
    (
        deps_doc("pkg-c~=2.1.0"),
        RUNTIME,
        "pkg-b",
        "pkg-b",
        "the dependency is simply not declared in this section, so the message must name the "
        "dependency -- the section it looked in is fine",
    ),
    (
        deps_doc("pkg-b>=1.0.0"),
        ("dependency-groups", "dev"),
        "pkg-b",
        "dependency-groups.dev",
        "the section itself does not exist -- pkg-b is a runtime dep, not a dev dep -- so the "
        "message must name the section, not the dependency",
    ),
    (
        deps_doc("pkg-b>=1.0.0"),
        ("project", "optional-dependencies", "plot"),
        "pkg-b",
        "project.optional-dependencies.plot",
        "the extra does not exist; the message names the extra, which is the part that is wrong",
    ),
    (
        '[project]\nversion = "1.0.0"\n',
        ("project", "version"),
        "pkg-b",
        "project.version",
        "the target is a scalar, not a dependency list",
    ),
]


@pytest.mark.parametrize(("doc", "section", "name", "expected", "why"), SPLICE_TARGET_CASES)
def test_splice_target_that_does_not_exist_raises(
    doc: str, section: Sequence[str], name: str, expected: str, why: str
) -> None:
    """Contract 2: the primitive is strict, so a caller bug cannot pass as a no-op.

    doc 04 section 2.4's skip rules ("dep absent" first among them) are enumerated at
    ``version-package.ts:50-52`` -- in the *caller*. ``editJson`` itself always raised, and
    asserted its message (``edit-json.test.ts:89``).

    The four rows fail for **different** reasons, and the message has to say which: a missing
    dependency inside a section that exists is a different caller bug from a missing section, and a
    single opaque ``MoltKeyPathError`` would make them indistinguishable at the call site.
    """
    with pytest.raises(MoltKeyPathError, match=re.escape(expected)) as excinfo:
        set_dependency_specifier(doc, section=section, name=name, specifier=">=1.0.4")
    assert expected in str(excinfo.value), why


# ======================================================================================
# Row 7 -- malformed input. Two of upstream's three inputs invert in TOML.
# ======================================================================================

MALFORMED_CASES = [
    # (document, why)
    ("[[[\n", "unterminated table header -- the direct analogue of upstream's `{{{`"),
    ("[project]\nversion =\n", "a key with no value"),
    ('[project]\nname = "a"\nname = "b"\n', "TOML forbids redefining a key"),
    ('[project]\nversion = "1.0.0"\n[project]\n', "TOML forbids redefining a table"),
    ('[project]\nversion = "1.0.0\n', "unterminated basic string"),
    ("[project]\nversion = 1.0.0\n", "bare `1.0.0` is not a TOML value (it parses as a number)"),
    ('version = "1.0.0"\n[project\n', "unterminated table header partway through the document"),
]


#: Upstream matches ``/Failed to parse JSON/`` (``edit-json.test.ts:105``); the TOML analogue is
#: the same sentence with the format name swapped. A parse failure has to *say* it is one -- the
#: CLI renders "your pyproject.toml is malformed" differently from "molt looked for a key that is
#: not there", and ``MoltParseError`` vs ``MoltKeyPathError`` is the only thing that distinguishes
#: them (see :func:`test_an_empty_document_is_a_missing_key_path_not_a_parse_error`).
PARSE_FAILURE_MESSAGE = r"(?i)failed to parse"


@pytest.mark.parametrize(("doc", "why"), MALFORMED_CASES)
def test_malformed_toml_raises_a_parse_error(doc: str, why: str) -> None:
    """Row 7 (edit-json.test.ts:92-107) -- upstream matched ``/Failed to parse JSON/``.

    ``MoltParseError`` is the seam already declared for changeset-grammar failures
    (``tests/test_errors.py``); malformed manifests reuse it so the CLI has one parse-failure
    class to render.
    """
    with pytest.raises(MoltParseError, match=PARSE_FAILURE_MESSAGE) as excinfo:
        edit_toml(doc, ("project", "version"), "1.1.0")
    assert "parse" in str(excinfo.value).lower(), why


@pytest.mark.parametrize(("doc", "why"), MALFORMED_CASES)
def test_malformed_toml_raises_from_the_splice_primitive_too(doc: str, why: str) -> None:
    """Both seams parse the same document, so both have to reject the same inputs."""
    with pytest.raises(MoltParseError, match=PARSE_FAILURE_MESSAGE) as excinfo:
        set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=">=1.0.4")
    assert "parse" in str(excinfo.value).lower(), why


def test_an_empty_document_is_a_missing_key_path_not_a_parse_error() -> None:
    """DIVERGENCE from row 7's second input.

    ``parseTree(json, errors, {allowEmptyContent: false})`` (``edit-json.ts:24-28``) makes ``""``
    a JSON *parse* failure. An empty TOML document is perfectly valid -- it is a document with no
    tables -- so the failure moves one layer out and becomes the row-6 error instead. The
    behaviour under test is unchanged (nothing is written, something is raised); only the class
    differs, and a caller that catches only ``MoltParseError`` here would crash.
    """
    with pytest.raises(MoltKeyPathError):
        edit_toml("", ("project", "version"), "1.1.0")


def test_a_document_full_of_comments_parses_and_keeps_every_comment() -> None:
    """DIVERGENCE from row 7's third input, ``{//comment\\n"version":"1.0.0"}``.

    ``disallowComments: true`` makes that a hard error in JSON. Comments are first-class in TOML
    and are the single strongest reason molt must use tomlkit at all
    (``roadmap/tech-stack.md`` section 8), so the same document must round-trip intact.
    """
    doc = (
        "# molt manifest\n"
        "\n"
        "[project]  # inline table comment\n"
        "# the released version\n"
        'version = "1.0.0"  # bumped by molt\n'
        "\n"
        "# trailing note\n"
    )
    assert edit_toml(doc, ("project", "version"), "1.1.0") == doc.replace('"1.0.0"', '"1.1.0"')


# ======================================================================================
# molt-native: the PEP 440 operator set, and the compound sets upstream destroys
# ======================================================================================

OPERATOR_CASES = [
    # (old_requirement, new_specifier, new_requirement, why)
    ("pkg-b==1.0.0", "==1.0.4", "pkg-b==1.0.4", "exact pin -- the `workspace:*` analogue"),
    ("pkg-b>=1.0.0", ">=1.0.4", "pkg-b>=1.0.4", "the most common internal pin"),
    ("pkg-b>1.0.0", ">1.0.4", "pkg-b>1.0.4", "strict lower bound"),
    ("pkg-b<=2.0.0", "<=3.0.0", "pkg-b<=3.0.0", "upper bound only -- upstream has no `<=` case"),
    ("pkg-b<2.0.0", "<3.0.0", "pkg-b<3.0.0", "`<` is the operator getVersionRangeType forgot"),
    ("pkg-b~=1.2.0", "~=1.2.1", "pkg-b~=1.2.1", "PEP 440 compatible-release; molt's `~` analogue"),
    ("pkg-b!=1.0.0", "!=1.0.4", "pkg-b!=1.0.4", "exclusion -- no semver counterpart at all"),
    ("pkg-b===1.0.0", "===1.0.4", "pkg-b===1.0.4", "arbitrary equality, PEP 440 only"),
    ("pkg-b==1.0.*", "==1.1.*", "pkg-b==1.1.*", "prefix match, PEP 440 only"),
    (
        "pkg-b>=1.0.0,<2.0.0",
        ">=1.0.4,<2.0.0",
        "pkg-b>=1.0.4,<2.0.0",
        "caret-style window: the lower bound moves, the upper bound is preserved",
    ),
    (
        "pkg-b>=1.2.0,<2.0.0",
        ">=2.0.0,<3.0.0",
        "pkg-b>=2.0.0,<3.0.0",
        "out-of-range major bump: both bounds move, or the set would be unsatisfiable "
        "(website/docs/guide/dependency-propagation.md)",
    ),
    (
        "pkg-b>=1.0.0,!=1.0.2,<2.0.0",
        ">=1.0.4,!=1.0.2,<2.0.0",
        "pkg-b>=1.0.4,!=1.0.2,<2.0.0",
        "three comparators, all preserved -- a known-bad release stays excluded",
    ),
    (
        "pkg-b >= 1.0.0, < 2.0.0",
        ">=1.0.4,<2.0.0",
        "pkg-b >=1.0.4,<2.0.0",
        "spaces inside the specifier region are replaced wholesale; the gap after the name is not",
    ),
]


@pytest.mark.parametrize(("old", "specifier", "new", "why"), OPERATOR_CASES)
def test_the_whole_pep_440_operator_set_splices(
    old: str, specifier: str, new: str, why: str
) -> None:
    """molt-native. The group file only ever shows npm's `^` and `~`; PEP 440 has eight operators.

    ``set_dependency_specifier`` is deliberately agnostic about *which* specifier it writes -- the
    release engine computes that. What is pinned here is that whatever the engine computes lands
    verbatim, for every operator, including the ones ``getVersionRangeType`` cannot represent.
    """
    doc = deps_doc(old)
    result = set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=specifier)
    assert result == doc.replace(old, new), why


def test_a_compound_range_is_never_flattened_to_its_leading_operator() -> None:
    """The constraint-widening bug, asserted as a negative.

    ``getVersionRangeType`` (``version-package.ts:116-125``) returns only the leading operator, so
    upstream rewrites ``">=1.0.0 <2.0.0"`` to ``">=1.0.4"`` and silently drops the upper bound --
    research README section 3.3, and already a 6-row Drop in
    ``tests/versioning/test_deliberately_not_ported.py``. molt carries the whole ``SpecifierSet``.
    """
    doc = deps_doc("pkg-b>=1.0.0,<2.0.0")
    result = set_dependency_specifier(
        doc, section=RUNTIME, name="pkg-b", specifier=">=1.0.4,<2.0.0"
    )
    assert result == doc.replace("pkg-b>=1.0.0,<2.0.0", "pkg-b>=1.0.4,<2.0.0")
    assert '"pkg-b>=1.0.4"' not in result, "the upper bound must not be dropped"
    assert "<2.0.0" in result


def test_a_snapshot_release_pins_exactly_and_drops_every_range_modifier() -> None:
    """Group file index row 16 (``index.test.ts:963-1020``), at the primitive level.

    Snapshot mode replaces the entire specifier set with an exact pin; the splice must not try to
    preserve the operator that was there. The version is spelled in PEP 440 (a local version
    segment), not as npm's ``0.0.0-canary-<sha>``.
    """
    doc = deps_doc("pkg-b>=1.0.0,<2.0.0")
    result = set_dependency_specifier(
        doc, section=RUNTIME, name="pkg-b", specifier="==0.0.0+canary.20211213000730"
    )
    assert result == doc.replace("pkg-b>=1.0.0,<2.0.0", "pkg-b==0.0.0+canary.20211213000730")


# ======================================================================================
# molt-native: everything outside the specifier region survives
# ======================================================================================

STRUCTURE_CASES = [
    # (old_requirement, new_specifier, new_requirement, why)
    ("pkg-b[cli]>=1.0.0", ">=1.0.4", "pkg-b[cli]>=1.0.4", "a single extra"),
    ("pkg-b[cli,plot]>=1.0.0", ">=1.0.4", "pkg-b[cli,plot]>=1.0.4", "several extras"),
    (
        'pkg-b>=1.0.0 ; python_version < "3.12"',
        ">=1.0.4",
        'pkg-b>=1.0.4 ; python_version < "3.12"',
        "environment marker, spaced -- markers are load-bearing and must survive verbatim",
    ),
    (
        'pkg-b[cli]>=1.0.0;python_version<"3.12"',
        ">=1.0.4",
        'pkg-b[cli]>=1.0.4;python_version<"3.12"',
        "extras plus an unspaced marker",
    ),
    (
        "pkg-b  >=  1.0.0",
        ">=1.0.4",
        "pkg-b  >=1.0.4",
        "the gap between name and specifier is preserved; the gap inside the specifier is not",
    ),
    (
        "  pkg-b>=1.0.0  ",
        ">=1.0.4",
        "  pkg-b>=1.0.4  ",
        "padding inside the TOML string itself is left alone",
    ),
    (
        'pkg-b[cli]  >=1.0.0  ; python_version < "3.12" and sys_platform == "win32"',
        ">=1.0.4",
        'pkg-b[cli]  >=1.0.4  ; python_version < "3.12" and sys_platform == "win32"',
        "all four regions at once: name, extras, gap, compound marker",
    ),
]


@pytest.mark.parametrize(("old", "specifier", "new", "why"), STRUCTURE_CASES)
def test_only_the_specifier_region_of_a_requirement_changes(
    old: str, specifier: str, new: str, why: str
) -> None:
    """molt-native. Contract 1 -- research doc 04 section 2.6 and tech-stack section 8.

    Round-tripping through ``packaging.requirements.Requirement`` would normalise all of this away
    (``str(Requirement('pkg-b[cli] >= 1.0 ; python_version < "3.12"'))`` collapses the spacing), so
    the implementation has to splice the substring instead of re-serialising.
    """
    doc = deps_doc(old, quote="'")
    result = set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=specifier)
    assert result == doc.replace(old, new), why


UNCONSTRAINED_CASES = [
    # (old_requirement, new_specifier, new_requirement, why)
    ("pkg-b", "==1.1.0rc0", "pkg-b==1.1.0rc0", "the bare-name case -- the analogue of npm `*`"),
    ("pkg-b[cli]", "==1.1.0rc0", "pkg-b[cli]==1.1.0rc0", "inserted after the extras, not before"),
    (
        'pkg-b ; python_version < "3.12"',
        "==1.1.0rc0",
        'pkg-b==1.1.0rc0 ; python_version < "3.12"',
        "inserted before the marker, and the gap in front of `;` is preserved",
    ),
]


@pytest.mark.parametrize(("old", "specifier", "new", "why"), UNCONSTRAINED_CASES)
def test_a_specifier_can_be_inserted_where_there_was_none(
    old: str, specifier: str, new: str, why: str
) -> None:
    """Group file index row 6, inverted -- and this is why the primitive must support insertion.

    An unconstrained dependency is normally left alone (``version-package.ts:93-102``,
    ``molt.versioning.is_unconstrained``). The exception is a prerelease new version: PEP 440
    resolvers do not install prereleases without a per-package opt-in, so leaving ``pkg-b``
    unpinned would produce an uninstallable tree. The caller decides; the primitive has to be
    able to carry it out (shared brief section 9).
    """
    doc = deps_doc(old, quote="'")
    result = set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=specifier)
    assert result == doc.replace(old, new), why


DIRECT_REFERENCE_CASES = [
    ("pkg-b @ file:///C:/src/pkg-b", "local path dep -- upstream's `file:` skip, in PEP 508 form"),
    ("pkg-b @ git+https://example.invalid/pkg-b.git@v1", "VCS dep -- upstream's `link:` analogue"),
    (
        "pkg-b[cli] @ https://example.invalid/pkg_b-1.0.0-py3-none-any.whl",
        "direct URL with extras -- the analogue of an npm dist-tag: not a range at all",
    ),
]


@pytest.mark.parametrize(("requirement", "why"), DIRECT_REFERENCE_CASES)
def test_a_direct_reference_has_no_specifier_to_rewrite(requirement: str, why: str) -> None:
    """Group file index rows 13 and 21 at the primitive level, under contract 2.

    PEP 508 forbids combining ``@ <url>`` with a version specifier, so there is no region to
    splice and no correct edit to make. The caller skips these (doc 04 section 2.4 rules 1 and 4);
    if it ever fails to, the primitive must refuse loudly rather than corrupt the requirement.

    The message must say **why**: "there is no specifier region here" is a different fact from
    "that key path is not in this document", and a caller that wants to turn the refusal back into
    a skip has to be able to tell them apart. That is also the argument for giving this case an
    error class of its own -- flagged for the owner; today it reuses ``MoltKeyPathError`` and is
    distinguished only by its message.
    """
    doc = deps_doc(requirement, quote="'")
    with pytest.raises(MoltKeyPathError, match="direct reference") as excinfo:
        set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=">=1.0.4")
    assert "pkg-b" in str(excinfo.value), why


# ======================================================================================
# molt-native: name matching (PEP 503) and picking the right entry
# ======================================================================================

NAME_MATCH_CASES = [
    # (declared_requirement, requested_name, new_requirement, why)
    (
        "Foo_Bar>=1.0.0",
        "foo-bar",
        "Foo_Bar>=1.0.4",
        "PEP 503 normalises `_` and `-` alike; the author's literal spelling stays on disk "
        "(Session 2 decision 4 -- normalise at comparison time, never at parse time)",
    ),
    ("foo.bar>=1.0.0", "foo-bar", "foo.bar>=1.0.4", "`.` normalises to `-` too"),
    ("FOO--BAR>=1.0.0", "foo-bar", "FOO--BAR>=1.0.4", "runs of `-_.` collapse to a single `-`"),
    ("pkg-b>=1.0.0", "PKG_B", "pkg-b>=1.0.4", "the requested name is normalised as well"),
]


@pytest.mark.parametrize(("declared", "requested", "new", "why"), NAME_MATCH_CASES)
def test_dependency_names_match_under_pep_503_normalization(
    declared: str, requested: str, new: str, why: str
) -> None:
    """molt-native. Nothing like this exists upstream -- npm names are matched literally."""
    doc = deps_doc(declared)
    result = set_dependency_specifier(doc, section=RUNTIME, name=requested, specifier=">=1.0.4")
    assert result == doc.replace(declared, new), why


PREFIX_COLLISION_CASES = [
    # (requested_name, old_requirement, new_requirement, why)
    ("pkg", "pkg>=1.0.0", "pkg>=1.0.4", "the shorter name must not be matched by the longer entry"),
    ("pkg-extra", "pkg-extra>=2.0.0", "pkg-extra>=1.0.4", "and the longer name hits only itself"),
]


@pytest.mark.parametrize(("requested", "old", "new", "why"), PREFIX_COLLISION_CASES)
def test_a_name_that_prefixes_another_does_not_splice_the_wrong_entry(
    requested: str, old: str, new: str, why: str
) -> None:
    """molt-native. A naive ``line.startswith(name)`` splices ``pkg-extra`` when asked for ``pkg``.

    Matching has to run through the PEP 508 grammar (parse the requirement, compare canonical
    names), not through string prefixes.
    """
    doc = deps_doc("pkg>=1.0.0", "pkg-extra>=2.0.0")
    result = set_dependency_specifier(doc, section=RUNTIME, name=requested, specifier=">=1.0.4")
    assert result == doc.replace(old, new), why


def test_every_entry_for_the_same_dependency_is_rewritten() -> None:
    """Contract 3. A marker-split dependency declares two live constraints on one package.

    Rewriting only the first would leave the ``python_version >= "3.12"`` branch pinned to a
    version that no longer exists in the workspace -- a broken install on exactly one interpreter,
    which is the worst kind of bug to find later.
    """
    doc = deps_doc(
        'pkg-b>=1.0.0 ; python_version < "3.12"',
        'pkg-b>=1.0.0 ; python_version >= "3.12"',
        quote="'",
    )
    result = set_dependency_specifier(doc, section=RUNTIME, name="pkg-b", specifier=">=1.0.4")
    assert result == doc.replace("pkg-b>=1.0.0", "pkg-b>=1.0.4")
    assert result.count("pkg-b>=1.0.4") == 2


def test_a_pep_735_include_group_entry_is_skipped() -> None:
    """``[dependency-groups]`` arrays may hold ``{include-group = ...}`` inline tables (PEP 735).

    Those are not PEP 508 strings. Treating one as a requirement would either crash or, worse,
    rewrite it; it has to be stepped over and left byte-identical.
    """
    doc = (
        "[dependency-groups]\n"
        'test = ["pytest>=9.0"]\n'
        'dev = [{ include-group = "test" }, "pkg-b>=1.0.0"]\n'
    )
    result = set_dependency_specifier(
        doc, section=("dependency-groups", "dev"), name="pkg-b", specifier=">=1.0.4"
    )
    assert result == doc.replace("pkg-b>=1.0.0", "pkg-b>=1.0.4")
    assert '{ include-group = "test" }' in result


# ======================================================================================
# Properties -- the invariants a substring splice is supposed to have
# ======================================================================================

_NAMES = st.sampled_from(["pkg-b", "Pkg_B", "pkg.b", "b"])
_EXTRAS = st.sampled_from(["", "[cli]", "[cli,plot]"])
_GAPS = st.sampled_from(["", " ", "  ", "\t"])
_SPECIFIERS = st.sampled_from(
    [">=1.0.0", "==1.0.0", "~=1.2.0", ">=1.0.0,<2.0.0", "!=1.0.0,>=0.9", "<2.0.0"]
)
_MARKERS = st.sampled_from(
    ["", ' ; python_version < "3.12"', ';sys_platform=="win32"', '  ;  extra == "cli"']
)
_TRIVIA = st.sampled_from(["", "\n", "\n\n", "  # a comment\n"])


@pytest.mark.property
@given(
    name=_NAMES,
    extras=_EXTRAS,
    gap=_GAPS,
    old_specifier=_SPECIFIERS,
    new_specifier=_SPECIFIERS,
    marker=_MARKERS,
    prefix=_TRIVIA,
    suffix=_TRIVIA,
)
def test_a_splice_changes_exactly_the_specifier_region(
    name: str,
    extras: str,
    gap: str,
    old_specifier: str,
    new_specifier: str,
    marker: str,
    prefix: str,
    suffix: str,
) -> None:
    """Every byte outside the matched specifier region is invariant.

    The expectation is built structurally rather than by diffing, so this cannot pass vacuously:
    the only difference permitted between input and output is ``old_specifier`` -> ``new_specifier``
    at its own position. TOML literal strings (``'...'``) are used for the array entries because
    markers contain double quotes -- which also makes every example an assertion that tomlkit's
    default re-quoting is suppressed.
    """
    old_requirement = f"{name}{extras}{gap}{old_specifier}{marker}"
    new_requirement = f"{name}{extras}{gap}{new_specifier}{marker}"
    doc = f"[project]{prefix}\ndependencies = ['{old_requirement}']{suffix}\n"
    result = set_dependency_specifier(
        doc, section=RUNTIME, name="pkg-b" if name != "b" else "b", specifier=new_specifier
    )
    assert result == doc.replace(old_requirement, new_requirement)


@pytest.mark.property
@given(
    name=_NAMES,
    extras=_EXTRAS,
    gap=_GAPS,
    old_specifier=_SPECIFIERS,
    new_specifier=_SPECIFIERS,
    marker=_MARKERS,
)
def test_splicing_a_specifier_back_restores_the_original_bytes(
    name: str,
    extras: str,
    gap: str,
    old_specifier: str,
    new_specifier: str,
    marker: str,
) -> None:
    """Round-trip idempotence: ``splice(splice(doc, b), a) == doc`` when ``a`` was the original.

    A splice that is not invertible is a splice that has quietly reformatted something. This is
    the cheapest available proof that ``molt version`` run twice with the same plan produces the
    same file -- the other half of the atomicity story (shared brief section 9).
    """
    requirement = f"{name}{extras}{gap}{old_specifier}{marker}"
    doc = f"[project]\ndependencies = ['{requirement}', 'other-pkg>=1.0']\n"
    requested = "pkg-b" if name != "b" else "b"
    once = set_dependency_specifier(doc, section=RUNTIME, name=requested, specifier=new_specifier)
    twice = set_dependency_specifier(once, section=RUNTIME, name=requested, specifier=old_specifier)
    assert twice == doc
