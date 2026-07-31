"""The pydantic model of a molt configuration -- and therefore its JSON Schema.

Ports ``@changesets/config``'s valibot schema (``packages/config/src/config.ts:45-200``), reshaped
for PyPI. Research doc 02 section 1.1 is the master table this is derived from; research doc 03
annotates each key Port / Adapt / Drop, and the object below is that list with the annotations
applied. It is the most Adapt-heavy surface in the port (38 Port against 51 Adapt), so read the
per-field notes rather than assuming a key means what it means upstream.

**This module imports pydantic, so nothing may import it at module scope.** ``molt.config`` is a
lazy facade for exactly that reason (design D2); ``molt --help`` and shell completion must not pay
validation-library import cost.

What the schema deliberately does **not** carry (research README section 4.4, and
``tests/config/test_deliberately_not_ported.py`` as the audit record):

- the npm scope publish-permission option, which PyPI has no analogue for;
- ``privatePackages``' npm dist-channel sub-key, PyPI having no such channels;
- the peer-dependency propagation flag and the experimental wrapper it lived in, Python having no
  peer-dependency concept.

Two keys have **no** changesets counterpart at all: ``changelog_template`` and ``changelog_dates``,
which turn on molt's Jinja2 changelog-entry template (research README section 5 item 5; gap
``CT-1``). They are flat keys rather than the ``[tool.molt.changelog]`` sub-table
``website/docs/guides/changelog-templates.md`` first sketched, because ``changelog`` is already the
*generator reference* and ``tests/config/test_parse.py`` row 29 pins that a mapping there is a hard
error. The template still sees ``config.dates``, exactly as the guide's worked example writes it --
:mod:`molt.apply` passes it a changelog-scoped view, so what a user types and what a template reads
are two different surfaces on purpose.

All three are still *accepted* on input with a warning so a migrating changesets configuration
loads (see :mod:`molt.config.parse`); tolerated is not the same as supported, which is why they are
absent here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field

__all__ = [
    "BUILTIN_CHANGELOG",
    "BUILTIN_COMMIT",
    "BUILTIN_COMMIT_OPTIONS",
    "Config",
    "Ecosystem",
    "Forge",
    "Formatter",
    "GeneratorRef",
    "PrivatePackages",
    "SnapshotOptions",
    "UpdateInternalDependencies",
    "UpdateInternalDependents",
    "default_config",
    "field_aliases",
]

#: molt's built-in changelog generator. Upstream's default is the JS module path
#: ``"@changesets/cli/changelog"`` (``defaults.ts:17``); molt resolves generators through Python
#: entry points or ``module:attr`` references rather than module paths (research README section 5,
#: anti-features), so the ref string is molt's own -- only the normalized ``[ref, options]`` shape
#: ports.
BUILTIN_CHANGELOG = "molt.changelog.default"

#: molt's built-in commit-message generator, used when ``commit = true``.
BUILTIN_COMMIT = "molt.commit.default"

#: Options the built-in commit generator gets by default. Upstream passes ``{skipCI: "version"}``
#: (``config.ts:177-179``) -- a GitHub-Actions ``[skip ci]`` marker -- so the *shape* ports and the
#: key spelling is molt's.
BUILTIN_COMMIT_OPTIONS: dict[str, Any] = {"skip_ci": "version"}

#: A generator reference in its one normalized form: ``(ref, options-or-None)``. The second element
#: is an object or ``None``, never absent -- a 3-element tuple is a validation failure, matching
#: valibot's exact-length ``v.tuple`` (research doc 02 section 1.4).
GeneratorRef = tuple[str, dict[str, Any] | None]

#: Which packaging tool's workspace model discovery uses. **Only what molt can actually run.**
#:
#: Poetry, Hatch, PDM and setuptools are planned for after the 0.1 MVP and are deliberately absent:
#: the generated JSON Schema is built from this type, and an editor autocompleting a backend that
#: then fails at discovery is the same hazard as autocompleting a dropped changesets option --
#: which ``tests/config/test_json_schema.py`` has a dedicated test against. They are still
#: *recognized* on input, and rejected with a message that says "planned", not "invalid"
#: (:mod:`molt.config.parse`).
#:
#: Named here rather than inline for a second reason: pydantic's ``Field(default=...)`` is typed as
#: returning the default's own type, so a bare ``"auto"`` widens to ``str`` and stops type-checking
#: against the literal.
Ecosystem = Literal["auto", "uv"]

#: Release-automation backend. The seam exists from day one (research README section 5 item 7).
Forge = Literal["github", "gitlab"]

#: ``false`` (the default) or the one live formatter backend molt offers. The JS backends are
#: accepted on input as inert migration aliases and normalized away before validation, so they are
#: deliberately absent from the type and from the generated schema.
Formatter = Literal["mdformat", False]

#: Minimum bump on a dependency that triggers rewriting a dependent's pin (``config.ts:90-94``).
UpdateInternalDependencies = Literal["minor", "patch"]

#: Whether an already-in-range dependent still gets a patch release (``config.ts:151-154``).
UpdateInternalDependents = Literal["always", "out-of-range"]

_DEFAULT_ECOSYSTEM: Ecosystem = "auto"
_DEFAULT_FORGE: Forge = "github"
_DEFAULT_FORMAT: Formatter = False
_DEFAULT_UPDATE_INTERNAL_DEPENDENCIES: UpdateInternalDependencies = "patch"
_DEFAULT_UPDATE_INTERNAL_DEPENDENTS: UpdateInternalDependents = "out-of-range"


def _normalize_changelog(value: Any) -> Any:
    """``"ref"`` -> ``("ref", None)``; ``false`` and the tuple pass through (``config.ts:171``).

    Anything else is handed to the annotation unchanged so pydantic reports the type failure with a
    ``loc``, rather than this function guessing at an intent it cannot know.
    """
    return (value, None) if isinstance(value, str) else value


def _normalize_commit(value: Any) -> Any:
    """As :func:`_normalize_changelog`, plus ``true`` -> the built-in generator (``config.ts:177``).

    Normalizing the boolean here is what gives a boolean and a named generator **one**
    representation downstream, so no consumer has to re-derive "is committing on" from two shapes.
    ``isinstance(value, bool)`` is checked before ``str`` because ``True`` must not fall through.
    """
    if value is True:
        return (BUILTIN_COMMIT, dict(BUILTIN_COMMIT_OPTIONS))
    return (value, None) if isinstance(value, str) else value


def _normalize_private_packages(value: Any) -> Any:
    """The ``false`` shorthand expands to the object form (``config.ts:187-197``).

    Upstream collapses this union to an always-present object so consumers never branch on the
    shorthand. molt keeps that, minus the dropped sub-key: ``false`` means "do not version private
    packages", which is the whole of what the shorthand ever said.

    There is deliberately no ``true`` shorthand -- upstream rejects it too, and it would be
    ambiguous the moment a second sub-key is ever added.
    """
    return {"version": False} if value is False else value


class PrivatePackages(BaseModel):
    """Whether packages marked unpublishable are still versioned.

    Private distributions -- apps and internal tools you version but never upload -- are versioned
    by default so their internal dependency pins stay correct. The Python marker is the
    ``Private :: Do Not Upload`` classifier (research doc 02 section 12.1), standing in for npm's
    ``"private": true``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    version: bool = Field(default=True, description="Version private packages alongside the rest.")


