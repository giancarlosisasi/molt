"""Conformance tests for molt's config parsing, normalization and validation.

Ported from ``roadmap/research/test-suite/03-config-changeset-io.md``, the
``packages/config/src/parse.test.ts`` section (57 rows), which was extracted from changesets
v3.0.0-next.9 (`parse.test.ts:1-652`, schema `config.ts:45-200`, rules `rules.ts:22-204`).

Molt reshapes this surface for PyPI, so most rows are *adapted*, not ported verbatim. The
adaptations below are the load-bearing ones; each is repeated in the docstring of the test that
pins it.

Pinned API contract (TDD target - ``molt.config`` does not exist yet)
--------------------------------------------------------------------
- ``parse_config(data, *, package_names=()) -> (config, warnings, errors)``. The
  ``package_names`` keyword is an addition to the one-argument signature named in the phase
  brief: glob expansion and the group-existence rules cannot run without the workspace's package
  names, and upstream's ``validateConfig(json, packages)`` takes them too (`parse.ts:25`). It
  carries names only; rules that need the dependency graph go through ``load_config``.
- ``load_config(start) -> (config, warnings, errors)`` resolves the workspace root by walking up
  from ``start`` (research doc 03 row 2; website docs ``config/config-file.md``).
- Success returns ``errors == []`` / ``warnings == []`` (empty lists), not upstream's
  ``undefined`` (`parse.ts:9-19`). Any non-empty ``errors`` means ``config is None``, which does
  mirror upstream's ``ParseResult`` union.
- Errors are pydantic-shaped records carrying a structured ``loc`` (tech-stack section 6:
  ``.errors()`` ``loc`` replaces valibot's ``getDotPath``). Tests assert ``loc``, never
  changesets' English prose, which does not port.

Deliberate divergences asserted here
------------------------------------
- ``fixed``/``linked`` globs ARE expanded. Upstream stores them verbatim and never expands them,
  which is upstream bug #1 in research README section 3.4; we do not port that bug.
- Duplicate detection therefore runs on *resolved* names, and each duplicate produces exactly one
  error - upstream's ``noDuplicateFixed/LinkedPackages`` re-emits every duplicate once per
  subsequent group (research README section 3.4, last row).
- An ``ignore`` pattern that matches nothing still resolves to ``[]`` with no error (row 24), but
  molt *warns* instead of dropping it silently - the config-level instance of the
  should-skip-package silent skip in upstream issue #2101 (research doc 08 section D.2 / item 4).
- Scoped npm names (``@pkg/a``) have no PyPI analogue (research README section 4.5: flat global
  namespace), so the upstream glob fixtures are respelled with flat families (``acme-*``,
  ``pkg-other-a``). Because the namespace is flat there is no scope boundary for ``*`` to stop
  at, so ``pkg-*`` captures ``pkg-other-a`` where upstream's ``@pkg*`` matched nothing.
- Package-name matching normalizes **both** pattern and candidate per PEP 503 before comparing
  (research doc 02 sections 12.4/12.5); the workspace's own spelling is what lands on ``Config``.
- ``format`` defaults to ``false``, not upstream's ``"auto"``: molt emits correct, deterministic
  Markdown with no formatter pass (research README section 5 item 13; research doc 02 section
  12.1 "Recommend ``format: "mdformat" | false``, default ``false`` (do not shell out to Node)";
  website docs ``config/options.md:95``). The group file's inline default object (line 40) shows
  ``"auto"`` because it transcribes *upstream's* default, annotated Adapt.
- Dropped keys (``access``, ``privatePackages.tag``, the ``___experimentalUnsafeOptions`` peer
  sibling) are not modelled; see ``test_deliberately_not_ported.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.config", reason="build step 3 - config not yet implemented (TDD target)")

from molt.config import Config, default_config, load_config, parse_config
from molt.config.parse import _JS_FORMATTERS
from molt.config.rules import SNAPSHOT_PLACEHOLDERS

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion
    from tests.conftest import ProjectBuilder


# --------------------------------------------------------------------------------------
# Helpers - keep the CASES tables legible and keep assertions off changesets' prose
# --------------------------------------------------------------------------------------

# Upstream's `it.each` default (`parse.test.ts:369`).
DEFAULT_PKGS: list[str] = ["pkg-a", "pkg-b"]
# Upstream uses `@pkg/*` / `@pkg-other/*` scopes here (`parse.test.ts:238-245`). PyPI has no
# scopes, so the families are flat prefixes instead. `pkg-other-a` stands in for `@pkg-other/a`,
# upstream's deliberate near-miss for `@pkg/*`: there the `/` is a path separator picomatch will
# not cross, so `@pkg*` matched nothing (research doc 02 section 4.2). In a flat namespace there
# is no boundary to stop at and `pkg-*` DOES capture it - see
# `test_a_glob_crosses_what_would_have_been_a_scope_boundary`.
GLOB_PKGS: list[str] = ["pkg-a", "pkg-b", "pkg-other-a", "acme-a", "acme-b", "other-a", "other-b"]
IGNORE_PKGS: list[str] = ["pkg-a", "pkg-b", "acme-a", "acme-b"]

# Keys molt refuses to model at all (research README section 4.4).
DROPPED_KEYS: tuple[str, ...] = (
    "access",
    "tag",
    "onlyUpdatePeerDependentsWhenOutOfRange",
    "___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH",
)


def _jsonish(value: Any) -> Any:
    """Normalize tuples to lists so a table row can spell a 2-tuple field as a list.

    The *shape* of ``changelog``/``commit`` (a ref plus an options mapping) is the pin; whether
    the model stores it as a tuple or a list is not.
    """
    if isinstance(value, Mapping):
        return {key: _jsonish(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish(item) for item in value]
    return value


def dump(config: Config) -> dict[str, Any]:
    """The canonical snake_case view of a ``Config`` (pydantic ``model_dump``)."""
    return _jsonish(config.model_dump())


def defaults() -> dict[str, Any]:
    """``dump()`` of the default config - the base every valid row is compared against."""
    return dump(default_config())


def _field(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return getattr(record, key, None)


def error_locs(errors: Iterable[Any]) -> list[tuple[Any, ...]]:
    """Structured ``loc`` paths of a result's errors (tech-stack section 6)."""
    locs: list[tuple[Any, ...]] = []
    for error in errors:
        loc = _field(error, "loc")
        if loc is not None:
            locs.append(tuple(loc))
    return locs


def has_error_at(errors: Iterable[Any], *prefix: Any) -> bool:
    """True when some error's ``loc`` starts with ``prefix``.

    Prefix matching, not equality: pydantic appends the union member tag to ``loc`` for union
    fields (``("changelog", "literal[False]")``), and which member reports first is an
    implementation detail.

    Known limitation: because it matches prefixes, it cannot distinguish row 35's
    ``("fixed", 0)`` from row 36's ``("fixed", 0, 0)`` - an error reported one level deeper than
    expected still satisfies the shallower assertion. Accepted: the rows differ in *which* level
    is wrong, and pinning exact depth would re-couple the tests to pydantic's union-tag layout,
    which is the thing this helper exists to avoid.
    """
    return any(loc[: len(prefix)] == prefix for loc in error_locs(errors))


def texts(records: Iterable[Any]) -> list[str]:
    """Printable text of each warning/error, whatever container the result uses."""
    out: list[str] = []
    for record in records:
        if isinstance(record, str):
            out.append(record)
        else:
            message = _field(record, "msg") or _field(record, "message")
            out.append(str(message) if message is not None else str(record))
    return out


def joined(records: Iterable[Any]) -> str:
    return "\n".join(texts(records))


# --------------------------------------------------------------------------------------
# Row 4 - the default config. THE anchor of this file.
# --------------------------------------------------------------------------------------


@pytest.mark.snapshot
def test_default_config_matches_the_snapshot(snapshot: SnapshotAssertion) -> None:
    """Row 4 (`parse.test.ts:108-142`), the load-bearing inline snapshot, as syrupy.

    Upstream's object is reproduced key-by-key in research doc 03 lines 25-52 with each key
    annotated Port / Adapt / Drop; molt's object is that list after the annotations are applied.
    """
    assert default_config().model_dump() == snapshot


@pytest.mark.unit
def test_default_config_explicit_defaults() -> None:
    """The same anchor, spelled out, so intent survives a snapshot regeneration.

    ``base_branch`` is ``"main"`` because v3 changed it from ``"master"`` (research README
    section 3.2); ``update_internal_dependents`` is top level because molt promotes it out of
    ``___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH`` (research doc 03 row 28).

    ``snapshot.prerelease_template`` is molt-native in the *default object*: upstream's schema
    gives it no default, so the key is simply absent from ``Config`` (`config.ts:122-130`;
    research doc 02 section 1.2, "there is no `snapshot.prereleaseTemplate` key"). pydantic
    materializes it as ``None``, which is why it appears here and not in the group file's default
    block (line 44). Absent, ``None`` and ``""`` must stay interchangeable to consumers.
    """
    config = defaults()
    assert config["base_branch"] == "main"
    assert config["changed_file_patterns"] == ["**"]
    assert config["commit"] is False
    assert config["fixed"] == []
    assert config["linked"] == []
    assert config["ignore"] == []
    assert config["update_internal_dependencies"] == "patch"
    assert config["update_internal_dependents"] == "out-of-range"
    assert config["bump_workspace_sources_only"] is False
    assert config["private_packages"] == {"version": True}
    assert config["snapshot"] == {"use_calculated_version": False, "prerelease_template": None}
    assert config["ecosystem"] == "auto"
    assert config["forge"] == "github"


