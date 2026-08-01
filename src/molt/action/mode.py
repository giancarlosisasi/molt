"""Which half of the release loop a run is: the four-case branch matrix.

Ports ``changesets/action`` v1.9.0 ``src/index.ts:57-176`` (read 2026-07-31). This is the decision
that lets **one** workflow file serve both phases of the release loop, and it is a pure function of
two things: what is in ``.changeset/``, and whether the workflow configured a publish command.

The matrix
----------
============================== ================== ============ =======================
Pending changesets             Publish configured Mode         Upstream
============================== ================== ============ =======================
none                           no                 ``NOTHING``  ``index.ts:67-72``
none                           yes                ``PUBLISH``  ``index.ts:73-149``
some, none releasing anything  either             ``NOTHING``  ``index.ts:150-152``
some, at least one releasing   either             ``VERSION``  ``index.ts:153-175``
============================== ================== ============ =======================

Two details that look like accidents and are not
------------------------------------------------
* **"Are changesets pending?" and "is there anything to release?" are different questions.**
  Upstream reports the output from ``changesets.length !== 0`` and branches on
  ``changesets.some(c => c.releases.length > 0)``. A repository holding only *empty* changesets --
  what ``molt add --empty`` writes, to record "this change needs no release" -- therefore reports
  changesets as pending **and** opens no pull request. Collapsing the two would either open an
  empty release pull request or claim there is nothing pending while ``.changeset/`` is not empty.
* **The publish branch is reachable only when there are no changesets at all**, which is exactly
  the state right after the release pull request merges and consumes them. That is the whole
  mechanism behind "one workflow, both phases": the same job runs on every push to the base
  branch, and which half it does is decided by what the merge left behind.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from molt.changeset import CHANGESET_DIR, read_changesets

if TYPE_CHECKING:
    from collections.abc import Sequence

    from molt.changeset import Changeset

__all__ = ["Mode", "mode_for", "read_pending_changesets", "select_mode"]


class Mode(StrEnum):
    """What a single action run does.

    A :class:`~enum.StrEnum` because change 3 writes this value into ``GITHUB_OUTPUT``, where
    everything is text: ``str(Mode.VERSION)`` is ``"version"`` with no conversion step to forget.
    """

    #: Neither phase runs. Reported, not silent -- a workflow that does nothing on every push is a
    #: misconfiguration, and the log line is what makes it findable.
    NOTHING = "nothing"
    #: Run the version script and open or update the release pull request.
    VERSION = "version"
    #: Run the publish command and create the host releases.
    PUBLISH = "publish"


def read_pending_changesets(cwd: Path | str) -> list[Changeset]:
    """Every pending changeset under ``cwd``, or an empty list when there is no changeset directory.

    :func:`molt.changeset.read_changesets` **raises** for a missing ``.changeset/`` because every
    other caller is a command the user ran against a project they believe is initialized, and
    telling them to run ``molt init`` is the useful answer. An action run is the one caller for
    which that is wrong: a repository that has never used changesets, or one whose release pull
    request was merged with ``.changeset/`` deleted, must still be able to run the publish half.
    The directory's absence is "nothing is pending", which is a true statement about it.

    A changeset file that is *present but malformed* still raises, unchanged -- that is a mistake
    somebody made in a file they wrote, and guessing past it would release the wrong packages.
    """
    root = Path(cwd)
    if not (root / CHANGESET_DIR).is_dir():
        return []
    return read_changesets(root)


def mode_for(changesets: Sequence[Changeset], *, publish: bool) -> Mode:
    """The branch matrix, over an already-read changeset list (``index.ts:57-176``).

    Separate from :func:`select_mode` because the outer loop needs both the mode **and** whether
    changesets are pending, and reading ``.changeset/`` twice to answer two questions about the
    same moment is how the two answers come to disagree.
    """
    if not changesets:
        return Mode.PUBLISH if publish else Mode.NOTHING
    if not any(changeset.releases for changeset in changesets):
        return Mode.NOTHING
    return Mode.VERSION


def select_mode(cwd: Path | str, *, publish: bool) -> Mode:
    """Which half of the release loop the repository at ``cwd`` is in.

    ``publish`` is a flag rather than the command itself, deliberately: the mode decision has no
    business knowing how a publish command is spelled or spawned, and taking a ``bool`` keeps this
    a pure ``.changeset/``-only read that a unit test can drive with no subprocess anywhere.
    """
    return mode_for(read_pending_changesets(cwd), publish=publish)
