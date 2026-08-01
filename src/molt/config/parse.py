"""``parse_config`` -- the non-throwing parse, and the raw-document pre-pass it runs first.

Ports ``packages/config/src/parse.ts`` plus ``config.ts``'s ``normalizeWrittenConfig``. Design D3:
the result object is the contract, so **this module never raises for any input**, including a
document that is not a table at all.

The pre-pass exists because three things cannot be decided after validation:

- **Both spellings of one option.** ``AliasChoices`` resolves aliases before the model sees them,
  so a post-validation check has no way to tell that ``base_branch`` *and* ``baseBranch`` were both
  written. Detecting it on the raw document is the only place it is visible (design D3 risk note).
- **Unknown and dropped keys.** The model never sees them, so only the raw document knows which
  key was written and where the setting moved to. Refusing them here is what lets the message name
  the key, the replacement and the nearest real option instead of pydantic's "Extra inputs are not
  permitted".
- **Recognized-but-impossible values**, like the JS formatter backends and an empty
  ``changed_file_patterns`` list. Both are refused with molt's own sentence and the offending value
  is **popped** out of the payload, so the model cannot report a second, worse message for one
  mistake.

**Strict everywhere** (owner rulings 2026-07-31 / 2026-08-01, session 6). Any key molt does not
recognize is a hard error, at the top level and inside every nested table, and so is any option
combination that cannot mean anything. What is left in the ``warnings`` channel after that is
**one** thing: a glob -- an ``ignore`` entry or a ``fixed`` / ``linked`` member -- that matches no
package *today*. That is a statement about what the workspace contains right now, and the package
may be added next week, so refusing it would make a monorepo's configuration a moving target. A
``format`` value naming a Node program is on the other side of that line: nothing about the
workspace can ever make molt able to run it, so it is wrong about molt rather than about the world,
and it fails.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from molt.config.models import (
    ChangelogOptions,
    Config,
    PrivatePackages,
    SnapshotOptions,
    field_aliases,
)
from molt.config.result import ConfigIssue, ConfigResult
from molt.config.rules import (
    check_changelog_coherent,
    check_dependents_of_ignored,
    check_fixed_and_linked_disjoint,
    check_snapshot_placeholders,
    expand_groups,
    expand_ignore,
)

__all__ = ["parse_config"]

#: Editor-autocomplete pointer, never a behavioral option. Upstream carries it in
#: ``defaultWrittenConfig`` (``defaults.ts:8``) and strips it from the normalized object. Unlike a
#: typo it must **not** warn.
SCHEMA_KEY = "$schema"

#: changesets' container for unstable options. molt promotes ``updateInternalDependents`` out of it
#: into a plain top-level option and drops the peer-dependency flag it held, so the wrapper is not
#: a molt config surface -- it is accepted only as a migration alias (research README section 4.4).
EXPERIMENTAL_KEY = "___experimentalUnsafeOptions_WILL_CHANGE_IN_PATCH"

#: JS formatter backends, and ``auto``, which selects one of them. Every one is a Node program and
#: molt has no Node, so naming one is a hard error (owner ruling 2026-08-01, session 6). The tuple
#: survives the reversal because recognizing the value by name was always what made the *message*
#: worth reading -- see :func:`_check_format` (research README section 5 item 13; research doc 02
#: section 12.1).
_JS_FORMATTERS = ("auto", "prettier", "oxfmt", "deno", "dprint", "biome")

#: Options molt refuses to model, and options molt has **moved**, with the reason the error
#: carries. ``tests/config/test_deliberately_not_ported.py`` is the audit record for the refused
#: ones.
#:
#: The two ``changelog_*`` entries are the second kind. They were real molt options until the
#: 2026-07-30 ruling closing ``VC-4`` folded them into the ``changelog`` table; naming them here is
#: what turns "your template stopped being applied" into a sentence that says where the setting
#: went. They failed the parse from 2026-07-31 (session 6) onward, like every other unknown key --
#: the table's value was always the *sentence*, never the tolerance.
_DROPPED_OPTIONS: dict[str, str] = {
    "changelog_template": (
        "it moved into the `changelog` table. Write "
        '`changelog = { template = "changelog-entry.md.jinja" }` instead.'
    ),
    "changelogTemplate": (
        "it moved into the `changelog` table. Write "
        '`changelog = { template = "changelog-entry.md.jinja" }` instead.'
    ),
    "changelog_dates": (
        "it moved into the `changelog` table. Write `changelog = { dates = true }` instead."
    ),
    "changelogDates": (
        "it moved into the `changelog` table. Write `changelog = { dates = true }` instead."
    ),
    "access": (
        "npm scoped-package publish permission. PyPI has no per-package equivalent; choose an "
        "index with `molt publish --repository` instead."
    ),
    "prettier": "replaced by `format` in changesets 3.0. molt's `format` defaults to false.",
    "onlyUpdatePeerDependentsWhenOutOfRange": (
        "governs peerDependencies propagation, which Python has no concept of."
    ),
}

#: Sub-tables whose keys are canonicalized and checked the same way the top level is.
#:
#: ``changelog`` is here only for its *table* form -- the pass runs on a ``Mapping`` and leaves the
#: generator-reference forms alone. Being in this table is what makes an unknown member of the
#: changelog table **fail** exactly as an unknown member of ``snapshot`` does: strictness is a
#: property of the document, not of its depth (owner ruling 2026-07-31, session 6). Until that
#: ruling both warned, on the older "unknown keys never fail" contract closed by ``VC-4``.
_NESTED_MODELS = {
    "changelog": ChangelogOptions,
    "snapshot": SnapshotOptions,
    "private_packages": PrivatePackages,
}

#: Backends molt recognizes but cannot run yet; scheduled after the 0.1 MVP.
#:
#: Caught here rather than by the model's ``Literal`` so the message can say *planned* instead of
#: pydantic's "Input should be 'auto' or 'uv'", which reads like the name is wrong forever. Caught
#: here rather than at discovery so the generated schema never advertises them -- an editor
#: completing a backend that fails two steps later is worse than one that never offers it.
_PLANNED_ECOSYSTEMS = ("poetry", "hatch", "pdm", "setuptools")


def parse_config(
    data: Any, *, package_names: Sequence[str] = (), workspace: Any = None
) -> ConfigResult:
    """Validate and normalize a written configuration document.

    ``package_names`` is an addition to upstream's one-argument signature -- glob expansion and the
    group-existence rules cannot run without the workspace's package names, and upstream's
    ``validateConfig(json, packages)`` takes them too (``parse.ts:25``). ``workspace`` is optional
    and carries the dependency graph; only :func:`molt.config.load.load_config` has one, which is
    why the ignored-dependents rule runs only there.

    Returns ``(config, warnings, errors)``. ``config`` is ``None`` **if and only if** ``errors`` is
    non-empty, and that single test is the only thing deciding it.

    **Every problem in the document is reported in one run** (design D2). Since the 2026-07-31
    (session 6) ruling an unknown key is an error, and an early return after the pre-pass would let
    the first typo hide every wrong-typed value and every group conflict in the same file -- the
    exact "fix one thing, re-run, find the next" failure the two-channel result shape exists to
    prevent. So the pipeline always runs to the end: the pre-pass, then the model, then the
    cross-option rules whenever the model produced a ``Config`` to run them against.

    Nothing is reported twice, and that holds by construction rather than by de-duplication: the
    pre-pass never copies an offending value into ``payload``. An unknown key is not copied, an
    ambiguous spelling ``continue``s without setting the key, and :func:`_check_ecosystem` /
    :func:`_check_format` / :func:`_check_changed_file_patterns` each **pop** the value they refuse.
    """
    warnings: list[ConfigIssue] = []
    errors: list[ConfigIssue] = []

    if not isinstance(data, Mapping):
        kind = type(data).__name__
        return ConfigResult(
            None,
            warnings,
            [
                ConfigIssue(
                    (),
                    f"Invalid type: the configuration must be a table of options, "
                    f"but a {kind} was given.",
                )
            ],
        )

    payload = _canonicalize(dict(data), warnings, errors)
    resolved = _expand_and_check_groups(payload, package_names, warnings, errors)
    config = _validate(payload, errors)
    if config is not None:
        config = _apply_rules(config, resolved, workspace, errors)
    return ConfigResult(None if errors else config, warnings, errors)


# --------------------------------------------------------------------------------------
# Pre-pass over the raw document
# --------------------------------------------------------------------------------------


def _canonicalize(
    raw: dict[str, Any], warnings: list[ConfigIssue], errors: list[ConfigIssue]
) -> dict[str, Any]:
    """Rewrite the written document into canonical snake_case keys, collecting diagnostics.

    Aliases collapse here rather than in the model so that *which* spelling arrived is still
    knowable; everything downstream sees one spelling per option, and so does every error ``loc``.
    """
    aliases = field_aliases(Config)
    raw.pop(SCHEMA_KEY, None)
    written = _hoist_experimental(raw, errors, aliases)

    for field, accepted in aliases.items():
        for name in accepted:
            if name in raw:
                written.setdefault(field, []).append((name, raw[name]))

    known = {name for accepted in aliases.values() for name in accepted}
    errors.extend(_unknown_or_dropped(key, (), aliases) for key in raw if key not in known)

    payload: dict[str, Any] = {}
    for field, present in written.items():
        if len(present) > 1:
            errors.append(
                ConfigIssue(
                    (field,),
                    f"Ambiguous option: {_quoted([name for name, _ in present])} are spellings of "
                    f'the same option. Keep one; "{field}" is canonical.',
                )
            )
            continue
        payload[field] = present[0][1]

    for field, model in _NESTED_MODELS.items():
        table = payload.get(field)
        if isinstance(table, Mapping):
            payload[field] = _canonicalize_nested(field, dict(table), model, errors)
    _check_format(payload, errors)
    _check_ecosystem(payload, errors)
    _check_changed_file_patterns(payload, errors)
    return payload


def _hoist_experimental(
    raw: dict[str, Any], errors: list[ConfigIssue], aliases: dict[str, tuple[str, ...]]
) -> dict[str, list[tuple[str, Any]]]:
    """Promote the wrapper's surviving option to the top level; refuse the rest.

    Returns the ``field -> [(spelling, value)]`` table seeded with whatever was hoisted, so that
    supplying the option both inside the wrapper and at the top level is caught by the same
    ambiguity check as any other pair of spellings. The wrapper key itself is consumed, so it never
    reads as an unknown top-level option and never reaches ``Config``.

    A member the wrapper carries that molt did not promote is an unknown key like any other, and
    fails for the same reason (owner ruling 2026-07-31, session 6): the wrapper is a migration
    alias for exactly one option, not a place to keep settings molt has no concept of.
    """
    wrapper = raw.pop(EXPERIMENTAL_KEY, None)
    if not isinstance(wrapper, Mapping):
        if wrapper is not None:
            errors.append(_unknown_or_dropped(EXPERIMENTAL_KEY, (), aliases))
        return {}

    promoted = ("update_internal_dependents", "updateInternalDependents")
    written: dict[str, list[tuple[str, Any]]] = {}
    for key, value in wrapper.items():
        if key in promoted:
            spelling = f"{EXPERIMENTAL_KEY}.{key}"
            written.setdefault("update_internal_dependents", []).append((spelling, value))
        else:
            errors.append(_unknown_or_dropped(key, (EXPERIMENTAL_KEY,), aliases))
    return written


def _canonicalize_nested(
    field: str,
    table: dict[str, Any],
    model: type[Any],
    errors: list[ConfigIssue],
) -> dict[str, Any]:
    """The same treatment for a sub-table, so nested error ``loc``s are canonical too.

    An unknown member fails here exactly as an unknown top-level key does; ``loc`` carries
    ``(table, member)`` so the report names both.
    """
    aliases = field_aliases(model)
    known = {name for accepted in aliases.values() for name in accepted}
    for key in table:
        if key not in known:
            errors.append(_unknown_or_dropped(key, (field,), aliases))

    payload: dict[str, Any] = {}
    for subfield, accepted in aliases.items():
        present = [name for name in accepted if name in table]
        if len(present) > 1:
            errors.append(
                ConfigIssue(
                    (field, subfield),
                    f"Ambiguous option: {_quoted(present)} are spellings of the same option. "
                    f'Keep one; "{subfield}" is canonical.',
                )
            )
        elif present:
            payload[subfield] = table[present[0]]
    return payload


def _check_format(payload: dict[str, Any], errors: list[ConfigIssue]) -> None:
    """Refuse a JavaScript formatter named as the ``format`` value, naming the three fixes.

    **This was a normalization with a warning until the 2026-08-01 (session 6) ruling.** It folded
    every backend in :data:`_JS_FORMATTERS` onto ``false`` so a migrating changesets configuration
    kept loading, and said so in the ``warnings`` channel. The ruling weighs that against what it
    costs: molt cannot run any of those programs, so the user asked for formatting, molt did
    nothing, and the only trace was a line in a run nobody reads twice. The tuple of recognized
    backends survives the reversal because recognizing the value by name was always the valuable
    half -- it is what lets this message name a real alternative instead of listing the two literals
    the model happens to accept.

    The value is **popped** for the same reason :func:`_check_ecosystem` pops: leaving the string in
    the payload would make the model report a second, worse message (``Input should be a valid
    boolean``) for one mistake.
    """
    value = payload.get("format")
    if isinstance(value, str) and value in _JS_FORMATTERS:
        payload.pop("format")
        errors.append(
            ConfigIssue(
                ("format",),
                f'format = "{value}" names a JavaScript formatter, and molt never shells out to '
                f'Node. Use `format = "mdformat"` to run a formatter, `format = false` to run '
                f"none, or delete the line. molt emits deterministic Markdown itself, so "
                f"`false` is a real answer rather than a downgrade.",
            )
        )


def _check_ecosystem(payload: dict[str, Any], errors: list[ConfigIssue]) -> None:
    """Reject a recognized-but-unbuilt backend with a message that says so.

    The value is removed from the payload afterwards so the model does not report a second,
    less-informative literal error for the same mistake.
    """
    value = payload.get("ecosystem")
    if isinstance(value, str) and value in _PLANNED_ECOSYSTEMS:
        payload.pop("ecosystem")
        errors.append(
            ConfigIssue(
                ("ecosystem",),
                f'ecosystem = "{value}" is planned but not implemented yet. molt supports "auto" '
                f'(detect) and "uv"; a repository with no workspace table is treated as a single '
                f"package either way. {_quoted(_PLANNED_ECOSYSTEMS)} are scheduled after the 0.1 "
                f"release.",
            )
        )


def _check_changed_file_patterns(payload: dict[str, Any], errors: list[ConfigIssue]) -> None:
    """Refuse an **empty** ``changed_file_patterns`` list, naming what to write instead.

    Owner ruling 2026-08-01 (session 6). An empty list means no file inside a package ever counts as
    a change to that package, so change detection is off for the whole workspace and ``molt
    status``'s CI gate can never fire again -- permanently, on every run, with nothing said. That is
    the one place molt exists to be noisy.

    Written here rather than left to ``min_length=1`` on the field because :func:`_validate`
    forwards pydantic's ``detail["msg"]`` verbatim, so a model constraint alone surfaces as *"List
    should have at least 1 item after validation, not 0"* -- true, located, and no help at all to
    somebody deciding what to type. The field carries ``min_length=1`` **as well**, for the schema;
    at runtime it is inert, because this check has already popped the value.

    A **non-empty** list that matches nothing stays legal, and silent: that is the same question as
    an unmatched ``ignore`` glob, which the same ruling settles as a warning -- except that config
    holds no file list, so there is nothing for it to warn about.
    """
    value = payload.get("changed_file_patterns")
    if isinstance(value, (list, tuple)) and not value:
        payload.pop("changed_file_patterns")
        errors.append(
            ConfigIssue(
                ("changed_file_patterns",),
                "changed_file_patterns is empty, which means no file inside a package ever counts "
                "as a change to it -- so nothing is ever reported as changed and the CI gate can "
                'never fire. Remove the key to get the default ["**"], or list the patterns that '
                "count.",
            )
        )


def _unknown_or_dropped(
    key: str, prefix: tuple[str | int, ...], aliases: dict[str, tuple[str, ...]]
) -> ConfigIssue:
    """One error shape for a typo and for a key molt deliberately dropped.

    Both are the same hazard from the user's side -- a setting that appears to be configured and is
    not -- and since the 2026-07-31 (session 6) ruling both **fail the parse**. They used to warn,
    so that a migrating changesets ``config.json`` kept loading; the ruling weighs that affordance
    against what it costs, which is a release computed from a configuration molt is only partly
    honouring.

    The dropped-options table survives the reversal, and this is the paragraph worth keeping: its
    value was never the tolerance, it was the *sentence*. Knowing that ``changelog_template`` moved
    into the ``changelog`` table is what turns "unknown option" into "here is what to write
    instead", and that sentence is now an error message rather than a warning.

    The line the strictness stops at is in this module's docstring and in design D6: a **key** is
    wrong about the document and will still be wrong tomorrow, while a **glob** that matches nothing
    is a fact about the workspace right now. Keys fail; globs warn.
    """
    reason = _DROPPED_OPTIONS.get(key)
    if reason is not None:
        return ConfigIssue((*prefix, key), f'Option "{key}" is not supported by molt: {reason}')
    return ConfigIssue(
        (*prefix, key),
        f'Unknown option "{key}": molt does not recognize it. Remove the line, or replace it '
        f"with an option molt supports.{_did_you_mean(key, aliases)}",
    )


def _did_you_mean(key: str, aliases: dict[str, tuple[str, ...]]) -> str:
    """Suggest the nearest real option, when one is near enough to be worth naming.

    Suggestions are drawn from the **canonical** names only. Offering ``baseBranch`` back to
    someone who typed ``baseBrnach`` would be correct and useless -- it teaches the spelling molt
    tolerates for migration rather than the one it documents.

    The cutoff is deliberately tight. A wrong suggestion is worse than none: it sends the reader
    off to check an option that was never the one they meant.
    """
    from difflib import get_close_matches

    matches = get_close_matches(key, list(aliases), n=1, cutoff=0.75)
    return f' Did you mean "{matches[0]}"?' if matches else ""


def _quoted(names: Sequence[str]) -> str:
    """``"a" and "b"``, or ``"a", "b" and "c"`` -- readable at both the sizes this is used at."""
    quoted = [f'"{name}"' for name in names]
    if len(quoted) < 2:
        return "".join(quoted)
    return " and ".join([", ".join(quoted[:-1]), quoted[-1]])


# --------------------------------------------------------------------------------------
# Validation and rules
# --------------------------------------------------------------------------------------


def _validate(payload: dict[str, Any], errors: list[ConfigIssue]) -> Config | None:
    """Run the model, mapping pydantic's ``.errors()`` onto :class:`ConfigIssue`.

    ``loc`` is carried through structurally, which is the whole reason pydantic won the validation
    decision: it is the 1:1 counterpart of valibot's ``getDotPath``, so tests and tooling key on a
    path rather than on English prose that does not port (tech-stack section 6).
    """
    from pydantic import ValidationError

    try:
        return Config.model_validate(payload)
    except ValidationError as exc:
        for detail in exc.errors():
            errors.append(ConfigIssue(tuple(detail["loc"]), detail["msg"]))
        return None


def _expand_and_check_groups(
    payload: dict[str, Any],
    package_names: Sequence[str],
    warnings: list[ConfigIssue],
    errors: list[ConfigIssue],
) -> dict[str, Any]:
    """Expand the three pattern options and run their rules, in upstream's order (``rules.ts:184``).

    Runs on the **raw payload** rather than on a validated ``Config``, and that is the whole point:
    the group rules read lists of strings and nothing else, so making them wait for the model would
    mean one wrong-typed value anywhere in the document hides every group conflict in it -- the
    "fix one thing, re-run, find the next" failure design D2 exists to remove. *(This corrects
    design D2's own closing paragraph, which accepted that hole rather than closing it; the delta
    spec's "Independent problems are reported together" scenario requires it closed.)*

    Each option is shape-guarded, because a malformed value is pydantic's to report, not this
    function's -- and calling the matcher with a non-string would raise, which this module may
    never do. An option that fails its guard is simply skipped and returns no entry, so the caller
    leaves the model's own value in place.
    """
    ignore, ignore_warnings = expand_ignore(_string_list(payload.get("ignore")), package_names)
    fixed, fixed_warnings, fixed_errors = expand_groups(
        "fixed", _string_groups(payload.get("fixed")), package_names
    )
    linked, linked_warnings, linked_errors = expand_groups(
        "linked", _string_groups(payload.get("linked")), package_names
    )

    warnings.extend(fixed_warnings)
    warnings.extend(linked_warnings)
    warnings.extend(ignore_warnings)
    errors.extend(fixed_errors)
    errors.extend(linked_errors)
    errors.extend(check_fixed_and_linked_disjoint(fixed, linked))

    return {"ignore": ignore, "fixed": fixed, "linked": linked}


def _string_list(value: Any) -> tuple[str, ...]:
    """``value`` as a tuple of strings, or ``()`` when it is anything else.

    A bare ``str`` is rejected on purpose: it is a sequence of strings to Python and a wrong type to
    the model, and iterating it would silently expand ``"pkg-a"`` into five one-character patterns.
    """
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def _string_groups(value: Any) -> tuple[tuple[str, ...], ...]:
    """``value`` as groups of strings, or ``()`` when any level is the wrong shape."""
    if not isinstance(value, (list, tuple)):
        return ()
    groups: list[tuple[str, ...]] = []
    for group in value:
        if not isinstance(group, (list, tuple)) or not all(isinstance(m, str) for m in group):
            return ()
        groups.append(tuple(group))
    return tuple(groups)


def _apply_rules(
    config: Config,
    resolved: dict[str, Any],
    workspace: Any,
    errors: list[ConfigIssue],
) -> Config:
    """Fold the expanded patterns back onto the model and run the rules that need a typed one.

    All rules run; none short-circuits. A configuration with three separate problems reports three,
    because a release tool that makes you fix one problem per run to discover the next is worse
    than one that is briefly noisy. That applies to the two molt-native impossible-combination
    rules added by the 2026-07-31 (session 6) ruling as well: a contradictory ``changelog`` table
    and an unrecognised snapshot placeholder are reported alongside the group diagnostics, not
    instead of them.
    """
    errors.extend(check_changelog_coherent(config))
    errors.extend(check_snapshot_placeholders(config))

    updated = config.model_copy(update=resolved)
    if workspace is not None:
        errors.extend(check_dependents_of_ignored(updated, workspace))
    return updated