@pytest.mark.unit
def test_default_changelog_is_a_normalized_generator_ref() -> None:
    """Default ``changelog`` is the built-in generator in normalized ``[ref, options]`` form.

    Upstream's default is the module path ``["@changesets/cli/changelog", null]``
    (`defaults.ts:17`, `config.ts:171-173`). molt resolves generators through entry points rather
    than module paths (research README section 5, anti-features: "entry points instead"), so the
    *ref string itself* is molt's to choose - only the normalized 2-element shape is pinned here.
    """
    changelog = defaults()["changelog"]
    assert isinstance(changelog, list)
    assert len(changelog) == 2
    assert isinstance(changelog[0], str) and changelog[0]
    assert changelog[1] is None or isinstance(changelog[1], dict)


@pytest.mark.unit
def test_default_format_is_false() -> None:
    """``format`` replaces v2's ``prettier`` boolean, but molt defaults it OFF.

    Upstream defaults to ``"auto"`` and auto-detects a JS formatter (`config.ts:62-73`; research
    README section 3.2). molt does not: it emits correct, deterministic Markdown itself rather
    than emitting broken blank lines and cleaning up afterwards (research README section 5,
    item 13 "Correct changelog markdown (no formatter pass)"). Shelling out to Node from a Python
    release tool is the cost with no benefit, so research doc 02 section 12.1 recommends
    ``format: "mdformat" | false`` with default ``false``, and website docs
    ``config/options.md:95`` document exactly that.

    The group file's default-config block (line 40) shows ``"auto"`` because it transcribes
    *upstream's* object; that key is annotated Adapt, and this is the adaptation.
    """
    assert defaults()["format"] is False


@pytest.mark.unit
@pytest.mark.parametrize("key", DROPPED_KEYS)
def test_default_config_has_no_dropped_changesets_keys(key: str) -> None:
    """The npm/peerDeps keys never appear on a molt ``Config`` (research README section 4.4)."""
    assert key not in defaults()


# --------------------------------------------------------------------------------------
# Rows 5-15, 18, 21-28, 51 - valid configs. Compared against the defaults plus overrides,
# exactly like upstream's `{ ...defaultConfig, <override> }` (`parse.test.ts:175-364`).
# --------------------------------------------------------------------------------------

# The built-in generator reference, read off the defaults rather than spelled out: the ref string
# is molt's own (see `test_default_changelog_is_a_normalized_generator_ref`), so pinning it twice
# would make this table assert the spelling instead of the shape.
BUILTIN_CHANGELOG: Any = defaults()["changelog"]

