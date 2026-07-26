"""Group 8 (infra/forge/utils) behaviors molt deliberately does NOT port.

Audit record, not coverage. One skipping test per dropped upstream behavior, each citing the
group-file row and the upstream ``file:line`` it comes from, so every divergence stays greppable
(``uv run pytest -m not_ported``).

Source: ``roadmap/research/test-suite/08-infra-forge-utils.md``, the
``packages/cli/src/utils/createPromiseQueue.test.ts`` (9 rows) and
``packages/cli/src/utils/getLastJsonObjectFromString.test.ts`` (9 rows) sections -- both Drop in
full.

``createPromiseQueue`` (``createPromiseQueue.ts:8-60``) is a hand-rolled bounded-concurrency
promise queue. Its main upstream consumer is the OTP re-queue (concurrency 1<->10) in publish -- a
subsystem molt deletes outright, because PyPI has no 2FA-at-publish (research README section
4.4). If molt ever batches forge calls or parallel-publishes, the idiomatic replacement is
``asyncio.Semaphore`` + ``gather``/``TaskGroup``: start-under-limit, block-at-limit, result and
exception propagation are all language-guaranteed and need no bespoke test the way the hand-rolled
queue does.

``getLastJsonObjectFromString`` (``getLastJsonObjectFromString.ts:1-15``) is an npm-stdout
scraper: it right-to-left-scans ``npm publish --json`` output for the trailing JSON object. molt
publishes via ``uv``/``twine`` (no JSON-on-stdout) and reads release facts from the PyPI JSON API,
so there is nothing to scrape. If a future ``--format json`` path ever needs the same trick, the
one-line stdlib replacement is a right-to-left scan with ``json.JSONDecoder().raw_decode`` instead
of upstream's regex-strip-and-retry loop.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


# ======================================================================================
# createPromiseQueue.test.ts -- 9 rows, all Drop
# ======================================================================================


def test_create_promise_queue_starts_jobs_immediately_before_the_limit_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:13-27 -- 'starts jobs immediately before hitting the "
        "concurrency limit'. asyncio.Semaphore(n) acquires immediately for the first n "
        "concurrent tasks; this is a guarantee of the primitive, not something molt needs its "
        "own regression test for (group 8, createPromiseQueue row 1; research README "
        "section 4.4)."
    )


def test_create_promise_queue_blocks_a_job_past_the_limit_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:29-41 -- 'does not start a job immediately after hitting "
        "the concurrency limit'. Semaphore.acquire() blocking the (n+1)th coroutine is language "
        "-guaranteed (group 8, createPromiseQueue row 2)."
    )


def test_create_promise_queue_starts_the_next_job_below_the_limit_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:43-59 -- 'starts a next job after going below the "
        "concurrency limit'. Semaphore.release() unblocking the next waiter on task completion "
        "is language-guaranteed (group 8, createPromiseQueue row 3)."
    )


def test_create_promise_queue_resolves_with_the_original_result_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:61-68 -- 'resolves with the original result'. An "
        "asyncio.Task's result is exactly what its coroutine returned; no bespoke result-plumbing "
        "exists to regress (group 8, createPromiseQueue row 4)."
    )


def test_create_promise_queue_rejects_with_the_original_error_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:70-80 -- 'rejects with the original error'. asyncio "
        "exception propagation through gather()/TaskGroup re-raises the original exception "
        "object, not a wrapper (group 8, createPromiseQueue row 5)."
    )


def test_create_promise_queue_drains_pending_jobs_after_a_rejection_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:82-95 -- 'drains pending jobs after a rejection'. "
        "SEMANTICALLY IMPORTANT IF THIS IS EVER RE-IMPLEMENTED: a queued job must still run "
        "after an earlier job in the same batch fails. Plain asyncio.gather() cancels sibling "
        "tasks on the first exception by default, which does NOT reproduce this drain-after-"
        "rejection behavior -- gather(..., return_exceptions=True) or an explicit TaskGroup would "
        "be needed. Worth an explicit test only if this queue is ever rebuilt (group 8, "
        "createPromiseQueue row 6)."
    )


def test_create_promise_queue_handles_a_synchronously_throwing_job_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:97-107 -- 'handles a synchronously throwing job' "
        "(the promiseTry ponyfill). Python has no sync/async call-site split to paper over: a "
        "coroutine function that raises before its first await still raises through the normal "
        "await/gather path, so there is no promiseTry analogue to port (group 8, "
        "createPromiseQueue row 7)."
    )


def test_create_promise_queue_drains_pending_jobs_after_a_synchronous_throw_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:109-122 -- 'drains pending jobs after a synchronous throw'. "
        "SEMANTICALLY IMPORTANT IF THIS IS EVER RE-IMPLEMENTED, for the same reason as row 6: "
        "a queued job must still run after an earlier job's *synchronous* failure, which plain "
        "gather() also does not reproduce by default (group 8, createPromiseQueue row 8)."
    )


def test_create_promise_queue_set_concurrency_raises_the_limit_is_dropped() -> None:
    pytest.skip(
        "createPromiseQueue.test.ts:124-138 -- 'setConcurrency allows more jobs to run'. "
        "DYNAMIC-CONCURRENCY-RAISE IS THE ONE BEHAVIOR WORTH AN EXPLICIT TEST IF THIS IS EVER "
        "RE-IMPLEMENTED: asyncio.Semaphore has no public resize API, so raising a limit at "
        "runtime needs a hand-rolled release-N-permits helper. It only existed upstream for the "
        "OTP TTY concurrency toggle (1<->10), which molt deletes outright -- PyPI has no "
        "2FA-at-publish (group 8, createPromiseQueue row 9; research README section 4.4)."
    )


# ======================================================================================
# getLastJsonObjectFromString.test.ts -- 9 rows, all Drop
# ======================================================================================


def test_get_last_json_object_handles_a_stringified_object_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:5-9 -- 'handles stringified object'. molt has no "
        "npm-stdout to scrape (uv/twine emit no JSON-on-stdout); release facts come from the "
        "PyPI JSON API instead (group 8, getLastJsonObjectFromString row 1)."
    )


def test_get_last_json_object_handles_a_deep_object_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:11-23 -- 'handles stringified deep object' "
        "(nested objects/arrays). Same scraper, same non-target (group 8, "
        "getLastJsonObjectFromString row 2)."
    )


def test_get_last_json_object_handles_leading_whitespace_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:25-31 -- 'handles leading whitespace'. If a future "
        "molt '--format json' path ever needs to scrape trailing text, the stdlib replacement is "
        "a right-to-left scan with json.JSONDecoder().raw_decode, which tolerates surrounding "
        "whitespace for free (group 8, getLastJsonObjectFromString row 3)."
    )


def test_get_last_json_object_handles_trailing_whitespace_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:33-39 -- 'handles trailing whitespace'. Same "
        "stdlib replacement as row 3 (group 8, getLastJsonObjectFromString row 4)."
    )


def test_get_last_json_object_handles_trailing_text_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:41-47 -- 'handles trailing text' (ignores a "
        "non-JSON tail). raw_decode naturally stops at the end of the parsed object and ignores "
        "whatever follows (group 8, getLastJsonObjectFromString row 5)."
    )


def test_get_last_json_object_returns_the_last_of_multiple_objects_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:49-58 -- 'handles string with multiple objects'. "
        "LOAD-BEARING BEHAVIOR IF THIS IS EVER RE-IMPLEMENTED: the function must return the "
        "*last* JSON object in the string, not the first -- a right-to-left "
        "json.JSONDecoder().raw_decode scan (trying each '{' from the end backwards) reproduces "
        "this; a naive left-to-right decode would silently return the wrong object (group 8, "
        "getLastJsonObjectFromString row 6)."
    )


def test_get_last_json_object_returns_none_for_an_empty_string_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:60-62 -- 'returns null for an empty string'. "
        "raw_decode on an empty string raises json.JSONDecodeError, which the stdlib "
        "replacement would catch and translate to None -- no scraper to test (group 8, "
        "getLastJsonObjectFromString row 7)."
    )


def test_get_last_json_object_returns_none_for_a_broken_object_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:64-66 -- 'returns null for a string with a broken "
        'object\' (unterminated ``{"bar:"``). Same non-target as row 7 (group 8, '
        "getLastJsonObjectFromString row 8)."
    )


def test_get_last_json_object_returns_none_without_an_object_is_dropped() -> None:
    pytest.skip(
        "getLastJsonObjectFromString.test.ts:68-70 -- 'returns null for a string without an "
        "object' (bare 'qwerty'). Same non-target as rows 7-8 (group 8, "
        "getLastJsonObjectFromString row 9)."
    )
