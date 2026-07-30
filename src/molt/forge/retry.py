"""The retry policy every forge backend shares: bounded attempts, jittered, ``Retry-After``-aware.

Upstream has none of this. ``get-github-info/src/dataloader.ts:100-131`` issues one request, reads
no ``x-ratelimit-*`` header, honours no ``Retry-After``, sets no timeout, and parses a 5xx body as
JSON -- so one GitHub blip mid-release fails the whole run with a parse error, and a throttled
request surfaces as "Fetched data from GitHub has missing data", which sends the operator looking
for the wrong bug (research README section 3.4; research doc 04 section 5.5).

Everything here is host-neutral and pure: HTTP status classes and the standard ``Retry-After``
header, with no GitHub vocabulary and no I/O except :func:`sleep_for`. ``website/docs/forges/
gitlab-gitea-others.md`` promises that "the generic caching and backoff behavior ... is part of the
shared client, so a new backend inherits real request-deduplication and retry rather than
reinventing them" -- this module is that promise for the retry half.

Why every bound is explicit (design D5)
---------------------------------------
An unbounded retry loop passes every happy-path test and then hangs the CLI against a permanently
failing endpoint, so "bounded" is asserted on **three** axes and each one closes a hole the others
leave open:

* :data:`MAX_ATTEMPTS` -- without an attempt cap the release never finishes.
* :data:`MAX_BACKOFF_SECONDS` -- without a per-nap cap, attempt five sleeps for an hour.
* :data:`MAX_TOTAL_BACKOFF_SECONDS` -- without a total cap, four individually-capped naps still
  add up to an unacceptable stall.

``tests/forge/test_github.py::test_a_permanently_failing_endpoint_gives_up_within_a_bounded_budget``
asserts all three, and asserts an attempt **count** rather than merely that the call returns.
"""

from __future__ import annotations

import random
import time

__all__ = [
    "MAX_ATTEMPTS",
    "MAX_BACKOFF_SECONDS",
    "MAX_TOTAL_BACKOFF_SECONDS",
    "TRANSIENT_STATUSES",
    "backoff_delay",
    "is_transient_status",
    "retry_after_seconds",
    "sleep_for",
]

#: Total attempts, first try included -- so three retries at most. Upstream retries zero times, so
#: there is no number to port; this one is chosen so a wedged endpoint costs a small, bounded
#: handful of calls. The conformance suite's ceiling is 5, and its floor is 2 (a client that never
#: retries fails it), so this value may be tightened to 2 or raised to 5 without touching a test.
MAX_ATTEMPTS = 4

#: Where the doubling starts. Small on purpose: a GitHub blip clears in well under a second, and
#: the first retry is the one that resolves nearly all of them.
BASE_BACKOFF_SECONDS = 0.5

#: What each attempt multiplies the previous delay by.
BACKOFF_FACTOR = 2.0

#: Ceiling on any single nap, ``Retry-After`` included. A server asking for longer than this is
#: telling molt the wait is not worth spending inside one release run; the request gives up
#: instead, with the status in the message, rather than blocking the CLI for the server's window.
MAX_BACKOFF_SECONDS = 30.0

#: Ceiling on the sum of every nap in one request's retry budget.
MAX_TOTAL_BACKOFF_SECONDS = 90.0

#: Statuses worth another attempt. 429 is here because a *primary* rate limit is a wait, not a
#: refusal, and the caller reports the exhausted-budget case separately once the retries are spent.
#: **403 is deliberately absent and is an open question, not an oversight:** GitHub answers both
#: "permanently forbidden" and "secondary rate limit" with 403, so treating it as transient retries
#: a bad token three times and treating it as permanent gives up on a throttle that would have
#: cleared. The conformance suite pins neither, by design -- a test writer should not invent
#: product behavior here. It is filed for an owner ruling (change 12 tasks.md section 7.1); until
#: then a 403 is permanent, which is the choice that cannot make a bad token worse.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


def is_transient_status(status: int) -> bool:
    """Whether ``status`` is worth another attempt.

    Every 5xx counts, not only the four named ones: a proxy in front of a host can invent 507 or
    520, and "the server broke" is transient whichever number it picks. Every other 4xx is
    permanent -- a malformed query stays malformed and a bad token stays bad, so retrying only
    burns the rate limit that the exhaustion message then complains about.
    """
    return status in TRANSIENT_STATUSES or 500 <= status < 600


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value in **seconds**, or ``None`` if it says nothing usable.

    RFC 9110 also allows an HTTP-date, which is deliberately not parsed: GitHub sends
    delta-seconds, and a wrong date parse would produce a nap of the wrong order of magnitude
    rather than a visible failure. An unparseable value falls back to the computed backoff, which
    is always safe -- see :func:`backoff_delay`.
    """
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def backoff_delay(attempt: int, *, retry_after: float | None = None) -> float:
    """Seconds to wait after a failed ``attempt`` (1-based), always ``> 0``.

    Exponential from :data:`BASE_BACKOFF_SECONDS`, then jittered **downwards** into the upper half
    of the window so the delay stays inside the bound the docstring above promises while two
    machines releasing at once still separate. ``Retry-After`` is a **floor**, not a hint: GitHub
    sends it on secondary rate limits, and ignoring it is what escalates a throttle into a block.
    Both are then clamped to :data:`MAX_BACKOFF_SECONDS`.

    The result is never zero. A retry with no wait is a hammer, not a backoff, and it is the shape
    that turns one throttled request into a block.
    """
    window = BASE_BACKOFF_SECONDS * (BACKOFF_FACTOR ** max(attempt - 1, 0))
    # `random`, not `secrets`: this is jitter to spread two concurrent releases apart, not a
    # security decision, and a predictable schedule is easier to reason about in a bug report.
    delay = window * random.uniform(0.5, 1.0)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(delay, MAX_BACKOFF_SECONDS)


def sleep_for(seconds: float) -> None:
    """Wait, via a ``time.sleep`` attribute lookup made at call time.

    The indirection is the test seam: ``tests/forge/test_github.py`` and
    ``tests/changelog/test_changelog.py`` both patch ``"time.sleep"`` by name and record the
    arguments, so the backoff **bounds** are asserted rather than tolerated. ``from time import
    sleep`` would bind the real function at import and make every retry row wait for real.
    """
    time.sleep(seconds)
