# molt test suite -- conventions

This suite is an **executable spec**: a full pytest tree the port must eventually turn green. It is
mapped, test-by-test, to the reference `changesets` suite in
`roadmap/research/changesets-07-test-suite.md` (the TDD map) and its per-group detail files in
`roadmap/research/test-suite/`. Read the map first; this file is the how-to for writing tests that fit.

Match the style of the two already-green files exactly: `tests/versioning/test_bump.py` and
`tests/versioning/test_ranges.py`.

## Layout

Tests mirror `src/molt/`, one package per reference group (test-suite map section 3):

```
tests/
  conftest.py          <- shared, product-code-free fixtures (see below)
  versioning/          <- group 1  (GREEN: test_bump.py, test_ranges.py; + test_pre.py net-new)
  engine/              <- group 2  (dependents graph) + group 2/4 (assemble-release-plan, the moat)
  config/  changeset/  <- group 3
  apply/   changelog/  <- group 4
  cli/                 <- groups 5, 6
  publish/             <- group 7
  git/  forge/  action/ + test_errors.py test_logger.py test_commit.py  <- group 8
```

## The importorskip guard (for not-yet-implemented modules)

Every test file that targets an unbuilt `molt` module must guard its import so the suite stays green
until the module lands, then lights up automatically:

```python
import pytest

pytest.importorskip("molt.engine", reason="build step 4 -- engine not yet implemented (TDD target)")

# pyrefly: ignore[missing-import]  -- molt.engine is the TDD target of build step 4.
from molt.engine import ReleasePlan, assemble_release_plan
```

`importorskip` SKIPs the whole module while `import molt.engine` raises `ModuleNotFoundError`. The
moment the module imports, the real assertions run -- no edit to the test needed. Put it at module top,
before the `from molt...` imports. The already-green versioning files do not need the guard because
`molt.versioning` exists.

**Do not add `# noqa: E402` here.** An earlier revision of this file prescribed it; that was wrong.
Under this repo's ruff config `E402` does not fire after a `pytest.importorskip` call, so the
directive is unused and `RUF100` then fails `ruff check`. Silence the unresolved import for the
not-yet-built module with `# pyrefly: ignore[missing-import]` instead (the repo convention -- not
`# type: ignore[...]`).

## Importing shared test helpers

`--import-mode=importlib` does not put test directories on `sys.path`, so `from fake_state import ...`
fails from a sibling module. `pyproject.toml` sets `pythonpath = ["."]`, so import helpers by their
full dotted path from the repo root; `[tool.pyrefly] search-path` includes `"."` so the type checker
resolves them too, with no suppression needed:

```python
from tests.engine.fake_state import DepEntry, FakeFullState, package_dir
```

## Drops -- deliberately-not-ported behavior

Behaviors molt deliberately does NOT port (test-suite map section 2; research README section 4.4) are
recorded, not deleted, so the divergence stays auditable. Put them in
`tests/<area>/test_deliberately_not_ported.py`, one test each, decorated `@pytest.mark.not_ported`,
that skips with a one-line reason:

```python
import pytest


@pytest.mark.not_ported
def test_pre_json_global_mode_is_dropped() -> None:
    pytest.skip(
        "pre.json global pre-mode is replaced by the stateless `molt version --pre` flag "
        "(research README section 4.2); -next.N tags are illegal in PEP 440 anyway."
    )
```

Source the reason from the map's Drop rows. This keeps the 81 dropped behaviors visible and greppable
(`-m not_ported`).

## Style (mirror `tests/versioning/test_bump.py`)

- **Flat `test_*` functions. No test classes.**
- A module-level `CASES` list fed to `@pytest.mark.parametrize`, with a trailing **`why`** column:
  ```python
  CASES = [
      # (old,   bump,           expected, why)
      ("1.0.0", BumpType.PATCH, "1.0.1",  "normal patch"),
  ]

  @pytest.mark.parametrize(("old", "bump", "expected", "why"), CASES)
  def test_inc(old: str, bump: BumpType, expected: str, why: str) -> None:
      assert inc(v(old), bump) == v(expected), why
  ```
- **Property tests**: `hypothesis @given` for invariants (monotonicity, fixpoint termination/confluence).
- **Snapshots**: `syrupy` -- `assert value == snapshot`. Upstream uses inline `toMatchInlineSnapshot`;
  in molt these become syrupy snapshots (release plans, changelogs, GraphQL queries).
- **Docstrings cite sources**: the research doc + section AND the upstream `file:line`
  (e.g. `research doc 01 section 3.3; index.test.ts:115-161`). These citations are load-bearing.
- **ASCII only** in anything that reaches the console (cp1252 safety).
- `from __future__ import annotations` at the top of every module; fully type-annotated (pyrefly strict).

## Shared fixtures (`tests/conftest.py`)

All are pure infrastructure -- none import a `molt` product module. Use the public names verbatim.

| Fixture | What it gives you |
|---|---|
| `tmp_project` | A `ProjectBuilder` rooted in `tmp_path`. `.root`; `.add_package(name, version, deps=..., dev_deps=..., optional_deps=...)`; `.write_changeset(id, releases, summary)`; `.set_config(as_json=False, **opts)`. tomlkit-round-tripped `pyproject.toml` + `.changeset/` + `[tool.molt]`/`.molt/config.json`. |
| `git_repo` | A real, Windows-safe `git` repo (branch `main`, gpg off, background GC off). `.root`; `.run(*args)`; `.commit(msg)`; `.tag(name)`; `.file_url()`; `.shallow_clone(dest, depth)`. |
| `frozen_clock` | The frozen upstream timestamp. `FROZEN_EPOCH_MS` / `FROZEN_DATETIME`; `.freeze(monkeypatch)` patches the provisional seam `molt.clock.now` (no-ops until it exists). |
| `seeded_ids` | Deterministic changeset-id generator (mirrors `vi.mock("human-id")`). Callable; `.next()`; `.take(n)`; `.reset()`. First id is `strange-words-combine`. |
| `pypi_registry` | respx mock of `GET pypi.org/pypi/<name>/json`. `.set_versions(name, versions)`; `.stale` knob makes the upload endpoint return `400 File already exists`. |
| `forge_api` | respx mock of the GitHub GraphQL endpoint. `.set_response(data)`; `.requests`; `.last_query` (snapshot it); `.auth_header()` (assert it). Matches any `.../graphql` host. |

## Test-type markers

Registered in `pyproject.toml` (`[tool.pytest.ini_options].markers`) and used by the milestone map so
`/opsx:propose` can select a build step's gate by kind:

`unit`, `functional`, `integration`, `e2e`, `property`, `snapshot`, `not_ported`, `network`, `git`,
`slow`.

The research detail files use a finer `Type` column (`unit / param / property / snapshot / fs / mock`).
Map it to the markers as: `param` -> the underlying kind plus `parametrize` (not a marker of its own);
`fs` -> `functional` (tmp workspace) or `integration` + `git` (real git); `mock` -> `network`. Pick a
milestone's gate from `roadmap/research/test-suite/MILESTONES.md`, which lists the exact selector and
the test-type composition per build-order step.

## Running

```
uv run pytest                       # whole suite
uv run pytest tests/engine          # one group (directory selector -- the primary gate form)
uv run pytest -m "unit or property" # by test type
uv run pytest -m "not not_ported"   # skip the audit-only drop tests
uv run pytest -k satisfies          # by name
```

Everything runs through uv -- never invoke `pytest`/`python` directly (repo `CLAUDE.md`).
