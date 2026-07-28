"""Writing a changeset to disk, byte-exactly.

Ports ``packages/write/src/index.ts:32-64``; the byte-level template is research doc 02 section 5.5.

The template molt emits (LF only, no BOM)::

    ---
    "<name>": <type>          one line per release, joined by \\n
    ---

    <summary>
                              one trailing LF, and no trailing spaces

**Names are always double-quoted, types never are.** That is load-bearing rather than cosmetic: an
unquoted name beginning with a reserved character is a hard YAML error, so an unquoted writer would
emit files its own parser rejects (doc 02 section 5.3; the source comment at
``write/src/index.ts:50-51`` says the same).

**Trailer divergence.** Upstream ends ``<summary>`` + ``\\n`` + two spaces and no final newline.
Those two spaces are a Markdown hard line break that ``markdownlint`` MD009 flags on every file the
tool writes, so molt emits a single trailing LF instead -- and :mod:`molt.changeset.parse` accepts
all three trailers found in the wild, because a repository migrating from changesets has upstream's
bytes on disk already (doc 02 section 12.6).

**The formatter is a hook the configuration drives, and its default is off** (design D7). molt emits
correct markdown the first time rather than depending on a formatter pass (research README section 5
item 13), and a default that silently spawned a subprocess would make ``molt add`` non-hermetic and
the byte-exact template unenforceable. Upstream's prettier/oxfmt/deno/dprint detection matrix is a
JS-toolchain concern and does not port (research README section 4.4).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from molt.changeset.parse import Changeset
from molt.changeset.read import CHANGESET_DIR
from molt.errors import MoltError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["format_files", "render_changeset", "write_changeset"]


def render_changeset(changeset: Changeset) -> str:
    """Serialize ``changeset`` to the template above.

    An empty changeset collapses the frontmatter to a blank line between two fences
    (``---\\n\\n---``), which parses back as no releases -- that is the file ``molt add --empty``
    produces, and it must survive its own writer.
    """
    frontmatter = "\n".join(
        f'"{release.name}": {release.type.value}' for release in changeset.releases
    )
    return f"---\n{frontmatter}\n---\n\n{changeset.summary}\n"


def write_changeset(
    root_dir: Path | str,
    changeset: Changeset,
    *,
    id: str | None = None,
    format: str | Literal[False] | None = None,
) -> Path:
    """Write ``changeset`` to ``<root_dir>/.changeset/<id>.md`` and return the path.

    ``format`` selects the formatter hook: ``False`` opts out explicitly, a name routes the new file
    through that formatter, and ``None`` -- what the CLI passes when the user has configured nothing
    -- consults the configuration, whose default is also off. The two "off" paths are deliberately
    distinct: only ``None`` reads configuration at all.

    ``id`` is the changeset's filename stem. Generating one is ``molt add``'s job, not this
    function's, so an id must be supplied here or already be on the changeset.
    """
    changeset_id = id if id is not None else changeset.id
    if not changeset_id:
        raise MoltError(
            "a changeset needs an id before it can be written; pass id= or set Changeset.id."
        )

    directory = Path(root_dir) / CHANGESET_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{changeset_id}.md"
    # write_bytes, never write_text: text mode opens with `newline=None` and translates every \n to
    # os.linesep, so on Windows the template would land as CRLF and churn every committed changeset
    # on every Windows contributor's machine.
    path.write_bytes(render_changeset(changeset).encode("utf-8"))

    formatter = _resolve_formatter(Path(root_dir), format)
    if formatter is not None:
        format_files([path], cwd=Path(root_dir), formatter=formatter)
    return path


def format_files(paths: Sequence[Path], *, cwd: Path, formatter: str) -> None:
    """Run ``formatter`` over ``paths`` -- the whole of what survives upstream's format layer.

    Resolved on ``PATH`` rather than run through ``sys.executable``: the formatter is the user's
    tool in the user's environment, and molt does not depend on it.
    """
    executable = shutil.which(formatter)
    if executable is None:
        raise MoltError(
            f'the configured formatter "{formatter}" was not found on PATH. Install it, or set '
            "format = false -- molt writes correct markdown without one."
        )
    completed = subprocess.run(
        [executable, *(str(path) for path in paths)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise MoltError(
            f'the formatter "{formatter}" exited with code {completed.returncode}: '
            f"{completed.stderr.strip()}"
        )


def _resolve_formatter(root: Path, requested: str | Literal[False] | None) -> str | None:
    """Decide which formatter, if any, runs over a newly written file (design D7).

    ``False`` returns before configuration is touched at all. That is what keeps "the user opted
    out" and "the user configured nothing" from sharing a code path -- they produce the same
    outcome today, and a single path would make the two indistinguishable the moment one of them
    needs to change.
    """
    if requested is False:
        return None
    if requested is not None:
        return requested or None
    # Imported here, not at module scope: molt.config is a lazy pydantic facade and the import cost
    # belongs to the commands that actually parse a configuration.
    from molt.config import load_config

    config = load_config(root).config
    if config is None:
        return None
    return config.format or None
