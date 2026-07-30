"""The release-**line** template: five tokens, one pass, a closed key set.

Ports ``packages/changelog-github/src/render-template.ts`` (research doc 04 section 5.7 step 6).
This is the *inner* of molt's two changelog template layers and it shapes **one bullet**. The
outer layer -- the ``## <version>`` entry, its sections, their order and the date -- is
:mod:`molt.changelog.render`. ``website/docs/extending/changelog-plugins.md`` states the split in
one sentence: "You return lines, not layout."

The line template is a *generator option*, so it is written where the generator is configured::

    changelog = ["github", { repo = "acme/widgets", template = "- {{ summary }} {{ ref }}" }]

What ports, and what is replaced wholesale
------------------------------------------
changesets ships a bespoke ``{token}`` mini-engine: ``TOKEN_REGEX = /\\{(\\w+)\\}/g``
(``render-template.ts:4``) driving a ``String.replace`` with an ``Object.hasOwn`` guard
(``render-template.ts:14-28``). molt renders with Jinja2 instead (research README section 5 item 5),
so every token is respelled ``{{ token }}`` and a single-brace ``{summary}`` is ordinary text.

Ported unchanged: the token **set**, the ``{ref}`` precedence rule, and the developer experience of
the failure -- an unknown token is a hard error that names the offender and lists every valid token,
never a silent empty string.

Three properties this module has to hold, and why each is easy to lose
---------------------------------------------------------------------
1. **Substitution is function-based, never a replacement string** (design D1; research README
   section 3.4, "function-based regex"). A summary or sha carrying ``$1``, ``\\1``, ``$&`` or
   ``\\g<0>`` is corrupted the moment it reaches the *replacement* position of ``re.sub`` or JS
   ``String.replace``. Nothing here builds a replacement string at all: Jinja2 substitutes values,
   and every text transform below is a pure string operation.

2. **One pass.** Token values are data. Jinja2 renders the template source once and never rescans
   what it substituted, so a summary containing ``{{ 7*7 }}`` lands verbatim instead of becoming
   ``49``. An implementation that interpolated values into the source and *then* rendered -- or that
   rendered in a loop until the output stopped changing -- would turn any pull request into a
   template-injection vector.

3. **The key set is closed, and ``StrictUndefined`` alone does not close it.** Jinja2
   pre-populates ``Environment.globals`` with ``range``, ``dict``, ``lipsum``, ``cycler``,
   ``joiner`` and ``namespace``, so ``StrictUndefined`` never sees them: ``- {{ lipsum }}`` would
   *render*, and ship a paragraph of lorem ipsum into a published changelog. The template's free
   names are therefore checked against :data:`RELEASE_LINE_TOKENS` explicitly, before rendering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import Environment, StrictUndefined, TemplateError
from jinja2 import meta as jinja_meta

from molt.errors import MoltTemplateError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "RELEASE_LINE_TOKENS",
    "build_release_line_tokens",
    "new_environment",
    "render_source",
    "render_template",
    "resolve_ref",
]

#: Every token a release-line template may use, in sorted order (``render-template.ts:6-12``).
#:
#: The order is part of the constant's definition, not an artefact of how it was built: it is the
#: order the unknown-token error lists tokens in, and a stable order there is the difference between
#: a diffable error string and a churning one. ``thanks`` was removed upstream and is deliberately
#: not resurrected -- ``tests/changelog/test_render_template.py`` has a row for it.
RELEASE_LINE_TOKENS: tuple[str, ...] = ("authors", "commit", "pull", "ref", "summary")


def new_environment() -> Environment:
    """A Jinja2 environment with molt's changelog template policy, in one place.

    Both template layers share it, and both settings are deliberate:

    ``undefined=StrictUndefined``
        An undefined name raises instead of rendering an empty string (design D6). A changelog is
        published; a silently missing version heading or section is discovered by users, not by the
        run that produced it.
    ``autoescape=False``
        The output is Markdown, not HTML. Autoescaping would rewrite a summary's ``<``, ``>``, ``&``
        and ``"`` into entities -- corrupting exactly the author prose molt promises to pass through
        literally (``website/docs/extending/custom-generators.md``, "Summaries are passed through
        literally"). Escaping is not a safety measure here: template *injection* is prevented by
        never compiling user content (design D5), which is a stronger guarantee than escaping it.
    """
    return Environment(undefined=StrictUndefined, autoescape=False)


def render_source(
    environment: Environment,
    source: str,
    context: Mapping[str, object],
    *,
    what: str,
) -> str:
    """Compile and render ``source`` once, reporting any Jinja2 failure as a molt error.

    Every :class:`jinja2.TemplateError` -- ``TemplateSyntaxError`` from a malformed template,
    ``UndefinedError`` from a name the context does not carry -- becomes a
    :class:`molt.errors.MoltTemplateError`, which is both a :class:`MoltError` (so the CLI funnel
    prints a friendly message rather than a traceback for what is a configuration mistake) and a
    :class:`ValueError` (so a raw Jinja2 exception escaping molt is a test failure, not a silent
    pass). Jinja2's own message is carried through verbatim, because it is the part that names the
    offending name or the failing line.

    ``what`` names the layer for the reader: a project with both a line template and an entry
    template needs to know which one it broke.
    """
    try:
        return environment.from_string(source).render(context)
    except TemplateError as error:
        raise MoltTemplateError(f"The {what} failed to render: {error}") from error


def render_template(template: str, tokens: Mapping[str, str]) -> str:
    """Substitute the release-line tokens in ``template`` (``render-template.ts:14-28``).

    ``tokens`` is a built context -- see :func:`build_release_line_tokens`. Free names in the
    template are validated against :data:`RELEASE_LINE_TOKENS` **before** rendering, which is what
    keeps Jinja2's built-in globals out of a changelog; see the module docstring, property 3.

    Raises :class:`molt.errors.MoltTemplateError` (a :class:`ValueError`) for an unknown token, for
    a malformed template, and for a documented token the caller did not supply.
    """
    environment = _line_environment()
    _reject_unknown_tokens(environment, template)
    return render_source(environment, template, tokens, what="changelog line template")


def _line_environment() -> Environment:
    """:func:`new_environment` with Jinja2's built-in globals **removed**.

    A release line has a five-token surface and nothing else, so ``range``, ``dict``, ``lipsum``,
    ``cycler``, ``joiner`` and ``namespace`` have no business in one. Clearing them buys two
    independent things:

    - It makes :func:`_reject_unknown_tokens` able to see them at all.
      ``jinja2.meta.find_undeclared_variables`` skips any name present in ``Environment.globals``
      (``jinja2/meta.py``, ``TrackingCodeGenerator.enter_frame``), so with the defaults in place
      ``- {{ lipsum }}`` reports *no* undeclared variable and renders lorem ipsum into a published
      changelog.
    - It leaves ``StrictUndefined`` as a second line of defence, so even a caller that skipped
      validation gets a failure rather than filler text.

    The entry template keeps its globals: ``cycler``, ``namespace`` and ``range`` are ordinary tools
    for a *layout*, which is what that template is for.
    """
    environment = new_environment()
    environment.globals.clear()
    return environment


def _reject_unknown_tokens(environment: Environment, template: str) -> None:
    """Fail on any free name in ``template`` that is not a documented token.

    ``jinja2.meta.find_undeclared_variables`` reports the names a template *loads* without first
    assigning them, so a ``{% for line in ... %}`` loop variable is correctly not reported. It also
    skips anything in ``environment.globals``, which is why this is handed the globals-free
    environment :func:`_line_environment` builds. That is precisely the check ``StrictUndefined``
    cannot perform.

    The message reproduces upstream's developer experience (``render-template.ts:20-24``): it names
    the offender and lists every valid token, so a typo is fixable from the error alone. Offenders
    are sorted so the sentence is stable across runs.
    """
    try:
        parsed = environment.parse(template)
    except TemplateError as error:
        raise MoltTemplateError(
            f"The changelog line template could not be parsed: {error}"
        ) from error
    unknown = sorted(set(jinja_meta.find_undeclared_variables(parsed)) - set(RELEASE_LINE_TOKENS))
    if not unknown:
        return
    offenders = ", ".join(f'"{name}"' for name in unknown)
    valid = ", ".join(RELEASE_LINE_TOKENS)
    raise MoltTemplateError(
        f"Unknown changelog template token {offenders}. "
        f"A release-line template may use only: {valid}."
    )


def resolve_ref(pull: str | None, commit: str | None) -> str:
    """The ``ref`` token: the pull request, else the commit, else nothing (``:43-47``).

    Design D2 -- the precedence lives here and nowhere else, so the token path and any other
    context builder cannot disagree about it. A reader following a changelog bullet wants the
    discussion, so a merged pull request beats the raw sha it came from.

    The empty string, **not** ``"()"``, when there is neither: an empty parenthesis pair is not
    Markdown, and it is what leaves the dangling space a release line has to trim.
    """
    reference = pull or commit
    return f"({reference})" if reference else ""


def build_release_line_tokens(
    *,
    summary_linked: str,
    pull: str | None = None,
    commit: str | None = None,
    authors: str | None = None,
) -> dict[str, str]:
    """The render context for one release line (``render-template.ts:30-53``).

    Every value is a string: an absent link becomes ``""`` rather than ``None``, so a template that
    mentions a token molt could not fill still renders instead of failing. The keys are exactly
    :data:`RELEASE_LINE_TOKENS` -- adding one silently widens the public template surface, dropping
    one breaks templates the docs told people to write.

    **Seam deviation, flagged deliberately.** Upstream's ``buildReleaseLineTokens`` takes a nested
    ``{ summaryLinked, links: { pull, commit, user }, users }`` object in which ``links.user`` is
    never read (``render-template.ts:30-38`` versus ``:43-53``). This flat, keyword-only signature
    drops the dead key. ``summary_linked`` keeps upstream's name because the string it carries is
    the summary *after* the generator has linkified it, which is not the same thing as
    ``changeset.summary``.
    """
    return {
        "authors": authors or "",
        "commit": commit or "",
        "pull": pull or "",
        "ref": resolve_ref(pull, commit),
        "summary": summary_linked,
    }