# (written, package names, expected overrides on the default dump, expected warning count, why)
VALID_CASES: list[tuple[dict[str, Any], list[str], dict[str, Any], int | None, str]] = [
    ({}, DEFAULT_PKGS, {}, 0, "row 5: empty config is exactly the defaults"),
    (
        {"changelog": "molt_ext.changelog"},
        DEFAULT_PKGS,
        {"changelog": ["molt_ext.changelog", None]},
        0,
        "row 6: a bare generator ref normalizes to [ref, None]",
    ),
    (
        {"changelog": False},
        DEFAULT_PKGS,
        {"changelog": False},
        0,
        "row 7: false disables changelog generation",
    ),
    (
        {"changelog": ["molt_ext.changelog", {"something": True}]},
        DEFAULT_PKGS,
        {"changelog": ["molt_ext.changelog", {"something": True}]},
        0,
        "row 8: the [ref, options] tuple passes through; options is an opaque mapping",
    ),
    ({"commit": False}, DEFAULT_PKGS, {"commit": False}, 0, "row 9: commit false is the default"),
    (
        {"commit": ["molt_ext.commit", {"custom_option": True}]},
        DEFAULT_PKGS,
        {"commit": ["molt_ext.commit", {"custom_option": True}]},
        0,
        "row 11: a custom commit generator tuple passes through",
    ),
    (
        {"changed_file_patterns": ["src/**"]},
        DEFAULT_PKGS,
        {"changed_file_patterns": ["src/**"]},
        0,
        "row 14: kept; these are gitignore-shaped file patterns (doc 02 section 12.4: pathspec)",
    ),
    (
        {"format": "mdformat"},
        DEFAULT_PKGS,
        {"format": "mdformat"},
        0,
        "the one live formatter backend molt offers (doc 02 section 12.1)",
    ),
    (
        {"format": False},
        DEFAULT_PKGS,
        {"format": False},
        0,
        "explicitly off, which is also the default",
    ),
    # The `{"format": "auto"}` and `{"format": "prettier"}` rows lived here until 2026-08-01: they
    # asserted that a JS backend was accepted, warned about and normalized to false. The owner
    # ruling of that date (session 6) refuses those values outright, so the rows assert a
    # normalization that no longer happens and were removed rather than edited. Their successor is
    # `test_a_javascript_formatter_value_is_refused`, one row per entry in `_JS_FORMATTERS`. The two
    # `format` rows *above* are the negative controls and must stay green untouched.
    (
        {"fixed": [["pkg-a", "pkg-b"]]},
        DEFAULT_PKGS,
        {"fixed": [["pkg-a", "pkg-b"]]},
        0,
        "row 15: literal fixed group, core engine input",
    ),
    (
        {"linked": [["pkg-a", "pkg-b"]]},
        DEFAULT_PKGS,
        {"linked": [["pkg-a", "pkg-b"]]},
        0,
        "row 18: literal linked group, core engine input",
    ),
    (
        {"ignore": ["pkg-a", "pkg-b"]},
        DEFAULT_PKGS,
        {"ignore": ["pkg-a", "pkg-b"]},
        0,
        "row 21: literal ignore names pass through",
    ),
    (
        {"ignore": ["pkg-*", "acme-*"]},
        IGNORE_PKGS,
        {"ignore": ["pkg-a", "pkg-b", "acme-a", "acme-b"]},
        0,
        "row 22: ignore globs ARE expanded, in package-name order (utils.ts:13-41)",
    ),
    (
        {"ignore": ["pkg-*", "!pkg-b", "acme-*"]},
        IGNORE_PKGS,
        {"ignore": ["pkg-a", "acme-a", "acme-b"]},
        0,
        "row 23: a `!` pattern un-matches a name already matched by an earlier pattern",
    ),
    (
        {"ignore": ["not-a-valid-package"]},
        DEFAULT_PKGS,
        {"ignore": []},
        None,
        "row 24: an unmatched ignore entry resolves to [] and is NOT an error (warns: see below)",
    ),
    (
        {"private_packages": False},
        DEFAULT_PKGS,
        {"private_packages": {"version": False}},
        0,
        "row 25: the false shorthand expands to the object form; upstream's `tag` key is dropped",
    ),
    (
        {"private_packages": {"version": False}},
        DEFAULT_PKGS,
        {"private_packages": {"version": False}},
        0,
        "row 51: version:false alone is valid - no error, no warning",
    ),
    (
        {"update_internal_dependencies": "minor"},
        DEFAULT_PKGS,
        {"update_internal_dependencies": "minor"},
        0,
        "row 26: enum member `minor`",
    ),
    (
        {"update_internal_dependencies": "patch"},
        DEFAULT_PKGS,
        {"update_internal_dependencies": "patch"},
        0,
        "row 27: enum member `patch`",
    ),
    (
        {"update_internal_dependents": "always"},
        DEFAULT_PKGS,
        {"update_internal_dependents": "always"},
        0,
        "row 28 (molt spelling): promoted to a plain top-level option",
    ),
    (
        {"snapshot": {"use_calculated_version": True}},
        DEFAULT_PKGS,
        {"snapshot": {"use_calculated_version": True, "prerelease_template": None}},
        0,
        "snapshot sub-object is reshaped (research README section 4.3); template defaults to None",
    ),
    (
        {"ecosystem": "uv"},
        DEFAULT_PKGS,
        {"ecosystem": "uv"},
        0,
        "molt-native: no changesets equivalent (research README section 4.5)",
    ),
    (
        {"forge": "gitlab"},
        DEFAULT_PKGS,
        {"forge": "gitlab"},
        0,
        "molt-native: the forge seam exists from day one (research README section 5, item 7)",
    ),
    # ------------------------------------------------------------------------------
    # `changelog` as a table. Row 29 used to live in INVALID_SHAPE_CASES, pinning that a
    # mapping here is a hard error; the owner renegotiated it on 2026-07-30 (gap `VC-4`)
    # so that the three settings describing one subsystem live under one key. The
    # generator-reference rows 6-8 above are deliberately untouched by that widening.
    # ------------------------------------------------------------------------------
    (
        {"changelog": {}},
        DEFAULT_PKGS,
        {"changelog": {"generator": BUILTIN_CHANGELOG, "template": None, "dates": False}},
        0,
        "row 29 (renegotiated 2026-07-30): an empty changelog table is exactly the defaults",
    ),
    (
        {"changelog": {"generator": "molt_ext.changelog", "template": "e.md.jinja", "dates": True}},
        DEFAULT_PKGS,
        {
            "changelog": {
                "generator": ["molt_ext.changelog", None],
                "template": "e.md.jinja",
                "dates": True,
            }
        },
        0,
        "the table carries all three members; the ref normalizes exactly as row 6 does",
    ),
    (
        {"changelog": {"generator": ["molt_ext.changelog", {"something": True}]}},
        DEFAULT_PKGS,
        {
            "changelog": {
                "generator": ["molt_ext.changelog", {"something": True}],
                "template": None,
                "dates": False,
            }
        },
        0,
        "the nested ref takes the [name, options] pair too - one resolution path, not two",
    ),
    (
        {"changelog": {"generator": False}},
        DEFAULT_PKGS,
        {"changelog": {"generator": False, "template": None, "dates": False}},
        0,
        "false inside the table means what `changelog = false` means: write no CHANGELOG.md",
    ),
    # ------------------------------------------------------------------------------
    # `version_source` -- molt-native, no changesets counterpart at all (research doc 02
    # section 12.5). Declared in the package's OWN manifest, which in a single-package
    # repository is the very `[tool.molt]` table this parser reads; being a declared field
    # is what keeps such a configuration valid under strict parsing.
    # ------------------------------------------------------------------------------
    (
        {"version_source": {"kind": "file", "path": "src/pkg_a/__about__.py"}},
        DEFAULT_PKGS,
        {
            "version_source": {
                "kind": "file",
                "path": "src/pkg_a/__about__.py",
                "pattern": None,
            }
        },
        0,
        "a file source carries its path; the pattern defaults to molt's own",
    ),
    (
        {"version_source": {"kind": "static"}},
        DEFAULT_PKGS,
        {"version_source": {"kind": "static", "path": None, "pattern": None}},
        0,
        "the static source needs nothing but its kind",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("written", "pkgs", "expected", "warns", "why"), VALID_CASES)
def test_valid_config_normalizes_to_the_defaults_plus_overrides(
    written: dict[str, Any],
    pkgs: list[str],
    expected: dict[str, Any],
    warns: int | None,
    why: str,
) -> None:
    """The valid `it.each` block, adapted (`parse.test.ts:174-383`; research doc 03 rows 5-28)."""
    config, warnings, errors = parse_config(written, package_names=pkgs)
    assert list(errors) == [], why
    assert config is not None, why
    assert dump(config) == {**defaults(), **expected}, why
    if warns is not None:
        assert len(list(warnings)) == warns, why


@pytest.mark.unit
def test_commit_true_normalizes_to_a_generator_tuple() -> None:
    """Row 10: ``commit: true`` normalizes to the built-in commit generator tuple.

    Upstream produces ``["@changesets/cli/commit", {skipCI: "version"}]``
    (`config.ts:177-179`, `parse.test.ts:200-207`). The ref is a JS module path and the option is
    a GitHub-Actions-specific ``[skip ci]`` marker, so only the normalized shape ports; the ref
    string and option keys are molt's to choose.
    """
    config, warnings, errors = parse_config({"commit": True}, package_names=DEFAULT_PKGS)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    commit = dump(config)["commit"]
    assert isinstance(commit, list)
    assert len(commit) == 2
    assert isinstance(commit[0], str) and commit[0]
    assert commit[1] is None or isinstance(commit[1], dict)


@pytest.mark.unit
def test_success_returns_empty_lists_rather_than_none() -> None:
    """Upstream signals "no problems" with ``errors: undefined`` (`parse.ts:9-19`).

    molt returns ``[]`` for both channels so callers never branch on ``None`` before iterating.
    """
    config, warnings, errors = parse_config({}, package_names=DEFAULT_PKGS)
    assert errors == []
    assert warnings == []
    assert config is not None


# --------------------------------------------------------------------------------------
# Key aliasing - camelCase in, snake_case out, in BOTH directions (tech-stack section 6)
# --------------------------------------------------------------------------------------

# (written, expected overrides on the default dump, why)
ALIAS_CASES: list[tuple[dict[str, Any], dict[str, Any], str]] = [
    ({"base_branch": "trunk"}, {"base_branch": "trunk"}, "canonical snake_case"),
    ({"baseBranch": "trunk"}, {"base_branch": "trunk"}, "changesets camelCase alias"),
    (
        {"changed_file_patterns": ["src/**"]},
        {"changed_file_patterns": ["src/**"]},
        "canonical snake_case",
    ),
    (
        {"changedFilePatterns": ["src/**"]},
        {"changed_file_patterns": ["src/**"]},
        "changesets camelCase alias",
    ),
    (
        {"update_internal_dependencies": "minor"},
        {"update_internal_dependencies": "minor"},
        "canonical snake_case",
    ),
    (
        {"updateInternalDependencies": "minor"},
        {"update_internal_dependencies": "minor"},
        "changesets camelCase alias",
    ),
    (
        {"bump_workspace_sources_only": True},
        {"bump_workspace_sources_only": True},
        "canonical name; `workspace` is inverted upstream so the term is not reused (README 4.6)",
    ),
    (
        {"bumpVersionsWithWorkspaceProtocolOnly": True},
        {"bump_workspace_sources_only": True},
        "renamed option still accepts the changesets spelling for migration",
    ),
    (
        {"privatePackages": {"version": False}},
        {"private_packages": {"version": False}},
        "camelCase alias on a nested object",
    ),
    (
        {"snapshot": {"useCalculatedVersion": True}},
        {"snapshot": {"use_calculated_version": True, "prerelease_template": None}},
        "camelCase alias on a nested field",
    ),
    (
        {
            "___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH": {
                "updateInternalDependents": "always"
            }
        },
        {"update_internal_dependents": "always"},
        "row 28: the wrapper is accepted only as a migration alias for the top-level option",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("written", "expected", "why"), ALIAS_CASES)
def test_camelcase_and_snake_case_spellings_both_land_on_the_canonical_field(
    written: dict[str, Any], expected: dict[str, Any], why: str
) -> None:
    """Both spellings are accepted; ``Config`` exposes only the snake_case field.

    pydantic ``AliasChoices`` (tech-stack section 6; research doc 02 line 760). The canonical
    style is snake_case because that is what TOML users expect, while every changesets doc and
    every migrating ``config.json`` uses camelCase (research doc 02, recommendation 4).
    """
    config, warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert list(errors) == [], why
    assert list(warnings) == [], f"{why}: an accepted alias must not warn"
    assert config is not None, why
    assert dump(config) == {**defaults(), **expected}, why
    for key in written:
        if key not in expected:
            assert key not in dump(config), why


@pytest.mark.unit
@pytest.mark.parametrize(
    ("written", "loc", "why"),
    [
        (
            {"base_branch": "a", "baseBranch": "b"},
            ("base_branch",),
            "disagreeing spellings of one option",
        ),
        (
            {"base_branch": "same", "baseBranch": "same"},
            ("base_branch",),
            "agreeing spellings are still ambiguous - the file has two sources of truth",
        ),
    ],
)
def test_supplying_both_spellings_of_one_option_is_an_error(
    written: dict[str, Any], loc: tuple[Any, ...], why: str
) -> None:
    """An option set twice is a config bug, not a precedence puzzle.

    Left to pydantic's defaults, ``AliasChoices`` silently takes the first choice that is
    present, so ``baseBranch`` would be dropped without a word - the same silent-precedence
    failure mode that makes two config *sources* an error (research README section 6, decision 7:
    "Never merge two sources"). Aliases exist to make a pasted changesets config load, not to
    layer over an explicit snake_case value.

    The error is reported at the canonical field so tooling can point at one location regardless
    of which spelling the user typed.
    """
    config, _warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert list(errors), why
    assert has_error_at(errors, *loc), (
        f"{why}: expected an error at {loc}, got {error_locs(errors)}"
    )
    assert config is None, why


# --------------------------------------------------------------------------------------
# Rows 16, 17, 19, 20 - fixed/linked globs. molt EXPANDS them; upstream does not.
# --------------------------------------------------------------------------------------

# (option, written groups, expected expanded groups, why)
GLOB_GROUP_CASES: list[tuple[str, list[list[str]], list[list[str]], str]] = [
    (
        "fixed",
        [["pkg-*", "acme-*"], ["other-a"]],
        [["pkg-a", "pkg-b", "pkg-other-a", "acme-a", "acme-b"], ["other-a"]],
        "row 16: globs resolve to concrete names, in package-name order",
    ),
    (
        "fixed",
        [["pkg-*", "!pkg-b", "acme-*"], ["other-a"]],
        [["pkg-a", "pkg-other-a", "acme-a", "acme-b"], ["other-a"]],
        "row 17: `!` exclusions apply during expansion, same as for ignore",
    ),
    (
        "linked",
        [["pkg-*", "acme-*"], ["other-a"]],
        [["pkg-a", "pkg-b", "pkg-other-a", "acme-a", "acme-b"], ["other-a"]],
        "row 19: linked expands identically to fixed",
    ),
    (
        "linked",
        [["pkg-*", "!pkg-b", "acme-*"], ["other-a"]],
        [["pkg-a", "pkg-other-a", "acme-a", "acme-b"], ["other-a"]],
        "row 20: linked honours `!` exclusions too",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("option", "written", "expected", "why"), GLOB_GROUP_CASES)
def test_fixed_and_linked_globs_are_expanded(
    option: str, written: list[list[str]], expected: list[list[str]], why: str
) -> None:
    """DELIBERATE DIVERGENCE from the reference: molt expands ``fixed``/``linked`` globs.

    changesets 3.0 documents and warns about globs in these options but stores the groups
    verbatim and never expands them - a regression vs v2 that then throws ``InternalError`` at
    release time, because ``matchFixedConstraint`` compares with a literal ``.includes()``. That
    is upstream bug #1 in research README section 3.4 ("do not port these"), and it is why rows
    16/17/19/20 of research doc 03 expect ``verbatim`` while these tests expect concrete names.

    Expansion uses the same matcher as ``ignore`` - upstream `utils.ts:13-41`, picomatch -> a
    purpose-built pattern->regex matcher (research doc 02 section 12.4: **do not use ``fnmatch``**
    - its ``*`` crosses ``/``, its ``?`` matches ``/``, it has no negation, and its case folding
    is platform-dependent via ``os.path.normcase``). Ordering follows the workspace's
    package-name order, and ``!`` patterns un-match names an earlier pattern accepted.
    """
    config, warnings, errors = parse_config({option: written}, package_names=GLOB_PKGS)
    assert list(errors) == [], why
    assert list(warnings) == [], why
    assert config is not None, why
    assert dump(config)[option] == expected, why


@pytest.mark.unit
def test_a_glob_crosses_what_would_have_been_a_scope_boundary() -> None:
    """PyPI's namespace is flat, so ``pkg-*`` captures ``pkg-other-a``.

    Upstream's fixture deliberately includes ``@pkg-other/a`` as a near-miss: picomatch treats
    the scope ``/`` as a path separator, so ``@pkg*`` matches nothing and ``@pkg/*`` stops short
    of ``@pkg-other/a`` (research doc 02 section 4.2, "The scope `/` is treated as a path
    separator. This is the single biggest semantic trap for a port."). Python has no scopes
    (research README section 4.5), so the boundary does not exist and the prefix simply matches.
    Pinned explicitly because respelling the fixture would otherwise silently drop the guard.
    """
    config, _warnings, errors = parse_config({"ignore": ["pkg-*"]}, package_names=GLOB_PKGS)
    assert list(errors) == []
    assert config is not None
    assert dump(config)["ignore"] == ["pkg-a", "pkg-b", "pkg-other-a"]


@pytest.mark.unit
def test_a_star_does_not_cross_a_path_separator() -> None:
    """``*`` must not cross ``/``; ``**`` must (research doc 02 sections 4.2 and 12.4).

    A distribution name can never contain ``/``, so this looks academic - but doc 02 section 4.3
    notes `git/src/index.ts:341-369` is a byte-identical copy of ``globMatch`` used for
    ``changed_file_patterns``, and prescribes "Port this once, use it twice". The matcher that
    resolves ``ignore`` is therefore the same one that decides which files changed, where ``/``
    is everywhere. ``fnmatch`` fails this row outright: its ``*`` crosses ``/`` (doc 02 section
    12.4, the first line of the comparison table).
    """
    paths = ["pkg-a", "nested/deep/x"]
    single, _warnings, errors = parse_config({"ignore": ["*"]}, package_names=paths)
    assert list(errors) == []
    assert single is not None
    assert dump(single)["ignore"] == ["pkg-a"], "`*` must stop at the separator"

    double, _warnings, errors = parse_config({"ignore": ["**"]}, package_names=paths)
    assert list(errors) == []
    assert double is not None
    assert dump(double)["ignore"] == paths, "`**` crosses separators"


@pytest.mark.unit
def test_a_leading_negation_is_a_no_op() -> None:
    """Negation only subtracts from what is already matched, so order is load-bearing.

    Verified upstream behavior (research doc 02 section 4.3, Properties; Appendix A item 4
    "confirmed order-sensitivity of negations both ways"): ``["pkg-*", "!pkg-b"]`` drops
    ``pkg-b``, but ``["!pkg-b", "pkg-*"]`` keeps it, because ``passed`` is still false when the
    negated matcher is visited and the branch that consumes negations never runs. ``fnmatch`` has
    no negation at all (doc 02 section 12.4), so this row cannot be satisfied by reaching for it.

    NOTE for the reviewer: the fix brief quoted doc 02 section 12.4's summary of this property as
    "``["!b", "a-*"]`` returns both the ``a-*`` matches **and** ``b``". Traced against the
    algorithm in section 4.3 that is not what happens - ``a-*`` does not match ``b``, so ``b``
    stays unmatched. Section 4.3's own worked example is used instead, since it is the one
    Appendix A records as empirically verified.
    """
    kept, _warnings, errors = parse_config(
        {"ignore": ["!pkg-b", "pkg-*"]}, package_names=DEFAULT_PKGS
    )
    assert list(errors) == []
    assert kept is not None
    assert dump(kept)["ignore"] == ["pkg-a", "pkg-b"]

    dropped, _warnings, errors = parse_config(
        {"ignore": ["pkg-*", "!pkg-b"]}, package_names=DEFAULT_PKGS
    )
    assert list(errors) == []
    assert dropped is not None
    assert dump(dropped)["ignore"] == ["pkg-a"]


# (option, patterns, package names, expected resolution, why)
PEP503_MATCH_CASES: list[tuple[str, list[str], list[str], list[str], str]] = [
    (
        "ignore",
        ["foo-bar"],
        ["Foo_Bar"],
        ["Foo_Bar"],
        "PEP 503: pattern and candidate are normalized before matching (doc 02 s12.4/12.5)",
    ),
    (
        "ignore",
        ["Foo.Bar"],
        ["foo-bar"],
        ["foo-bar"],
        "and in the other direction",
    ),
    (
        "ignore",
        ["foo-*"],
        ["Foo_Bar", "other"],
        ["Foo_Bar"],
        "globs match the normalized name; the workspace spelling is what lands on Config",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("option", "patterns", "pkgs", "expected", "why"), PEP503_MATCH_CASES)
def test_name_matching_normalizes_both_sides_per_pep_503(
    option: str, patterns: list[str], pkgs: list[str], expected: list[str], why: str
) -> None:
    """``Foo_Bar``, ``foo-bar`` and ``Foo.Bar`` are one distribution (research README 4.5).

    ``re.sub(r"[-_.]+", "-", name).lower()``. Research doc 02 section 12.4 says to "normalize
    both pattern and candidate with PEP 503 before matching, and document it", and section 12.5
    names ``ignore``/``fixed``/``linked`` matching as one of the three places that must go
    through the normalized index. It also says to "preserve the original spelling for display",
    which is why the *workspace's* spelling is what lands on ``Config`` - not the pattern's.

    There is no changesets analogue: npm names are already case-restricted and have no
    punctuation equivalence, so nothing upstream exercises this.
    """
    config, _warnings, errors = parse_config({option: patterns}, package_names=pkgs)
    assert list(errors) == [], why
    assert config is not None, why
    assert dump(config)[option] == expected, why


@pytest.mark.unit
def test_a_fixed_group_member_resolves_through_pep_503_normalization() -> None:
    """The same rule on the group options, where getting it wrong also produces a false warning.

    A ``fixed`` member spelled ``foo-bar`` against a workspace package named ``Foo_Bar`` must
    resolve, not trip ``fixedGroupsExist`` (`rules.ts:22-37`). Research doc 02 section 12.5 lists
    ``fixed``/``linked`` matching alongside changeset-frontmatter lookup and dependency-graph
    edges as the consumers of the normalized index.
    """
    config, warnings, errors = parse_config(
        {"fixed": [["foo-bar", "other-pkg"]]}, package_names=["Foo_Bar", "other-pkg"]
    )
    assert list(errors) == []
    assert list(warnings) == [], "a name that differs only by PEP 503 folding is not unmatched"
    assert config is not None
    assert dump(config)["fixed"] == [["Foo_Bar", "other-pkg"]]


# --------------------------------------------------------------------------------------
# Rows 29-36, 41-43, 48-50, 53-55 - invalid shapes. Assert the structured `loc`, never the
# English message: changesets' strings are valibot's (`config.ts:31`, `:40`) and do not port.
# --------------------------------------------------------------------------------------

# (written, expected loc prefix, why)
INVALID_SHAPE_CASES: list[tuple[dict[str, Any], tuple[Any, ...], str]] = [
    # Row 29 is NOT here any more: `changelog = {}` became valid when the owner renegotiated it on
    # 2026-07-30 (gap `VC-4`). Its discriminating power moved rather than disappearing -- the four
    # rows below prove that a mapping is still not coerced into a generator ref member by member,
    # and that a wrong-typed `changelog` value of any other shape still fails.
    (
        {"changelog": 123},
        ("changelog",),
        "row 29 (successor): a scalar is none of the accepted forms",
    ),
    (
        {"changelog": {"dates": "not true"}},
        ("changelog",),
        "row 29 (successor): a table member of the wrong type is a hard error",
    ),
    (
        {"changelog": {"template": 123}},
        ("changelog",),
        "row 29 (successor): as above for the template member",
    ),
    (
        {"changelog": {"generator": [1, 2]}},
        ("changelog",),
        "row 29 (successor): the nested ref is validated exactly like the top-level one",
    ),
    (
        {"changelog": ["a", "b", "c"]},
        ("changelog",),
        "row 30: the tuple form is 2-arity, not 3",
    ),
    (
        {"changelog": [False, "something"]},
        ("changelog",),
        "row 31: the tuple's first element must be the generator ref string",
    ),
    ({"commit": {}}, ("commit",), "row 32: same union as changelog, plus the boolean form"),
    ({"fixed": {}}, ("fixed",), "row 34: fixed is a list of groups"),
    ({"fixed": [{}]}, ("fixed", 0), "row 35: each group is itself a list"),
    ({"fixed": [[{}]]}, ("fixed", 0, 0), "row 36: group members are strings"),
    ({"linked": {}}, ("linked",), "row 41: as row 34"),
    ({"linked": [{}]}, ("linked", 0), "row 42: as row 35"),
    ({"linked": [[{}]]}, ("linked", 0, 0), "row 43: as row 36"),
    (
        {"update_internal_dependencies": "major"},
        ("update_internal_dependencies",),
        "row 48: the enum is minor|patch only - `major` is not a member",
    ),
    ({"ignore": "string value"}, ("ignore",), "row 49: ignore is a list, not a string"),
    ({"ignore": [123, "pkg-a"]}, ("ignore", 0), "row 50: element type error is located by index"),
    (
        {"snapshot": {"use_calculated_version": "not true"}},
        ("snapshot", "use_calculated_version"),
        "row 53: nested boolean; loc is the dotted path",
    ),
    (
        {"changed_file_patterns": False},
        ("changed_file_patterns",),
        "row 54: as row 34",
    ),
    (
        {"changed_file_patterns": ["src/**", 100]},
        ("changed_file_patterns", 1),
        "row 55: element type error at index 1",
    ),
    (
        {"snapshot": {"prerelease_template": ""}},
        ("snapshot", "prerelease_template"),
        "empty template rejected by the min-length rule (config.ts:122-130; doc 02 line 229)",
    ),
    (
        {"update_internal_dependents": "sometimes"},
        ("update_internal_dependents",),
        "molt-native: the promoted option keeps the always|out-of-range enum",
    ),
    (
        {"version_source": {"path": "about.py"}},
        ("version_source", "kind"),
        "molt-native: `kind` is required -- molt will not guess where a version lives",
    ),
    (
        {"version_source": {"kind": "file"}},
        ("version_source", "path"),
        "molt-native: a file source with no path has no file to read or splice",
    ),
    (
        {"version_source": {"kind": "file", "path": "a.py", "pattern": r"v = \"(.+)\""}},
        ("version_source", "pattern"),
        "molt-native: the pattern must capture the `version` group molt replaces",
    ),
    (
        {"version_source": {"kind": "file", "path": "a.py", "pattern": "unclosed ("}},
        ("version_source", "pattern"),
        "molt-native: a pattern that does not compile is refused where it is written",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("written", "loc", "why"), INVALID_SHAPE_CASES)
def test_invalid_shape_reports_a_structured_error(
    written: dict[str, Any], loc: tuple[Any, ...], why: str
) -> None:
    """The invalid `it.each` block (`parse.test.ts:394-561`), asserted on ``loc``.

    Research doc 03 marks every one of these rows Adapt "message adapts": the concept ports, the
    prose does not. ``config is None`` whenever ``errors`` is non-empty, mirroring upstream's
    ``ParseResult`` union (`parse.ts:9-19`).
    """
    config, _warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert list(errors), why
    assert has_error_at(errors, *loc), (
        f"{why}: expected an error at {loc}, got {error_locs(errors)}"
    )
    assert config is None, why


@pytest.mark.unit
@pytest.mark.parametrize(
    ("written", "member"),
    [
        ({"changelog": {"dates": "not true"}}, "dates"),
        ({"changelog": {"template": 123}}, "template"),
        ({"changelog": {"generator": [1, 2]}}, "generator"),
    ],
)
def test_an_invalid_changelog_table_member_is_located_at_the_member(
    written: dict[str, Any], member: str
) -> None:
    """The error names the offending *member*, not merely ``changelog``.

    Asserted as "the member appears somewhere in the path" rather than as an exact ``loc``, because
    pydantic inserts its union-member tag between the two -- the real path is
    ``("changelog", "ChangelogOptions", "dates")``. Pinning the tag would couple this row to
    pydantic's union layout, which is the thing ``has_error_at``'s prefix matching exists to avoid;
    pinning only ``("changelog",)`` would not discriminate a located member error from the
    whole-value error every union failure also produces.
    """
    config, _warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert config is None
    assert any(member in loc for loc in error_locs(errors)), (
        f"expected some error path to name {member!r}, got {error_locs(errors)}"
    )


@pytest.mark.unit
def test_an_unknown_changelog_table_member_is_a_hard_error() -> None:
    """An unknown member of the ``changelog`` table fails, exactly like an unknown top-level key.

    **Renegotiated 2026-07-31 (session 6).** This row used to assert the opposite -- a warning, on
    the older "unknown keys never fail" contract that let a migrating configuration keep loading.
    The ruling reverses it: *"We should avoid impossible-states concepts and fail hard and fast with
    a good UX message for the user."* The cost the old row named as acceptable is exactly what the
    ruling refuses to pay: ``templat`` left templating quietly off while the file said it was on.

    Strictness is a property of the document, not of its depth, so this is the same rule the top
    level gets -- and the suggestion survives the move to the error channel, because naming the
    option the user meant is what makes the refusal actionable.
    """
    config, warnings, errors = parse_config(
        {"changelog": {"templat": "entry.md.jinja"}}, package_names=DEFAULT_PKGS
    )
    assert config is None
    assert error_locs(errors) == [("changelog", "templat")]
    assert 'Did you mean "template"?' in joined(errors)
    assert list(warnings) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "written",
    [
        {"changelog_template": "entry.md.jinja", "changelog_dates": True},
        {"changelogTemplate": "entry.md.jinja", "changelogDates": True},
    ],
)
def test_the_removed_flat_changelog_keys_are_hard_errors_naming_the_replacement(
    written: dict[str, Any],
) -> None:
    """The migration story for the keys the ``VC-4`` ruling removed, now told as a refusal.

    ``changelog_template`` / ``changelog_dates`` were real options between
    ``implement-version-command`` and the 2026-07-30 ruling that folded them into the ``changelog``
    table. **Renegotiated 2026-07-31 (session 6):** an un-migrated configuration no longer loads
    with a warning, it fails.

    What must not be lost when the channel changes is the *replacement text*. The dropped-options
    table exists for that sentence and for nothing else -- "unknown option" tells a user their
    template stopped being applied, while ``changelog = { template = ... }`` tells them what to
    type. Both spellings are covered because a migrating file may carry either.
    """
    config, warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert config is None
    assert len(list(errors)) == 2
    assert "`changelog = { template =" in joined(errors)
    assert "`changelog = { dates = true }`" in joined(errors)
    assert list(warnings) == []


@pytest.mark.unit
def test_errors_carry_both_a_location_and_a_message() -> None:
    """Every error is machine-readable *and* printable.

    ``loc`` is what tests and tooling key on (pydantic ``.errors()``, tech-stack section 6);
    ``msg`` is what the CLI prints. Upstream flattens both into one string (`parse.ts:21-23`),
    which is why its own tests can only ``toContain`` prose.
    """
    _config, _warnings, errors = parse_config({"ignore": [123]}, package_names=DEFAULT_PKGS)
    assert errors
    for error in errors:
        assert _field(error, "loc") is not None
        assert texts([error])[0]


# --------------------------------------------------------------------------------------
# Rows 37, 38, 44, 45 - group-existence rules warn (rules.ts:22-37, :62-77)
# --------------------------------------------------------------------------------------

# (option, written groups, package names, unmatched pattern, expected remaining groups, why)
GROUPS_EXIST_CASES: list[tuple[str, list[list[str]], list[str], str, list[list[str]], str]] = [
    (
        "fixed",
        [["not-existing"]],
        DEFAULT_PKGS,
        "not-existing",
        [],
        "row 37: a member matching no package warns; the emptied group is dropped",
    ),
    (
        "fixed",
        [["pkg-a", "foo-*"]],
        ["pkg-a"],
        "foo-*",
        [["pkg-a"]],
        "row 38: a glob matching nothing warns (upstream uses foo/*; PyPI names contain no /)",
    ),
    (
        "linked",
        [["not-existing"]],
        DEFAULT_PKGS,
        "not-existing",
        [],
        "row 44: as row 37, for linked",
    ),
    (
        "linked",
        [["pkg-a", "foo-*"]],
        ["pkg-a"],
        "foo-*",
        [["pkg-a"]],
        "row 45: as row 38, for linked (upstream uses foo/*; PyPI names contain no /)",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("option", "written", "pkgs", "unmatched", "expected", "why"), GROUPS_EXIST_CASES
)
def test_group_member_matching_no_package_is_a_warning_not_an_error(
    option: str,
    written: list[list[str]],
    pkgs: list[str],
    unmatched: str,
    expected: list[list[str]],
    why: str,
) -> None:
    """``fixedGroupsExist`` / ``linkedGroupsExist`` warn (`rules.ts:22-37`, `:62-77`).

    Warning, not error: a stale group member should not block a release. The expected group
    content follows from molt expanding these globs (research README section 3.4 - see
    ``test_fixed_and_linked_globs_are_expanded``); a group left with no members is dropped rather
    than kept as an empty list, so the engine never has to guard a degenerate group.
    """
    config, warnings, errors = parse_config({option: written}, package_names=pkgs)
    assert list(errors) == [], why
    assert len(list(warnings)) == 1, why
    assert unmatched in joined(warnings), why
    assert config is not None, why
    assert dump(config)[option] == expected, why


# --------------------------------------------------------------------------------------
# Rows 39, 40, 46, 47 - duplicate detection errors (rules.ts:39-60, :79-100)
# --------------------------------------------------------------------------------------

# (option, written groups, package names, duplicated names, why)
DUPLICATE_CASES: list[tuple[str, list[list[str]], list[str], list[str], str]] = [
    (
        "fixed",
        [["pkg-a"], ["pkg-a"]],
        ["pkg-a"],
        ["pkg-a"],
        "row 39: one name in two groups -> exactly one error",
    ),
    (
        "fixed",
        [["pkg-*"], ["pkg-*"]],
        ["pkg-a", "pkg-b"],
        ["pkg-a", "pkg-b"],
        "row 40 (adapted): globs are expanded first, so both resolved names are duplicated",
    ),
    (
        "linked",
        [["pkg-a"], ["pkg-a"]],
        ["pkg-a"],
        ["pkg-a"],
        "row 46: as row 39, for linked",
    ),
    (
        "linked",
        [["pkg-*"], ["pkg-*"]],
        ["pkg-a", "pkg-b"],
        ["pkg-a", "pkg-b"],
        "row 47: as row 40, for linked",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("option", "written", "pkgs", "duplicates", "why"), DUPLICATE_CASES)
def test_a_package_in_two_groups_is_an_error(
    option: str, written: list[list[str]], pkgs: list[str], duplicates: list[str], why: str
) -> None:
    """``noDuplicateFixedPackages`` / ``noDuplicateLinkedPackages`` (`rules.ts:39-60`, `:79-100`).

    Rows 40/47 are adapted rather than ported: upstream detects the duplicate on the literal glob
    string ``"pkg-*"`` precisely *because* it never expands the group. molt expands first
    (research README section 3.4), so the duplicates are the resolved names - one error each,
    which is the true cardinality of the conflict.

    Two errors where upstream printed one is a UX regression unless the message also names the
    pattern the user actually wrote (upstream's does: `rules.ts:56`). So when the written group
    contains a glob, the source pattern must survive into the error text; otherwise the report
    points at names that appear nowhere in the config file.
    """
    config, warnings, errors = parse_config({option: written}, package_names=pkgs)
    assert len(list(errors)) == len(duplicates), why
    text = joined(errors)
    for name in duplicates:
        assert text.count(name) >= 1, why
    if any("*" in member for group in written for member in group):
        assert "pkg-*" in text, f"{why}: the error must name the glob the user wrote"
    assert config is None, why
    assert list(warnings) == [], why


@pytest.mark.unit
@pytest.mark.parametrize("option", ["fixed", "linked"])
def test_a_duplicate_is_reported_once_regardless_of_how_many_groups_follow(option: str) -> None:
    """DELIBERATE DIVERGENCE: no re-emission per subsequent group.

    Upstream pushes the accumulated ``duplicatedNames`` set inside the per-group loop
    (`rules.ts:52-58`, `:92-98`), so a name present in N groups is reported N-1 times. That is
    the "``noDuplicateFixed/LinkedPackages`` loop placement" row of research README section 3.4;
    with the two groups upstream's own tests use it happens to look correct. Three groups expose
    it: upstream emits 2 errors, molt emits exactly 1.
    """
    written = {option: [["pkg-a"], ["pkg-a"], ["pkg-a"]]}
    config, _warnings, errors = parse_config(written, package_names=["pkg-a"])
    assert len(list(errors)) == 1
    assert "pkg-a" in joined(errors)
    assert config is None


@pytest.mark.unit
def test_a_package_cannot_be_both_fixed_and_linked() -> None:
    """``noFixedAndLinkedPackages`` (`rules.ts:102-115`) - untested upstream, ported anyway.

    Research doc 02 lists it as rule 5 of 7. Without a test it would silently disappear in the
    port, and the two grouping strategies would then fight over the same package.
    """
    written = {"fixed": [["pkg-a"]], "linked": [["pkg-a"]]}
    config, _warnings, errors = parse_config(written, package_names=["pkg-a", "pkg-b"])
    assert len(list(errors)) == 1
    assert "pkg-a" in joined(errors)
    assert config is None


# --------------------------------------------------------------------------------------
# Rows 1-3 and 56-57 - the filesystem path: real workspace, real config file
# --------------------------------------------------------------------------------------


@pytest.mark.functional
def test_load_config_reads_the_config_file(tmp_project: ProjectBuilder) -> None:
    """Row 1 (`parse.test.ts:10-60`), adapted to ``[tool.molt]``.

    Upstream reads ``.changeset/config.json``; molt reads ``[tool.molt]`` in the workspace-root
    ``pyproject.toml`` (research README section 6, decision 7). The non-throwing
    ``(config, warnings, errors)`` shape ports directly. As upstream, the result is the full
    default object with only the two written keys changed - and ``commit: true`` normalized.
    """
    tmp_project.add_package("pkg-a").set_config(changelog=False, commit=True)
    config, warnings, errors = load_config(tmp_project.root)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    result = dump(config)
    assert result["changelog"] is False
    assert isinstance(result["commit"], list) and len(result["commit"]) == 2
    assert {key: value for key, value in result.items() if key not in {"changelog", "commit"}} == {
        key: value for key, value in defaults().items() if key not in {"changelog", "commit"}
    }


@pytest.mark.functional
def test_load_config_resolves_the_root_config_from_a_nested_package(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 2 (`parse.test.ts:62-85`): started from ``packages/pkg-a``, still finds the root.

    Root discovery is via the uv workspace rather than ``pnpm-workspace.yaml``; the observable
    behavior - config is a property of the workspace root, not of the current directory - is what
    ports (website docs ``config/config-file.md``).
    """
    tmp_project.add_package("pkg-a").set_config(base_branch="release")
    nested_config, warnings, errors = load_config(tmp_project.root / "packages" / "pkg-a")
    assert list(errors) == []
    assert list(warnings) == []
    assert nested_config is not None
    root_config, _, _ = load_config(tmp_project.root)
    assert root_config is not None
    assert dump(nested_config) == dump(root_config)
    assert dump(nested_config)["base_branch"] == "release"


@pytest.mark.functional
def test_an_empty_config_table_yields_exactly_the_defaults(tmp_project: ProjectBuilder) -> None:
    """Row 3 (`parse.test.ts:87-105`): an empty ``[tool.molt]`` is the default config."""
    tmp_project.add_package("pkg-a").set_config()
    config, warnings, errors = load_config(tmp_project.root)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert dump(config) == defaults()


@pytest.mark.functional
def test_a_public_dependent_of_an_ignored_package_is_an_error(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 56 (`parse.test.ts:602-626`): ``alsoSkipDependentsOfSkipped`` (`rules.ts:124-172`).

    Needs the real dependency graph, so it goes through ``load_config`` over a ``tmp_project``
    workspace rather than through ``parse_config``. Dependencies are PEP 508 strings in a list,
    not a name->range map (research README section 4.5).
    """
    (
        tmp_project.add_package("pkg-a", version="1.0.0", deps=["pkg-b==1.0.0"])
        .add_package("pkg-b", version="1.0.0")
        .set_config(ignore=["pkg-b"])
    )
    config, _warnings, errors = load_config(tmp_project.root)
    assert list(errors), "pkg-a depends on the ignored pkg-b but is not itself ignored"
    text = joined(errors)
    assert "pkg-a" in text
    assert "pkg-b" in text
    assert config is None


@pytest.mark.functional
def test_a_dynamically_versioned_dependency_is_not_assumed_skipped(
    tmp_project: ProjectBuilder,
) -> None:
    """A defect ``implement-version-sources`` found and fixed, not a new feature.

    ``alsoSkipDependentsOfSkipped``'s third clause -- "a package with no version is skipped" -- read
    a ``dynamic = ["version"]`` member as frozen, so a workspace with *any* non-empty ``ignore``
    and a hatch-style member refused to load at all: ``pkg-a depends on the skipped package
    pkg-b``. It is only reachable with a non-empty ``ignore`` (or private versioning off), which is
    why nothing caught it before.

    ``molt.config`` cannot resolve a version source -- it runs first, and resolution opens a file
    per member -- so the rule stops guessing here and the accurate check stays in
    ``molt.commands.version._unskipped_dependents``, which has a real resolution to ask.
    """
    (
        tmp_project.add_package("pkg-a", version="1.0.0", deps=["pkg-b==1.2.3"])
        .add_package(
            "pkg-b",
            version=None,
            dynamic_version=True,
            version_source={"kind": "file", "path": "about.py"},
        )
        .add_package("pkg-c", version="1.0.0")
        .set_config(ignore=["pkg-c"])
    )
    tmp_project.write_file("packages/pkg-b/about.py", '__version__ = "1.2.3"\n')

    config, _warnings, errors = load_config(tmp_project.root)

    assert list(errors) == [], "a dynamically versioned dependency is not a frozen one"
    assert config is not None


@pytest.mark.functional
def test_a_private_dependent_of_an_ignored_package_is_exempt(
    tmp_project: ProjectBuilder,
) -> None:
    """Row 57 (`parse.test.ts:628-649`): private dependents are exempt (`rules.ts:164-166`).

    A private package is never uploaded, so a stale pin on a skipped dependency cannot break a
    consumer. Upstream keys this off ``package.json``'s ``private: true`` (`parse.test.ts:631`);
    the Python marker is the ``Private :: Do Not Upload`` classifier (research doc 02 section
    12.1), which ``ProjectBuilder.add_package(private=True)`` writes through tomlkit like every
    other field.
    """
    (
        tmp_project.add_package("pkg-b", version="1.0.0")
        .add_package("pkg-a", version="1.0.0", deps=["pkg-b==1.0.0"], private=True)
        .set_config(ignore=["pkg-b"])
    )
    config, _warnings, errors = load_config(tmp_project.root)
    assert list(errors) == []
    assert config is not None


# --------------------------------------------------------------------------------------
# Net-new molt behavior (research doc 07 section 7 "where molt exceeds the oracle")
# --------------------------------------------------------------------------------------


@pytest.mark.functional
def test_defining_config_in_both_locations_is_an_error(tmp_project: ProjectBuilder) -> None:
    """Never merge two config sources (research README section 6, decision 7).

    ``[tool.molt]`` and ``.molt/config.json`` are alternatives, not layers: silently merging them
    would need a precedence rule, and a release tool is the last place for a surprising one. No
    changesets equivalent - upstream has exactly one config location.
    """
    tmp_project.set_config(base_branch="release")
    tmp_project.set_config(as_json=True, baseBranch="release")
    config, _warnings, errors = load_config(tmp_project.root)
    assert list(errors), "both config sources present must be fatal, even when they agree"
    text = joined(errors)
    assert "pyproject.toml" in text
    assert ".molt/config.json" in text or "config.json" in text
    assert config is None


@pytest.mark.functional
@pytest.mark.parametrize(
    ("as_json", "options", "why"),
    [
        (False, {"base_branch": "release"}, "[tool.molt] in the root pyproject.toml"),
        (True, {"baseBranch": "release"}, ".molt/config.json, camelCase as a migrant would write"),
    ],
)
def test_either_config_source_alone_works(
    tmp_project: ProjectBuilder, as_json: bool, options: dict[str, Any], why: str
) -> None:
    """Both supported locations are equivalent (website docs ``config/config-file.md``)."""
    tmp_project.set_config(as_json=as_json, **options)
    config, warnings, errors = load_config(tmp_project.root)
    assert list(errors) == [], why
    assert list(warnings) == [], why
    assert config is not None, why
    assert dump(config)["base_branch"] == "release", why


@pytest.mark.functional
def test_no_config_at_all_is_the_default_config(tmp_project: ProjectBuilder) -> None:
    """Configuration is optional; its absence is not a warning."""
    tmp_project.add_package("pkg-a")
    config, warnings, errors = load_config(tmp_project.root)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert dump(config) == defaults()


GARBAGE_INPUTS: list[tuple[Any, str]] = [
    (None, "null config"),
    ("just a string", "a scalar where an object is required"),
    (123, "a number where an object is required"),
    (
        [],
        "a list where an object is required - a DIVERGENCE: research doc 02 Appendix A item 5 "
        "records that upstream's valibot accepts a top-level array, so a changesets config of "
        "`[]` loads there and is rejected here. Rejecting is deliberate; `[]` cannot carry any "
        "option and silently loading it as the defaults hides a broken file",
    ),
    ({"fixed": {"a": 1}}, "right key, wrong container"),
    ({"ignore": [[]]}, "nested list where strings are required"),
    ({"snapshot": "yes"}, "scalar where a sub-object is required"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("garbage", "why"), GARBAGE_INPUTS)
def test_parse_config_never_raises(garbage: Any, why: str) -> None:
    """The whole point of the ``(config, warnings, errors)`` shape (tech-stack section 6).

    Config problems are data the CLI reports in one pass, not exceptions that abort on the first
    bad key (website docs ``config/config-file.md``, "Validation never crashes").
    """
    config, warnings, errors = parse_config(garbage, package_names=DEFAULT_PKGS)
    assert list(errors), why
    assert config is None, why
    assert isinstance(list(warnings), list), why


@pytest.mark.unit
def test_unmatched_ignore_pattern_warns_instead_of_silently_dropping() -> None:
    """Net-new: the config-level instance of the silent skip in upstream issue #2101.

    Row 24 stays intact - the pattern resolves to ``[]`` and is *not* an error, so a stale entry
    never blocks a release. What molt adds is the diagnostic: upstream drops the entry with no
    signal at all (`config.ts:181-183` -> `utils.ts:13-41`), which is exactly the complaint in
    issue #2101 (research doc 08 section D.2 and item 4 of its recommendations).
    """
    config, warnings, errors = parse_config(
        {"ignore": ["not-a-valid-package"]}, package_names=DEFAULT_PKGS
    )
    assert list(errors) == []
    assert config is not None
    assert dump(config)["ignore"] == []
    assert len(list(warnings)) == 1
    assert "not-a-valid-package" in joined(warnings)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("written", "unknown", "why"),
    [
        ({"base_brnach": "main"}, "base_brnach", "a typo must never quietly disable a setting"),
        ({"access": "public"}, "access", "dropped npm scope key, tolerated for migration"),
        (
            {
                "___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH": {
                    "onlyUpdatePeerDependentsWhenOutOfRange": True
                }
            },
            "onlyUpdatePeerDependentsWhenOutOfRange",
            "dropped peerDependencies key inside an otherwise-aliased wrapper",
        ),
    ],
)
def test_unknown_and_dropped_keys_are_hard_errors(
    written: dict[str, Any], unknown: str, why: str
) -> None:
    """An unknown key fails the parse, and so does a key molt deliberately dropped.

    **Renegotiated 2026-07-31 (session 6).** Until that ruling this row asserted the opposite:
    ``errors == []`` and a warning per key, so that a migrating changesets ``config.json`` kept
    loading. The ruling weighs that affordance against what it costs -- *"We should avoid
    impossible-states concepts and fail hard and fast with a good UX message for the user"* -- and
    drops it. A key molt does not implement means a setting the user believes is configured and
    molt is ignoring, and molt writes versions into manifests and uploads them to an index, where
    the mistake is permanent.

    The three parameter sets are the three shapes the rule has to cover: a plain typo, a dropped
    changesets option (research README section 4.4), and a member of the experimental wrapper that
    molt did not promote -- the wrapper is a migration alias for exactly one option, not a place to
    keep settings molt has no concept of.
    """
    config, warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert config is None, why
    assert len(list(errors)) >= 1, why
    assert unknown in joined(errors), why
    assert list(warnings) == [], why


@pytest.mark.unit
def test_schema_key_is_accepted_and_stripped() -> None:
    """``$schema`` drives editor autocomplete only; it is not a behavioral option.

    Upstream carries it in ``defaultWrittenConfig`` (`defaults.ts:8`) and strips it from the
    normalized ``Config`` (`parse.test.ts:102-104`). Unlike a typo it must NOT warn.
    """
    written = {"$schema": "https://molt.gio-labs.com/schema/config.json", "base_branch": "release"}
    config, warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert dump(config) == {**defaults(), "base_branch": "release"}


# --------------------------------------------------------------------------------------
# Strict everywhere -- owner rulings 2026-07-31 and 2026-08-01 (session 6).
#
# "We should avoid impossible-states concepts and fail hard and fast with a good UX message
# for the user." The rows above renegotiated by that ruling say so individually; the block
# below is what it *adds*: refusal at every depth, refusal of combinations that cannot mean
# anything, and the one thing that is still only a warning.
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_an_unknown_key_still_suggests_the_nearest_real_option() -> None:
    """The ``difflib`` suggestion survived the move from the warning channel to the error one.

    Worth its own row because the suggestion is the whole reason the pre-pass, rather than
    pydantic's ``extra="forbid"``, is the enforcement path (design D1): pydantic says *"Extra inputs
    are not permitted"*, while molt names the option the user meant. Losing it while promoting the
    channel would leave the strictness in place and take the usability out of it.
    """
    _config, _warnings, errors = parse_config({"baseBrnach": "main"}, package_names=DEFAULT_PKGS)
    assert 'Did you mean "base_branch"?' in joined(errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("table", "member"),
    [("snapshot", "use_calculated_versoin"), ("private_packages", "versoin")],
)
def test_an_unknown_member_of_a_nested_table_is_a_hard_error(table: str, member: str) -> None:
    """ "Every nested table" means every one, not only the ``changelog`` table the ruling names.

    ``changelog`` is covered by ``test_an_unknown_changelog_table_member_is_a_hard_error``; these
    two are what proves the rule is a property of the document rather than of the one table that
    prompted it. ``loc`` carries ``(table, member)`` so the report names both.
    """
    config, warnings, errors = parse_config({table: {member: True}}, package_names=DEFAULT_PKGS)
    assert config is None
    assert error_locs(errors) == [(table, member)]
    assert member in joined(errors)
    assert list(warnings) == []


@pytest.mark.unit
def test_an_unknown_member_of_the_version_source_table_is_a_hard_error() -> None:
    """Strictness reaches the newest table too, and the suggestion comes with it.

    ``version_source`` has its own row rather than joining the parametrized nested-table one above
    because ``kind`` is **required**: a document carrying only the unknown member also fails the
    required-field check, so the exact-``loc`` assertion those two rows make would not fit. The
    guarantee is the same one -- an unknown key is an error at any depth (owner ruling 2026-07-31,
    session 6) -- and a typo in ``pattern`` silently leaving molt on its default pattern is exactly
    the impossible state that ruling removes.
    """
    config, warnings, errors = parse_config(
        {"version_source": {"kind": "file", "path": "a.py", "patern": "x"}},
        package_names=DEFAULT_PKGS,
    )
    assert config is None
    assert error_locs(errors) == [("version_source", "patern")]
    assert 'Did you mean "pattern"?' in joined(errors)
    assert list(warnings) == []


@pytest.mark.unit
def test_every_problem_in_one_document_is_reported_in_one_run() -> None:
    """Failing hard is about the errors channel, not about stopping early (design D2).

    Three unrelated problems, each found by a different layer: an unknown key (the raw-document
    pre-pass), a value outside a known option's enum (the model), and a package in two ``fixed``
    groups (the cross-option rules). Before this change the parser returned as soon as the pre-pass
    had anything, so the typo would have hidden the other two -- and a release tool that makes you
    fix one problem per run to discover the next is worse than one that is briefly noisy.

    The group rules therefore run on the raw payload rather than on a validated ``Config``, which is
    a correction to this change's own design D2: D2 accepted that a type failure anywhere would
    suppress the group diagnostics, and the delta spec's "Independent problems are reported
    together" scenario does not.

    Nothing is reported twice, and that holds by construction rather than by de-duplication -- the
    pre-pass never copies an offending value into the payload, so the model cannot report the same
    mistake a second time.
    """
    written = {
        "base_brnach": "main",
        "update_internal_dependencies": "major",
        "fixed": [["pkg-a"], ["pkg-a"]],
    }
    config, _warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert config is None
    messages = texts(errors)
    assert len(messages) == len(set(messages)), f"a message was reported twice: {messages}"
    assert "base_brnach" in joined(errors), "the unknown key"
    assert has_error_at(errors, "update_internal_dependencies"), "the wrong-valued known option"
    assert has_error_at(errors, "fixed", 1), "the package in two groups"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("table", "why"),
    [
        ({"generator": False, "template": "entry.md.jinja"}, "a template nothing will ever read"),
        ({"generator": False, "dates": True}, "a release date on an entry nothing will write"),
        (
            {"generator": False, "template": "entry.md.jinja", "dates": True},
            "both at once, which is the shape a user actually writes",
        ),
    ],
)
def test_a_disabled_changelog_generator_with_template_settings_is_refused(
    table: dict[str, Any], why: str
) -> None:
    """``generator = false`` writes no ``CHANGELOG.md``, so a template beside it configures nothing.

    Closes gap ``CFG-3``, which recorded the combination as accepted-and-inert. ``molt.apply``'s
    changelog planner returns before any entry is assembled, so the template is read off disk by the
    command and then discarded: the user configured an entry template and got no changelog.

    The message must name a **fix**, not only the problem -- there are two ways out and the user has
    to choose between them, so both are named.
    """
    config, _warnings, errors = parse_config({"changelog": table}, package_names=DEFAULT_PKGS)
    assert config is None, why
    assert has_error_at(errors, "changelog"), why
    text = joined(errors)
    assert "Remove" in text and "name a generator" in text, f"{why}: the message must name a fix"


@pytest.mark.unit
def test_a_dated_changelog_with_no_template_is_legal() -> None:
    """The negative control for the row above: it looks like a contradiction and is not.

    ``changelog = { dates = true }`` means the built-in entry template, dated. The difference is the
    only one that matters -- the generator is **on**, so there is a changelog for the date to appear
    in (design D3's "legal, and each argued" table).
    """
    config, warnings, errors = parse_config(
        {"changelog": {"dates": True}}, package_names=DEFAULT_PKGS
    )
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert config.changelog_dates is True
    assert config.changelog_generator == tuple(BUILTIN_CHANGELOG)
    assert config.changelog_template is None


@pytest.mark.unit
def test_an_unrecognised_snapshot_placeholder_is_refused() -> None:
    """An unknown ``{token}`` would be written into a version string verbatim, and that is forever.

    The placeholder set is closed. molt substitutes what it knows and leaves anything else alone, so
    ``{brnach}`` reaches PEP 440 as literal text inside a version an index then stores permanently.
    Refusing it at parse time is the same protection the engine's invocation-time checks give,
    one layer earlier, where the message can name the option instead of the run.

    The negative control lives in this row rather than beside it: every placeholder molt documents,
    alone, plus the compound spellings ``tests/engine/test_assemble.py::SNAPSHOT_TEMPLATE_CASES``
    drives end to end. The rule must not refuse a template the engine renders correctly -- two lists
    that disagree would be worse than the hole being closed (gap ``CFG-7``).
    """
    config, _warnings, errors = parse_config(
        {"snapshot": {"prerelease_template": "{brnach}"}}, package_names=DEFAULT_PKGS
    )
    assert config is None
    assert has_error_at(errors, "snapshot", "prerelease_template")
    text = joined(errors)
    assert "{brnach}" in text, "the message must name the offending token"
    for placeholder in SNAPSHOT_PLACEHOLDERS:
        assert f"{{{placeholder}}}" in text, "and list every placeholder molt accepts"

    accepted = [f"{{{placeholder}}}" for placeholder in SNAPSHOT_PLACEHOLDERS] + [
        # The compound spellings of `tests/engine/test_assemble.py::SNAPSHOT_TEMPLATE_CASES`.
        "{tag}-{tag}",
        "{tag}.{timestamp}.{commit}",
        "{tag}.{commit-short}",
        "{datetime}-{tag}",
    ]
    for template in accepted:
        good, _warns, errs = parse_config(
            {"snapshot": {"prerelease_template": template}}, package_names=DEFAULT_PKGS
        )
        assert list(errs) == [], f"{template} is documented and must parse"
        assert good is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("written", "loc", "why"),
    [
        ({"base_branch": ""}, ("base_branch",), "an empty ref cannot resolve to a commit"),
        (
            {"changelog": {"template": ""}},
            ("changelog",),
            "an empty filename cannot resolve to a file",
        ),
    ],
)
def test_an_empty_required_string_is_refused(
    written: dict[str, Any], loc: tuple[Any, ...], why: str
) -> None:
    """An empty name where a name is required fails here, not four layers down.

    ``snapshot.prerelease_template`` already carried ``min_length=1``; these two are the same rule,
    missing. Without them ``base_branch = ""`` fails inside a ``git merge-base`` error and
    ``changelog.template = ""`` inside a "template not found" message pointing at the workspace root
    directory -- both true, neither naming the option that is wrong.
    """
    config, _warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert config is None, why
    assert has_error_at(errors, *loc), (
        f"{why}: expected an error at {loc}, got {error_locs(errors)}"
    )


@pytest.mark.unit
def test_an_ignored_member_of_a_fixed_group_still_parses() -> None:
    """The single most important finding of this change's audit: it is NOT a contradiction.

    ``fixed`` promises one shared version and ``ignore`` promises no release, which reads like the
    clearest impossible state on the surface. It is pinned **legal** by a ported row --
    ``tests/cli/test_version.py::test_ignore_overrides_a_fixed_group_cobump``
    (``version.test.ts:844-897``, Row 18, Port) -- which requires exactly this combination to work,
    with ``ignore`` winning and the ignored member's pin still tracking.

    This row exists so nobody re-derives the audit and adds the rule that would turn that Port row
    red. See ``design.md`` D3.
    """
    written = {"fixed": [["pkg-a", "pkg-b"]], "ignore": ["pkg-a"]}
    config, warnings, errors = parse_config(written, package_names=DEFAULT_PKGS)
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert dump(config)["fixed"] == [["pkg-a", "pkg-b"]]
    assert dump(config)["ignore"] == ["pkg-a"]


@pytest.mark.unit
@pytest.mark.parametrize("value", _JS_FORMATTERS)
def test_a_javascript_formatter_value_is_refused(value: str) -> None:
    """Owner ruling 2026-08-01 (session 6): a Node formatter as the ``format`` value fails.

    This is **not** an unknown key, and the distinction is what a reader will otherwise get wrong:
    ``format`` is a real option and ``"prettier"`` is a value molt recognizes by name. That is why
    it is refused by its own check rather than by the unknown-key path, and why the migration guide
    has to mention it separately -- a stock changesets 3.0 configuration carries ``format: "auto"``
    and does nothing unusual, and that configuration now fails.

    Until this ruling molt accepted every one of these, normalized it to ``false`` and warned. The
    ruling calls that the impossible state it is: the user asked for formatting, molt did nothing,
    and the only trace was a line in a run nobody reads twice. molt has no Node and never will, so
    nothing about the workspace can ever make the setting come true.

    Parametrized over the product's own tuple, so a backend added there gets a row for free.
    """
    config, warnings, errors = parse_config({"format": value}, package_names=DEFAULT_PKGS)
    assert config is None
    assert error_locs(errors) == [("format",)]
    text = joined(errors)
    assert value in text, "the message must name the value the user wrote"
    assert "mdformat" in text and "false" in text, "and the settings that work instead"
    assert list(warnings) == []


@pytest.mark.unit
def test_an_empty_changed_file_patterns_list_is_refused() -> None:
    """Owner ruling 2026-08-01 (session 6): change detection cannot be switched off silently.

    An empty list means no file inside a package ever counts as a change to that package. Change
    detection is off for the whole workspace, ``molt status``'s CI gate can never fire again, and
    nothing says so on any run -- in the one place a release tool exists to be noisy.

    The message, not merely the refusal, is the requirement here. ``min_length=1`` alone would
    surface as pydantic's *"List should have at least 1 item after validation, not 0"*: true,
    located, and no help to somebody deciding what to type.
    """
    config, warnings, errors = parse_config(
        {"changed_file_patterns": []}, package_names=DEFAULT_PKGS
    )
    assert config is None
    assert error_locs(errors) == [("changed_file_patterns",)]
    text = joined(errors)
    assert "Remove the key" in text and '["**"]' in text, "the fix that restores the default"
    assert "list the patterns that count" in text, "and the fix that keeps a narrower rule"
    assert list(warnings) == []


@pytest.mark.unit
def test_a_pattern_list_that_matches_nothing_is_still_legal() -> None:
    """A **non-empty** ``changed_file_patterns`` list stays legal even when it can never match.

    ``["!src/**"]`` is negation-only, and negation only subtracts from what an earlier pattern
    already matched (``test_a_leading_negation_is_a_no_op``), so nothing ever passes it. It is still
    legal, and for the same reason an unmatched ``ignore`` glob is: the empty list can never match
    *by construction*, while a non-empty one is a statement about what the workspace holds right
    now. A rule here would also be a fourth place that knows about negation ordering.

    The honest limit, stated because it is easy to mistake for an oversight: this is legal **and
    silent**. ``changed_file_patterns`` matches file paths and config holds no file list while
    parsing, so unlike the ``ignore`` case there is nothing for it to warn about, and it does not
    invent one.
    """
    config, warnings, errors = parse_config(
        {"changed_file_patterns": ["!src/**"]}, package_names=DEFAULT_PKGS
    )
    assert list(errors) == []
    assert list(warnings) == []
    assert config is not None
    assert dump(config)["changed_file_patterns"] == ["!src/**"]


@pytest.mark.unit
def test_the_warning_channel_survives_strict_keys() -> None:
    """After this change the ``warnings`` channel carries exactly one thing, and it is not empty.

    Owner ruling 2026-08-01 (session 6), no longer provisional: *a pattern written for a package
    that does not exist yet is meaningful.* An ``ignore`` entry naming ``legacy-*`` in a repository
    that has not created ``legacy-api`` yet is a reasonable thing to write, and refusing it would
    make a monorepo's configuration a moving target in exactly the repositories molt is for.

    That is the line the strictness stops at: **is this wrong about the document, or wrong about the
    world right now?** An unknown key will still be wrong after the next commit and no workspace
    change makes it meaningful, so it fails. A glob that matches nothing today is a statement about
    this moment, so it warns. A ``format`` value naming a Node program is on the failing side of the
    same line for the sharpest reason of all -- it is not wrong about the workspace, it is wrong
    about molt.
    """
    config, warnings, errors = parse_config({"ignore": ["legacy-*"]}, package_names=DEFAULT_PKGS)
    assert list(errors) == []
    assert config is not None
    assert len(list(warnings)) == 1
    assert "legacy-*" in joined(warnings)
    assert dump(config)["ignore"] == []
