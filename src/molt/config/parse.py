"""``parse_config`` -- the non-throwing parse, and the raw-document pre-pass it runs first.

Ports ``packages/config/src/parse.ts`` plus ``config.ts``'s ``normalizeWrittenConfig``. Design D3:
the result object is the contract, so **this module never raises for any input**, including a
document that is not a table at all.

The pre-pass exists because three things cannot be decided after validation:

- **Both spellings of one option.** ``AliasChoices`` resolves aliases before the model sees them,
  so a post-validation check has no way to tell that ``base_branch`` *and* ``baseBranch`` were both
  written. Detecting it on the raw document is the only place it is visible (design D3 risk note).
- **Unknown and dropped keys.** ``extra="ignore"`` drops them without a word, which is the failure
  mode where a typo silently disables a setting.
- **Migration-only values**, like the JS formatter backends: they normalize to ``false`` *and*
  warn, and a model field cannot carry a warning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from molt.config.models import Config, PrivatePackages, SnapshotOptions, field_aliases
from molt.config.result import ConfigIssue, ConfigResult
from molt.config.rules import (
    check_dependents_of_ignored,
    check_fixed_and_linked_disjoint,
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

#: JS formatter backends. Accepted so a migrating configuration loads, normalized to ``false``
#: because molt emits correct, deterministic Markdown itself rather than shelling out to Node to
#: clean up after itself (research README section 5 item 13; research doc 02 section 12.1).
_JS_FORMATTERS = ("auto", "prettier", "oxfmt", "deno", "dprint", "biome")

#: Options molt refuses to model, with the reason the warning carries. Tolerated on input is not
#: the same as supported -- ``tests/config/test_deliberately_not_ported.py`` is the audit record.
_DROPPED_OPTIONS: dict[str, str] = {
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
_NESTED_MODELS = {"snapshot": SnapshotOptions, "private_packages": PrivatePackages}


def parse_config(
    data: Any, *, package_names: Sequence[str] = (), workspace: Any = None
) -> ConfigResult:
    """Validate and normalize a written configuration document.

    ``package_names`` is an addition to upstream's one-argument signature -- glob expansion and the
    group-existence rules cannot run without the workspace's package names, and upstream's
    ``validateConfig(json, packages)`` takes them too (``parse.ts:25``). ``workspace`` is optional
    and carries the dependency graph; only :func:`molt.config.load.load_config` has one, which is
    why the ignored-dependents rule runs only there.

    Returns ``(config, warnings, errors)``. ``config`` is ``None`` whenever ``errors`` is non-empty.
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
    if errors:
        return ConfigResult(None, warnings, errors)

    config = _validate(payload, errors)
    if config is None:
        return ConfigResult(None, warnings, errors)

    config = _apply_rules(config, package_names, workspace, warnings, errors)
    if errors:
        return ConfigResult(None, warnings, errors)
    return ConfigResult(config, warnings, errors)


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
    written = _hoist_experimental(raw, warnings, aliases)

    for field, accepted in aliases.items():
        for name in accepted:
            if name in raw:
                written.setdefault(field, []).append((name, raw[name]))

    known = {name for accepted in aliases.values() for name in accepted}
    warnings.extend(_unknown_or_dropped(key, (), aliases) for key in raw if key not in known)

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
            payload[field] = _canonicalize_nested(field, dict(table), model, warnings, errors)
    _normalize_format(payload, warnings)
    return payload


