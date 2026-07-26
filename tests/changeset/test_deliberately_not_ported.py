"""Group 3 (changeset IO) behaviors molt deliberately does NOT port.

Audit record, not coverage. One skipping test per dropped upstream behavior, each citing the
research section it comes from, so every divergence stays greppable
(``uv run pytest -m not_ported``). Format per ``tests/README.md`` and test contract section 2.

**Reconciliation with the group file.** ``roadmap/research/test-suite/03-config-changeset-io.md``
records **zero Drop rows** for ``parse/src/index.test.ts`` (20 Port), ``read/src/index.test.ts``
(9 Port / 1 Adapt) and ``write/src/index.test.ts`` (2 Port / 2 Adapt) -- all four Drop rows in
that file belong to ``config/src/parse.test.ts`` (the npm ``access`` scope x3 and the peer-only
``onlyUpdatePeerDependentsWhenOutOfRange`` x1), which are recorded in
``tests/config/test_deliberately_not_ported.py``, not here. The group file even says so
explicitly: *"No test in these files pins the ``#``-line-stripping upstream bug -- that bug is in
changelog assembly, not in parse/read/write/config, so there is nothing to DROP for it here."*

Every entry below is therefore a **molt-side structural drop**: upstream behavior that no
reference test pins, but that molt must consciously decline so nobody re-adds it later. Four
entries; none of them subtracts from the group file's 34 rows for this half.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.not_ported


def test_legacy_v1_changeset_format_is_dropped() -> None:
    pytest.skip(
        "The legacy v1 changeset format (a JS/JSON changeset directory, not markdown+YAML "
        "frontmatter) was removed across read/apply-release-plan/release-utils in changesets v3 "
        "and v1 files are now silently ignored. molt is a v3 port and never supported it: "
        "parse_changeset accepts only the v3 markdown + YAML frontmatter grammar "
        "(research README section 3.2 'v3 changed the semantics'; doc 05 section on removed "
        "behaviors; doc 05 'Support v3 format only (no legacy)')."
    )


def test_js_formatter_backend_detection_matrix_is_dropped() -> None:
    pytest.skip(
        "writeChangeset detects and shells out to prettier / oxfmt / deno / dprint via "
        "@changesets/format (write/src/index.ts:11-30), and write/src/index.test.ts:94-132 pins "
        "detect -> 'prettier' and format(..., {formatter: 'oxfmt'}). That whole backend matrix is "
        "a JS-toolchain concern with no Python analogue and does not port (research README "
        "sections 3.2 and 4.4; group file, write rows 2-3). molt keeps only the seam: "
        "format=False skips the hook and the default format='auto' invokes it -- asserted in "
        "tests/changeset/test_write.py against a monkeypatched spy that never names a formatter."
    )


def test_hash_line_stripping_summary_post_processing_is_not_reproduced() -> None:
    pytest.skip(
        "UPSTREAM BUG, deliberately not reproduced: changesets' summary post-processing strips "
        "every line starting with '#', which destroys Markdown headings in changeset "
        "descriptions (research README section 3.4, 'upstream bugs -- do not port these'). It "
        "lives in changelog assembly rather than in parse, so the group file records no Drop row "
        "for it; recorded here anyway so the divergence is greppable from the changeset slice. "
        "The CORRECTED behavior is asserted positively in tests/changeset/test_parse.py :: "
        "test_markdown_headings_in_the_summary_are_preserved and "
        "test_every_markdown_heading_level_and_hash_shaped_line_survives."
    )


def test_npm_scoped_package_namespace_is_dropped() -> None:
    pytest.skip(
        "'@scope/name' is an npm namespace concept; PyPI has a flat global namespace with PEP 503 "
        "normalization instead (research README section 4.5). parse/src/index.test.ts:40-53 is "
        "still ported -- as proof the grammar treats a quoted name as opaque -- but the scope "
        "SEMANTICS (scope-wide access, scope globs like '@pkg/*' in fixed/linked/ignore) have no "
        "molt equivalent. The Python analogue that replaces it is asserted in "
        "tests/changeset/test_parse.py :: test_parse_preserves_the_authors_literal_package_name."
    )