class SnapshotOptions(BaseModel):
    """Snapshot-release options (``config.ts:111-134``), reshaped for PEP 440.

    ``prerelease_template`` has **no** default upstream -- the key is simply absent from the
    normalized object (research doc 02 section 1.4, "the type lies"). pydantic materializes it as
    ``None`` instead, so absent, ``None`` and unset must stay interchangeable to every consumer.
    The empty string is rejected rather than treated as absent, matching valibot's
    ``minLength(1)``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    use_calculated_version: bool = Field(
        default=False,
        validation_alias=AliasChoices("use_calculated_version", "useCalculatedVersion"),
        description="Base the snapshot on the changeset-derived version instead of 0.0.0.",
    )
    prerelease_template: Annotated[str, Field(min_length=1)] | None = Field(
        default=None,
        validation_alias=AliasChoices("prerelease_template", "prereleaseTemplate"),
        description=(
            "Template for the snapshot version suffix. Placeholders: {tag}, {commit}, "
            "{commit-short}, {timestamp}, {datetime}."
        ),
    )


class Config(BaseModel):
    """A fully normalized molt configuration.

    Every option is settable by its canonical ``snake_case`` name and by the changesets
    ``camelCase`` spelling, both landing on the same field, so a changesets configuration migrates
    unchanged. Supplying *both* spellings of one option is an error rather than a silent
    precedence rule -- see :mod:`molt.config.parse`.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    base_branch: str = Field(
        default="main",
        validation_alias=AliasChoices("base_branch", "baseBranch"),
        description='Git ref used as the comparison base for "what changed".',
    )
    changed_file_patterns: tuple[str, ...] = Field(
        default=("**",),
        validation_alias=AliasChoices("changed_file_patterns", "changedFilePatterns"),
        description="Which files inside a package directory count as a change for that package.",
    )
    changelog: Annotated[
        GeneratorRef | Literal[False],
        BeforeValidator(
            _normalize_changelog,
            json_schema_input_type=GeneratorRef | str | Literal[False],
        ),
    ] = Field(
        default=(BUILTIN_CHANGELOG, None),
        description="Changelog generator to run, with optional generator options.",
    )
    changelog_dates: bool = Field(
        default=False,
        validation_alias=AliasChoices("changelog_dates", "changelogDates"),
        description="Give the changelog template a release date (one timestamp per version run).",
    )
    changelog_template: str | None = Field(
        default=None,
        validation_alias=AliasChoices("changelog_template", "changelogTemplate"),
        description=(
            "Filename of a Jinja2 changelog-entry template, resolved against the workspace root."
        ),
    )
    commit: Annotated[
        GeneratorRef | Literal[False],
        BeforeValidator(
            _normalize_commit,
            json_schema_input_type=GeneratorRef | str | bool,
        ),
    ] = Field(
        default=False,
        description="Commit generator to run after version/publish, with optional options.",
    )
    ecosystem: Ecosystem = Field(
        default=_DEFAULT_ECOSYSTEM,
        description="Which workspace backend discovers packages, versions and internal deps.",
    )
    fixed: tuple[tuple[str, ...], ...] = Field(
        default=(),
        description="Groups of packages always released together, at the same shared version.",
    )
    forge: Forge = Field(
        default=_DEFAULT_FORGE,
        description="Which forge backend drives release automation.",
    )
    format: Formatter = Field(
        default=_DEFAULT_FORMAT,
        description="Optional external formatter run over the files molt writes.",
    )
    ignore: tuple[str, ...] = Field(
        default=(),
        description="Packages that must never be released; globs are expanded at load time.",
    )
    linked: tuple[tuple[str, ...], ...] = Field(
        default=(),
        description="Groups that share a version only when they happen to be released together.",
    )
    private_packages: Annotated[
        PrivatePackages,
        BeforeValidator(
            _normalize_private_packages,
            json_schema_input_type=PrivatePackages | Literal[False],
        ),
    ] = Field(
        default_factory=PrivatePackages,
        validation_alias=AliasChoices("private_packages", "privatePackages"),
        description="Whether private (never-uploaded) packages are still versioned.",
    )
    snapshot: SnapshotOptions = Field(
        default_factory=SnapshotOptions,
        description="Snapshot-release options.",
    )
    update_internal_dependencies: UpdateInternalDependencies = Field(
        default=_DEFAULT_UPDATE_INTERNAL_DEPENDENCIES,
        validation_alias=AliasChoices("update_internal_dependencies", "updateInternalDependencies"),
        description="Minimum bump on a dependency that triggers rewriting a dependent's pin.",
    )
    update_internal_dependents: UpdateInternalDependents = Field(
        default=_DEFAULT_UPDATE_INTERNAL_DEPENDENTS,
        validation_alias=AliasChoices("update_internal_dependents", "updateInternalDependents"),
        description="Whether an already-in-range dependent still gets a patch release.",
    )
    bump_workspace_sources_only: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "bump_workspace_sources_only", "bumpVersionsWithWorkspaceProtocolOnly"
        ),
        description="Only rewrite dependency pins that are backed by a workspace source.",
    )


def default_config() -> Config:
    """The default configuration -- what a repository with no config file gets, exactly.

    Every field carries its default, so this is ``Config()``; it exists as a function rather than a
    module constant so callers cannot accidentally share and mutate one instance, and so the
    defaults stay snapshot-pinnable from a single call site.
    """
    return Config()


def field_aliases(model: type[BaseModel]) -> dict[str, tuple[str, ...]]:
    """Map each field of ``model`` to every spelling that reaches it, canonical first.

    Derived from the model rather than hand-listed: the raw-document pre-pass needs the same alias
    table the validator uses, and two copies of it would drift the first time an option is added
    with only one of them updated.
    """
    table: dict[str, tuple[str, ...]] = {}
    for name, info in model.model_fields.items():
        alias = info.validation_alias
        if isinstance(alias, AliasChoices):
            spellings = tuple(choice for choice in alias.choices if isinstance(choice, str))
        elif isinstance(alias, str):
            spellings = (name, alias)
        else:
            spellings = (name,)
        table[name] = spellings if name in spellings else (name, *spellings)
    return table