def _hoist_experimental(
    raw: dict[str, Any], warnings: list[ConfigIssue], aliases: dict[str, tuple[str, ...]]
) -> dict[str, list[tuple[str, Any]]]:
    """Promote the wrapper's surviving option to the top level; warn about the rest.

    Returns the ``field -> [(spelling, value)]`` table seeded with whatever was hoisted, so that
    supplying the option both inside the wrapper and at the top level is caught by the same
    ambiguity check as any other pair of spellings. The wrapper key itself is consumed, so it never
    reads as an unknown top-level option and never reaches ``Config``.
    """
    wrapper = raw.pop(EXPERIMENTAL_KEY, None)
    if not isinstance(wrapper, Mapping):
        if wrapper is not None:
            warnings.append(_unknown_or_dropped(EXPERIMENTAL_KEY, (), aliases))
        return {}

    promoted = ("update_internal_dependents", "updateInternalDependents")
    written: dict[str, list[tuple[str, Any]]] = {}
    for key, value in wrapper.items():
        if key in promoted:
            spelling = f"{EXPERIMENTAL_KEY}.{key}"
            written.setdefault("update_internal_dependents", []).append((spelling, value))
        else:
            warnings.append(_unknown_or_dropped(key, (EXPERIMENTAL_KEY,), aliases))
    return written


def _canonicalize_nested(
    field: str,
    table: dict[str, Any],
    model: type[Any],
    warnings: list[ConfigIssue],
    errors: list[ConfigIssue],
) -> dict[str, Any]:
    """The same treatment for a sub-table, so nested error ``loc``s are canonical too."""
    aliases = field_aliases(model)
    known = {name for accepted in aliases.values() for name in accepted}
    for key in table:
        if key not in known:
            warnings.append(_unknown_or_dropped(key, (field,), aliases))

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


def _normalize_format(payload: dict[str, Any], warnings: list[ConfigIssue]) -> None:
    """Fold the JS formatter backends onto ``false``, with one warning each."""
    value = payload.get("format")
    if isinstance(value, str) and value in _JS_FORMATTERS:
        payload["format"] = False
        warnings.append(
            ConfigIssue(
                ("format",),
                f'format = "{value}" is a JavaScript formatter and was normalized to false. '
                f'molt emits deterministic Markdown itself; set "mdformat" to run a formatter.',
            )
        )


def _unknown_or_dropped(
    key: str, prefix: tuple[str | int, ...], aliases: dict[str, tuple[str, ...]]
) -> ConfigIssue:
    """One warning shape for a typo and for a key molt deliberately dropped.

    Both are the same hazard from the user's side -- a setting that appears to be configured and is
    not -- and neither may fail the parse, because a migrating changesets configuration has to keep
    loading. Warning rather than failing is why the message has to carry its own weight: nothing
    downstream will stop and explain, so this sentence is the only thing standing between a typo
    and a silently unapplied setting.
    """
    reason = _DROPPED_OPTIONS.get(key)
    if reason is not None:
        return ConfigIssue((*prefix, key), f'Option "{key}" is not supported by molt: {reason}')
    return ConfigIssue(
        (*prefix, key), f'Unknown option "{key}" was ignored.{_did_you_mean(key, aliases)}'
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
    return " and ".join(f'"{name}"' for name in names)


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


def _apply_rules(
    config: Config,
    package_names: Sequence[str],
    workspace: Any,
    warnings: list[ConfigIssue],
    errors: list[ConfigIssue],
) -> Config:
    """Expand globs and run the cross-option rules, in upstream's order (``rules.ts:184-192``).

    All rules run; none short-circuits. A configuration with three separate problems reports three,
    because a release tool that makes you fix one problem per run to discover the next is worse
    than one that is briefly noisy.
    """
    ignore, ignore_warnings = expand_ignore(config.ignore, package_names)
    fixed, fixed_warnings, fixed_errors = expand_groups("fixed", config.fixed, package_names)
    linked, linked_warnings, linked_errors = expand_groups("linked", config.linked, package_names)

    warnings.extend(fixed_warnings)
    warnings.extend(linked_warnings)
    warnings.extend(ignore_warnings)
    errors.extend(fixed_errors)
    errors.extend(linked_errors)
    errors.extend(check_fixed_and_linked_disjoint(fixed, linked))

    resolved = config.model_copy(update={"ignore": ignore, "fixed": fixed, "linked": linked})
    if workspace is not None:
        errors.extend(check_dependents_of_ignored(resolved, workspace))
    return resolved
