"""Changelog behavior molt deliberately does NOT port, kept as an auditable record.

Scope: the two changelog sections of ``roadmap/research/test-suite/04-apply-changelog.md``
-- ``packages/changelog-github/src/index.test.ts`` (25 rows) and
``packages/changelog-github/src/render-template.ts`` (8 rows). The group file records
**0 Drop rows** across both: every upstream *test* has a molt counterpart in
``tests/changelog/test_changelog.py`` or ``tests/changelog/test_render_template.py``.

What is recorded here instead are the divergences underneath those rows: mostly
**structural** ones -- mechanisms molt replaces wholesale, which therefore have no molt
test and would otherwise leave no trace -- plus one **output-format** divergence
(``test_empty_updated_dependencies_bracket_group_is_dropped``) that is not on research
README section 3.4's bug table and needs owner sign-off. Each entry cites the upstream
source and the research section that decided it.

Explicitly NOT recorded here, by instruction of
``roadmap/tdd-ddd/phases/phase-P4-apply-changelog.md``: the two upstream tests that pin
BROKEN blank-line output (group-file index rows 29 and 32). Those are **adapted** to
clean-markdown expectations by the apply-side tests, not dropped.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


def test_bespoke_brace_token_template_engine_is_dropped() -> None:
    pytest.skip(
        "changesets' hand-rolled {token} engine -- a TOKEN_REGEX driving String.replace with "
        "an Object.hasOwn guard (render-template.ts:4-28) -- is replaced wholesale by Jinja2 "
        "(research README section 5 item 5: changelog templating is issue #109, open 7 years). "
        "Token semantics port; the engine does not. In molt a single-brace {summary} is "
        "ordinary text, pinned by test_render_template.py."
    )


def test_thanks_template_token_is_dropped() -> None:
    pytest.skip(
        "The {thanks} token was removed upstream before v3 and molt does not resurrect it "
        "(render-template.ts:6-12 lists the five survivors; render-template.test.ts:33-37 "
        "asserts the rejection). Attribution is composed from {authors} plus literal text, so "
        "the template author owns the wording. The rejection itself IS tested in "
        "test_render_template.py; what is dropped is the token."
    )


def test_identity_keyed_dataloader_cache_is_dropped() -> None:
    pytest.skip(
        "get-github-info's module-level DataLoader (dataloader.ts:62; its two loader "
        "entry points at dataloader.ts:64-72) builds a fresh "
        "{...options, kind} object per call with no cacheKeyFn, so its cache is keyed by "
        "object identity and never hits (research README section 3.4; doc 04 section 5.4). "
        "molt keys by (kind, repo, id) with no DataLoader and no microtask-tick batching "
        "model, so the JS batching semantics have no counterpart. The corrected behavior is "
        "pinned by the three cache tests in test_changelog.py."
    )


def test_maxbatchsize_50_as_the_only_rate_limit_mitigation_is_dropped() -> None:
    pytest.skip(
        "Upstream's sole defense against throttling is DataLoader's maxBatchSize of 50 -- "
        "there is no inspection of x-ratelimit-*, no Retry-After, no backoff, no retry, no 5xx "
        "handling and no timeout (doc 04 section 5.5). molt replaces the batch-size heuristic "
        "with real resilience (doc 04 section 5.9; website/docs/forges/github.md), so the "
        "50-entity constant is not a molt behavior to test."
    )


def test_duplicated_github_only_env_resolution_is_dropped() -> None:
    pytest.skip(
        "changesets resolves GITHUB_SERVER_URL / GITHUB_REPOSITORY twice, in two independent "
        "copies of the same logic (changelog-github/src/index.ts:23-50 and "
        "get-github-info/src/env.ts:1-34), each caching the parsed .env in a module-level "
        "promise. molt resolves once behind the forge seam (research README section 5 item 7 "
        "-- forge-agnosticism; website/docs/forges/overview.md), so neither the duplication "
        "nor the module-global cache is ported. The env VARIABLES themselves are kept "
        "(website/docs/forges/github.md) and are exercised in test_changelog.py."
    )


def test_content_type_less_graphql_post_is_dropped() -> None:
    pytest.skip(
        "dataloader.ts:100-138 posts a JSON string with no Content-Type header, so fetch "
        "defaults it to text/plain;charset=UTF-8 and GitHub tolerates it (doc 04 section 5.5). "
        "molt sends application/json (section 5.9 recommendation), asserted in "
        "test_changelog.py, so the upstream quirk is a divergence rather than a ported fact."
    )


def test_empty_updated_dependencies_bracket_group_is_dropped() -> None:
    pytest.skip(
        "changelog-github emits a LITERAL empty bracket group when no changeset in the group "
        "carries a commit: index.ts:79-90 builds `- Updated dependencies [${...filter(_ => _)"
        ".join(', ')}]:`, so the .filter() empties the group but the brackets stay, producing "
        "`- Updated dependencies []:`. Verified against the reference clone at "
        "changelog-github/src/index.ts:79-90. molt drops the empty group and emits "
        "`- Updated dependencies:` instead (pinned by "
        "test_dependency_release_line_omits_empty_brackets_when_no_changeset_has_a_commit in "
        "test_changelog.py). "
        "OWNER SIGN-OFF NEEDED -- this divergence is on neither research README section 3.4's "
        "bug table nor the P4 shared brief section 9 load-bearing list, and `[]:` is valid "
        "Markdown rather than broken output, so it is weaker than the two blank-line "
        "divergences it was reasoned from (README section 5 item 13, 'correct changelog "
        "markdown'). It is also not a corner case: a local `molt version` with no commit "
        "recorded on the changeset is the COMMON path, so this changes the default output of "
        "the github generator outside CI. Recorded here so the decision is auditable; revert "
        "the divergence and this record together if the owner prefers upstream fidelity."
    )


def test_double_cjs_default_unwrap_in_plugin_resolution_is_dropped() -> None:
    pytest.skip(
        "apply-release-plan/src/index.ts unwraps `.default` up to twice for CJS / __esModule "
        "interop when loading a changelog plugin (doc 04 section 4.2). Python has no dual "
        "module format, so molt resolves a generator by entry point, dotted path, or file path "
        "with no unwrapping at all (doc 04 section 4.4; "
        "website/docs/extending/changelog-plugins.md)."
    )
