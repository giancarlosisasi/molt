r"""Conformance tests for molt's changelog **line**-template engine.

Ports ``packages/changelog-github/src/render-template.test.ts`` -- the 8 rows of the
"``packages/changelog-github/src/render-template.ts``" section of
``roadmap/research/test-suite/04-apply-changelog.md`` (4 Port / 4 Adapt / 0 Drop) -- onto
``molt.changelog.template``. Mechanics come from research doc 04 section 5.7 step 6.

The one structural divergence: **the substitution engine**
----------------------------------------------------------
changesets ships a bespoke ``{token}`` regex mini-engine -- ``TOKEN_REGEX = /\{(\w+)\}/g``
(``render-template.ts:4``) driving a ``String.replace`` with an ``Object.hasOwn`` guard
(``render-template.ts:14-28``). molt renders with **Jinja2** instead (research README
section 5 item 5: real changelog templating is changesets issue #109, open 7 years with a
pull request that waited 6 of them). So every template below is respelled ``{{ token }}``.

What ports unchanged: the token *set*, the ``{ref}`` precedence rule, and the developer
experience of the failure -- an unknown token is a hard error naming the offender and
listing the valid set, not a silent empty string. What changes: the spelling, and the
mechanism (Jinja2 ``StrictUndefined`` in place of the hand-rolled ``hasOwn`` check).

Two template layers, do not confuse them
-----------------------------------------
``website/docs/guides/changelog-templates.md`` documents a *different*, molt-native Jinja2
template -- ``[tool.molt.changelog] template = "..."`` -- which shapes the **entry**
(the ``## <version>`` heading, the ``### Major/Minor/Patch Changes`` sections, dates).
The template exercised here is the upstream **per-line** ``template`` option, which lives
in the *generator's* options table (``changelog = ["github", { repo = "...", template =
"..." }]``) and shapes one bullet. ``website/docs/extending/changelog-plugins.md`` states
the split: "You return lines, not layout." Both are Jinja2; they are different seams.

TDD targets declared by this file (they do not exist yet -- build step 7)
-------------------------------------------------------------------------
- ``molt.changelog.template.render_template(template: str, tokens: Mapping[str, str]) -> str``
- ``molt.changelog.template.build_release_line_tokens(*, summary_linked, pull=None,
  commit=None, authors=None) -> dict[str, str]``
- ``molt.changelog.template.RELEASE_LINE_TOKENS`` -- the sorted token key-set.

**Seam deviation, flagged deliberately:** upstream's ``buildReleaseLineTokens`` takes a
nested ``{ summaryLinked, links: { pull, commit, user }, users }`` object
(``render-template.ts:30-38``) in which ``links.user`` is never read -- only ``links.pull``,
``links.commit`` and ``users`` are (``render-template.ts:43-53``). The flat keyword-only
signature above drops the dead key. If the owner prefers the nested shape, only the four
``build_release_line_tokens`` call sites here change.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "molt.changelog.template",
    reason="build step 7 -- molt.changelog not yet implemented (TDD target)",
)

from molt.changelog.template import (
    RELEASE_LINE_TOKENS,
    build_release_line_tokens,
    render_template,
)

pytestmark = pytest.mark.unit

# ``render-template.test.ts:9-15`` -- the token fixture, verbatim.
TOKENS: dict[str, str] = {
    "summary": "msg",
    "ref": "(REF)",
    "pull": "(PULL)",
    "commit": "(COMMIT)",
    "authors": "AUTHORS",
}

# ``render-template.ts:6-12`` -- RELEASE_LINE_TOKENS, sorted. Written out as a literal so the
# assertion below cannot be satisfied by whatever the implementation happens to return.
EXPECTED_TOKEN_KEYS: tuple[str, ...] = ("authors", "commit", "pull", "ref", "summary")


# ======================================================================================
# render_template -- rows 1-4
# ======================================================================================

RENDER_CASES = [
    # (template, expected, why)
    (
        "- {{ summary }} {{ ref }}",
        "- msg (REF)",
        "row 1 (render-template.test.ts:17-19): known tokens substitute in place",
    ),
    (
        "- start {{ summary }} end",
        "- start msg end",
        "row 2 (render-template.test.ts:21-25): literal text either side is untouched",
    ),
    (
        "- nothing to substitute",
        "- nothing to substitute",
        "row 2 corollary: a template with no token is returned verbatim",
    ),
    (
        "- {{ pull }} {{ commit }} Thanks {{ authors }}! - {{ summary }}",
        "- (PULL) (COMMIT) Thanks AUTHORS! - msg",
        "row 1 extended: every documented token resolves, not just the two in the base row",
    ),
    (
        "- {summary}",
        "- {summary}",
        "molt-native: single braces are ordinary text in Jinja2, so the bespoke changesets "
        "{token} engine (render-template.ts:4) is provably gone",
    ),
]


@pytest.mark.parametrize(("template", "expected", "why"), RENDER_CASES)
def test_render_template_substitutes_tokens(template: str, expected: str, why: str) -> None:
    """``render-template.test.ts:17-25`` -- substitution and literal passthrough."""
    assert render_template(template, TOKENS) == expected, why


UNKNOWN_TOKEN_CASES = [
    # (template, offender, why)
    (
        "- {{ summery }}",
        "summery",
        "row 3 (render-template.test.ts:27-31): a typo must fail the run, not render empty",
    ),
    (
        "- {{ thanks }}",
        "thanks",
        "row 4 (render-template.test.ts:33-37): {thanks} was deliberately removed upstream; "
        "molt must keep rejecting it rather than quietly resurrect it",
    ),
    (
        "- {{ lipsum }}",
        "lipsum",
        "molt-native: `lipsum` is a Jinja2 BUILT-IN global, so StrictUndefined never sees it "
        "and `render_template` would happily emit paragraphs of lorem ipsum. This row is what "
        "forces validation against RELEASE_LINE_TOKENS explicitly instead of delegating the "
        "check to StrictUndefined",
    ),
    (
        "- {{ range }}",
        "range",
        "molt-native: same hazard, second built-in. Jinja2 ships range, dict, lipsum, cycler, "
        "joiner and namespace in Environment.globals; none of them is a changelog token",
    ),
]


@pytest.mark.parametrize(("template", "offender", "why"), UNKNOWN_TOKEN_CASES)
def test_render_template_rejects_an_unknown_token(template: str, offender: str, why: str) -> None:
    """``render-template.ts:18-27`` -- unknown tokens raise, listing the valid set.

    ADAPTED: the mechanism is a check against ``RELEASE_LINE_TOKENS`` layered *over* Jinja2
    ``StrictUndefined`` rather than an ``Object.hasOwn`` check, but the DX is pinned
    unchanged -- the error names the offending token and lists every valid one.
    ``ValueError`` is asserted (not a bare ``Exception``) precisely so a raw
    ``jinja2.UndefinedError`` leaking out of molt fails this test: ``UndefinedError``
    derives from ``jinja2.TemplateError``, not ``ValueError``, so an unwrapped Jinja2
    failure is caught here rather than passing silently.

    ``StrictUndefined`` alone is **not** sufficient, which is what the last two rows exist
    to prove: it only raises for names it cannot resolve, and Jinja2 pre-populates
    ``Environment.globals`` with ``range``, ``dict``, ``lipsum``, ``cycler``, ``joiner`` and
    ``namespace``. ``render_template("- {{ lipsum }}", TOKENS)`` therefore *renders* under a
    StrictUndefined-only implementation -- silently, and with a paragraph of lorem ipsum in
    a published changelog. The token set has to be validated explicitly.
    """
    with pytest.raises(ValueError) as excinfo:
        render_template(template, TOKENS)
    message = str(excinfo.value)
    assert "unknown changelog template token" in message.lower(), (
        f"{why}; the upstream phrasing is load-bearing DX (render-template.ts:20-24)"
    )
    assert offender in message, f"{why}; the error must name the offending token"
    for valid in EXPECTED_TOKEN_KEYS:
        assert valid in message, f"{why}; the error must list every valid token, missing {valid}"


def test_render_template_does_not_re_render_substituted_values() -> None:
    """molt-native, adversarial: a token *value* is data, never a template.

    Jinja2 renders in one pass, so a summary containing ``{{ 7*7 }}`` must survive
    literally. An implementation that pre-substituted the summary into the template string
    and then rendered (or rendered twice) would emit ``49`` and expose changeset authors to
    template injection. Pairs with the ``$``/``\\1`` regex payload test below; both are the
    "summaries are passed through literally" promise in
    ``website/docs/extending/custom-generators.md``.
    """
    hostile = dict(TOKENS, summary="fix {{ 7*7 }} and {% raw %}stuff{% endraw %}")
    rendered = render_template("- {{ summary }}", hostile)
    assert rendered == "- fix {{ 7*7 }} and {% raw %}stuff{% endraw %}"


def test_render_template_does_not_interpret_regex_replacement_syntax() -> None:
    r"""Upstream bug NOT ported (research README section 3.4, "function-based regex").

    A token value carrying ``$1`` / ``\1`` / ``\g<0>`` must land verbatim. In Python this
    forces a replacement **callable** wherever ``re.sub`` is used, never a replacement
    string -- a string replacement would expand ``\1`` and corrupt the line. The equivalent
    JS bug is ``String.replace`` with a ``$&``-bearing replacement string.
    """
    hostile = dict(TOKENS, summary=r"keep \1 and \g<0> and $1 and $& intact")
    rendered = render_template("- {{ summary }} {{ ref }}", hostile)
    assert rendered == r"- keep \1 and \g<0> and $1 and $& intact (REF)"


# ======================================================================================
# build_release_line_tokens -- rows 5-8 (forge-agnostic ref precedence)
# ======================================================================================


def test_ref_prefers_the_pull_request_over_the_commit() -> None:
    """Row 5 (``render-template.test.ts:41-52``; ``render-template.ts:43-47``).

    PR beats commit -- the whole point of ``{ref}``: a reader wants the discussion, not the
    raw sha. Every field is asserted, so a builder that returned the right ``ref`` for the
    wrong reason (e.g. by ignoring ``commit`` entirely) still fails.
    """
    tokens = build_release_line_tokens(
        summary_linked="linked",
        pull="[#1](u)",
        commit="[`abc`](u)",
        authors="[@x](u)",
    )
    assert tokens["ref"] == "([#1](u))", "PR wins over commit"
    assert tokens["pull"] == "[#1](u)", "the bare pull token is the unwrapped link"
    assert tokens["commit"] == "[`abc`](u)", "the commit token survives even when ref ignores it"
    assert tokens["authors"] == "[@x](u)"
    assert tokens["summary"] == "linked"


def test_ref_falls_back_to_the_commit_when_there_is_no_pull_request() -> None:
    """Row 6 (``render-template.test.ts:54-63``) -- absent tokens render as ``""``, not None."""
    tokens = build_release_line_tokens(
        summary_linked="msg", pull=None, commit="[`abc`](u)", authors=None
    )
    assert tokens["ref"] == "([`abc`](u))", "no PR -> the commit is the reference"
    assert tokens["pull"] == "", "a missing link is the empty string so templates stay renderable"
    assert tokens["authors"] == ""
    assert tokens["summary"] == "msg"


def test_ref_is_empty_when_there_is_neither_pull_request_nor_commit() -> None:
    """Row 7 (``render-template.test.ts:65-73``) -- no links at all -> no parentheses.

    Load-bearing for row 23 of ``changelog-github/src/index.test.ts``: an empty trailing
    ``{{ ref }}`` is what leaves the dangling space the release line must trim.
    """
    tokens = build_release_line_tokens(summary_linked="msg", pull=None, commit=None, authors=None)
    assert tokens["ref"] == "", "empty, NOT '()' -- an empty parenthesis pair is not markdown"
    assert tokens["commit"] == ""
    assert tokens["pull"] == ""
    assert tokens["authors"] == ""


def test_token_key_set_is_exactly_the_documented_release_line_tokens() -> None:
    """Row 8 (``render-template.test.ts:75-84``) -- locks molt's template context key-set.

    Asserted against a hand-written literal tuple rather than a comprehension over the
    result, so neither a missing nor an extra token passes.

    The two assertions are deliberately different in strength. ``build_release_line_tokens``
    returns a ``dict``, whose iteration order is insertion order and not a contract, so its
    keys are compared *sorted*. ``RELEASE_LINE_TOKENS`` is compared **unsorted**: the P4
    shared brief (section 8) defines it as "the sorted key set
    ``("authors", "commit", "pull", "ref", "summary")``", which makes the order part of the
    constant's definition rather than an incidental property of how it was built. It is also
    the order the unknown-token error message lists tokens in, and a stable order there is
    the difference between a diffable error string and a churning one.
    """
    tokens = build_release_line_tokens(summary_linked="m", pull=None, commit=None, authors=None)
    assert tuple(sorted(tokens)) == EXPECTED_TOKEN_KEYS, (
        "the render context is exactly these five keys -- adding one silently expands the "
        "public template surface, dropping one breaks documented templates"
    )
    assert tuple(RELEASE_LINE_TOKENS) == EXPECTED_TOKEN_KEYS, (
        "RELEASE_LINE_TOKENS is what the unknown-token error advertises; it must agree with "
        "what build_release_line_tokens produces AND be in the sorted order the shared brief "
        "defines (P4 shared brief section 8)"
    )


def test_every_documented_token_renders_from_a_built_context() -> None:
    """Rows 5-8 tied together: the two halves of the module must actually compose.

    Row 8 pins the key-set and rows 1-4 pin rendering, but nothing upstream asserts that a
    context built by ``buildReleaseLineTokens`` renders through ``renderTemplate`` without
    tripping the unknown-token guard. This is the seam where a rename on one side and not
    the other would slip through.
    """
    tokens = build_release_line_tokens(
        summary_linked="msg", pull="[#1](u)", commit="[`abc`](u)", authors="[@x](u)"
    )
    template = " ".join(f"{{{{ {name} }}}}" for name in EXPECTED_TOKEN_KEYS)
    assert render_template(template, tokens) == "[@x](u) [`abc`](u) [#1](u) ([#1](u)) msg"
