"""Publish/pack behaviors molt deliberately does **not** port, kept as an auditable record.

One skipping test per dropped behavior (``tests/README.md``, "Drops"), each carrying the reason
from its Drop row in ``roadmap/research/test-suite/07-publish-pack.md`` plus the research citation.
Greppable with ``-m not_ported``.

Coverage of this file -- **all 7 Drop rows of group 7**, which fall into exactly two families:

1. **npm dist-tags** (2 + 2 rows). ``publish/index.test.ts`` rows 2 and 8 guard the ``--tag``
   option; ``publish/e2e.test.ts`` rows 3 and 4 exercise the ``only-pre`` heuristic that reroutes a
   first prerelease to the ``latest`` dist-tag. PyPI has **no dist-tags** -- no ``latest`` pointer,
   no per-tag view of a project, nothing to steer -- so the option, both its guards, and the ~80
   lines of ``only-pre`` logic behind them all disappear together (research README section 4.4;
   group file, "Dies").
2. **OTP / 2FA-at-publish** (3 rows). ``publish/e2e.test.ts`` rows 12, 13 and 14 cover OTP from the
   environment, the web-auth OTP challenge, and the interactive retry loop with its TTY-gated
   concurrency flip (1 <-> 10). **PyPI has no 2FA-at-publish**: an upload is authenticated by an
   API token or, preferably, by an **OIDC Trusted Publishing** exchange that happens once, before
   any upload, with nothing to prompt for mid-run (research README section 4.4; tech-stack section
   11). The whole ``AuthState`` / re-queue machine goes with it -- which is why the frozen
   :class:`tests.publish.fake_publish.FakeOIDC` has no interactive surface at all.

What is **not** recorded here, deliberately:

* The **5-package-manager output matrix** (npm 10/11/12, pnpm 10/11, yarn 4) and its per-PM error
  codes. That is not a Drop row of its own -- it is a fixture axis that multiplies rows 1-14, and
  it collapses because molt has one path (``python -m build`` + twine). The rows it multiplies are
  Adapt, not Drop, and belong to ``tests/publish/test_publish.py`` / ``test_pack.py``.
* ``getPublishPlan`` row 5's ``tag: "latest"`` half. The row as a whole is **Adapt**, not Drop: the
  surviving claim ("a local prerelease that is not in the published set gets published") is ported
  in ``tests/publish/test_plan.py::test_a_local_prerelease_absent_from_the_published_set_is_
  included``, which asserts the absence of the ``tag`` key rather than recording a drop here.

**New in molt with no upstream row to drop:** ``molt yank`` (PEP 592) is a recovery verb npm cannot
offer at all, and exhaustive pre-flight validation replaces changesets' per-package validation
because a partial PyPI publish cannot be rolled back (research README section 4.3). Those are
additions, so they are pinned as real tests, not recorded here.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# ======================================================================================
# Family 1 -- npm dist-tags and the `--tag` option (publish/index rows 2, 8)
#
# `--tag` exists to point an npm dist-tag at the version being published. PyPI resolves a
# requirement from the project's full version list; there is no mutable pointer to aim, so the
# option has no meaning, and neither do the two guards that constrain it.
# ======================================================================================


def test_publish_index_row_2_custom_tag_is_rejected_in_pre_mode() -> None:
    pytest.skip(
        "publish/index.test.ts 'in pre state should report error if the tag option is used in pre "
        "release' (index.ts:73-81): `publish({tag})` while `.changeset/pre.json` has mode 'pre' "
        "logs 'Releasing under custom tag is not allowed in pre mode!' and throws ExitError(1). "
        "Doubly dropped: `--tag` is npm dist-tag routing and PyPI has none (research README "
        "section 4.4), and pre.json itself is gone -- molt spells prerelease as the stateless flag "
        "`molt version --pre {a,b,rc,dev}` (research README section 4.2; recorded in "
        "tests/cli/test_deliberately_not_ported.py). A PEP 440 prerelease is published exactly "
        "like any other version."
    )


def test_publish_index_row_8_custom_tag_is_rejected_when_publishing_from_a_pack_dir() -> None:
    pytest.skip(
        "publish/index.test.ts 'rejects custom tags when publishing from a pack directory' "
        "(index.ts:68-71): `{fromPackDir, tag}` logs 'Releasing under custom tag is not allowed in "
        "artifact mode.' and throws ExitError(1). The guard exists because the dist-tag was "
        "already baked into the plan when the artifacts were packed, so a second, different tag at "
        "upload time would silently disagree with the plan file. molt's plan entries carry no "
        "`tag` key at all (tests/publish/test_plan.py::"
        "test_no_plan_entry_carries_an_npm_only_or_camelcase_key), so there is no disagreement to "
        "guard against. `molt publish --from-pack-dir` itself is Adapt and is ported "
        "(group 7 publish/e2e row 7)."
    )


# ======================================================================================
# Family 1 (continued) -- the `only-pre` heuristic (publish/e2e rows 3, 4)
#
# Upstream inspects the packument: if EVERY published version is a prerelease carrying the current
# pre tag, the next prerelease is routed to `latest` instead of the pre tag, so that `npm install
# pkg` finds something at all. PyPI needs no such heuristic -- a resolver picks the highest version
# a requirement admits, and PEP 440 makes it skip prereleases unless the specifier opts in
# (research README section 4.1), which is the same protection without a mutable pointer.
# ======================================================================================


def test_publish_e2e_row_3_only_pre_publish_without_an_existing_latest_tag() -> None:
    pytest.skip(
        "publish/e2e.test.ts:963-1035 '$pm > publishes a new pre version of an only-pre package "
        "without existing latest tag': registry seeded with `1.0.0-beta.0` under dist-tag `beta` "
        "and no `latest`; publishing `1.0.0-beta.1` moves `beta` and leaves `latest` unset. Every "
        "assertion in the row is about dist-tag bookkeeping, which PyPI does not have (research "
        "README section 4.4). Publishing a PEP 440 prerelease is plain publishing and is covered "
        "by tests/publish/test_plan.py::"
        "test_a_local_prerelease_absent_from_the_published_set_is_included."
    )


def test_publish_e2e_row_4_only_pre_publish_with_an_existing_latest_tag() -> None:
    pytest.skip(
        "publish/e2e.test.ts:1037-1109 '$pm > publishes a new pre version of an only-pre package "
        "with existing latest tag': same fixture with `latest` also seeded; publishing "
        "`1.0.0-beta.1` advances `latest` onto a prerelease. This is the `only-pre` heuristic "
        "itself (getPublishPlan.ts:120-134 sets publishedState 'only-pre'; :80-92 getReleaseTag "
        "then returns 'latest'). molt has no `latest` pointer to advance, so the ~80 lines the "
        "research README section 4.4 calls 'the hairiest CLI logic' are deleted rather than ported."
    )


# ======================================================================================
# Family 2 -- OTP / 2FA-at-publish (publish/e2e rows 12, 13, 14)
#
# npm can demand a one-time password *at the moment of upload*, so changesets carries an AuthState
# through the whole publish loop, re-queues EOTP/YN0033 failures, and drops concurrency from 10 to
# 1 whenever a TTY is available so the prompt has somewhere to appear. PyPI authenticates once, up
# front, with an API token or an OIDC Trusted Publishing exchange (tech-stack section 11); PEP 740
# attestations replace npm provenance. Nothing about an upload can ask the user a question, so the
# entire machine -- state, prompts, re-queue, concurrency flip -- has no molt analogue.
# ======================================================================================


def test_publish_e2e_row_12_initial_otp_read_from_the_environment() -> None:
    pytest.skip(
        "publish/e2e.test.ts:1571-1642 '$pm > reads initial otp from env in non-tty mode': "
        "`NPM_CONFIG_OTP` / `PNPM_CONFIG_OTP` seed `AuthState.otpCode` "
        "(publishPackages.ts:22-51 getInitialAuthState) so the `PUT /pkg-a` carries an `otpCode` "
        "and succeeds with 201 in CI, where no prompt is possible. PyPI has no 2FA-at-publish "
        "(research README section 4.4): the CI story is OIDC Trusted Publishing, an exchange that "
        "happens before the first upload and fails loudly there if it fails at all "
        "(tests/publish/fake_publish.py FakeOIDC, `available=False`). Note the row is already "
        "`it.runIf(pm.name !== 'yarn 4')` upstream -- even npm's own ecosystem could not make this "
        "surface uniform."
    )


def test_publish_e2e_row_13_web_auth_otp_failure_in_non_tty_mode() -> None:
    pytest.skip(
        "publish/e2e.test.ts:1644-1721 '$pm > surfaces web-auth OTP publish failures in non-tty "
        "mode' (snapshots at __snapshots__/e2e.test.ts.snap L55-73, L127-147, L201-221 for npm "
        "10/11/12, and L275-, L346-, L414- for pnpm 10/pnpm 11/yarn 4): the registry answers the "
        "upload with an npm web-auth challenge (an auth URL plus a done URL to poll); with no TTY "
        "the run fails EOTP and exits 1, and each package manager phrases it differently. There is "
        "no web-auth flow on PyPI and no per-upload challenge to surface, so both the flow and its "
        "6-way snapshot matrix are dropped (research README section 4.4). The surviving idea -- "
        "'an auth failure is surfaced and the run exits non-zero' -- is Adapt, not Drop, and is "
        "ported from publish/e2e rows 8 and 9 against AuthFailed."
    )


def test_publish_e2e_row_14_interactive_otp_retry_and_tty_gated_concurrency() -> None:
    pytest.skip(
        "publish/e2e.test.ts:1723-1807 '$pm > retries interactively after an OTP auth challenge': "
        "with a TTY, the first `PUT` returns 401 needing an OTP, the user is prompted on stdin, "
        "and the upload is retried with the code -- 3 to 4 `PUT`s for one release -- while "
        "publish concurrency is dropped from 10 to 1 for the duration and restored afterwards "
        "(npm-utils.ts:383-521, internalPublish + the retry loop). Every moving part is npm-only: "
        "the challenge, the prompt, the re-queue, and the concurrency flip that exists only so two "
        "packages cannot prompt at once. molt authenticates once before the first upload and never "
        "prompts mid-run, so its concurrency is a free choice rather than an auth constraint "
        "(research README section 4.4). PyPI's own retryable case is different in kind and IS "
        "ported: a stale read means the index can reject an upload with 400 'File already exists', "
        "which molt maps to *skip*, not fail (group 7 publish/e2e row 11; "
        "tests/publish/fake_publish.py DuplicateUpload)."
    )
