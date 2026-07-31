"""Changeset id generation -- the ``human-id`` port (research doc 02 section 6).

Upstream calls ``humanId({ separator: "-", capitalize: false })`` from ``human-id@^4.2.0``
(``write/src/index.ts:41-44``) and gets ``adjective-noun-verb``, all lowercase, nouns plural and
verbs agreeing with the plural noun -- ``orange-foxes-waggle``, ``cool-computer-club``. molt vendors
the scheme rather than the library: research doc 02 section 12.6 recommends exactly that, because a
three-list word bank is the whole of what the dependency contributes.

**The id carries no meaning and never has.** ``read/src/index.test.ts:50-52`` enshrines it: "I just
want it enshrined in the tests that the file name's format is in no way part of the changeset spec".
It is load-bearing only as an *identity* -- :func:`molt.apply.apply_release_plan` deletes
``<id>.md`` after versioning, so an id that does not round-trip leaves a consumed changeset on disk
and the next ``molt version`` double-bumps.

Two deliberate divergences from ``human-id``:

- **The word bank is smaller.** ``human-id`` ships 338 adjectives, 419 nouns and 392 verbs for a
  pool of about 15 million; the lists below are hand-vendored and give roughly 3 million. That is
  not a faithfulness problem, because of the second divergence.
- **Collisions are handled.** Upstream calls ``humanId()`` once and writes with ``fs.writeFile``,
  which silently *overwrites* an existing changeset -- research doc 02 section 12.6 calls the
  ``while path.exists(): regenerate`` guard "a 2-line improvement". :func:`unique_changeset_id`
  is that guard, so the pool size only has to make a retry rare, not impossible.

Selection is :func:`random.choice`, matching upstream's ``Math.random`` -- unseeded and not
cryptographic. Nothing about a changeset id is a secret.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Final

from molt.errors import MoltError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "ADJECTIVES",
    "MAX_ID_ATTEMPTS",
    "NOUNS",
    "VERBS",
    "generate_changeset_id",
    "unique_changeset_id",
]

#: How many times :func:`unique_changeset_id` re-rolls before giving up. A repository would need a
#: pathological number of pending changesets to reach this; hitting it means something is wrong with
#: the generator, not with the repository, so it fails loudly rather than overwriting a file.
MAX_ID_ATTEMPTS: Final = 10

# fmt: off
# The three word banks are laid out ten-per-line on purpose and `ruff format` is turned off over
# them: exploded one-per-line they are ~500 lines of noise in every diff that touches this file.

#: The first word. Lowercase, ASCII, and deliberately dull: an id is read aloud in review comments.
ADJECTIVES: Final[tuple[str, ...]] = (
    "afraid", "ancient", "angry", "beige", "big", "bitter", "blue", "bold", "brave", "bright",
    "brown", "bumpy", "busy", "calm", "chatty", "chilly", "chubby", "clean", "clever", "cloudy",
    "clumsy", "cool", "cuddly", "curly", "curvy", "cute", "damp", "dirty", "dry", "dull",
    "eager", "early", "empty", "fair", "famous", "fancy", "fast", "fine", "flat", "fluffy",
    "foolish", "fresh", "friendly", "funny", "fuzzy", "gentle", "giant", "gifted", "golden", "good",
    "great", "green", "grumpy", "happy", "hard", "healthy", "heavy", "honest", "hot", "hungry",
    "itchy", "jolly", "kind", "large", "late", "lazy", "light", "little", "lively", "loose",
    "loud", "lovely", "lucky", "mean", "mighty", "modern", "moody", "neat", "nervous", "nice",
    "noisy", "odd", "olive", "orange", "plain", "polite", "poor", "popular", "pretty", "proud",
    "purple", "quick", "quiet", "rare", "rich", "ripe", "rotten", "round", "rude", "sad",
    "salty", "shaggy", "sharp", "shiny", "short", "shy", "silent", "silly", "slick", "slimy",
    "slow", "small", "smart", "smooth", "soft", "sour", "spicy", "spotty", "stale", "steady",
    "sticky", "strange", "strong", "sweet", "swift", "tall", "tame", "tasty", "tender", "thick",
    "thin", "tidy", "tiny", "tough", "tricky", "true", "ugly", "vast", "violet", "warm",
    "weak", "wet", "wicked", "wide", "wild", "wise", "witty", "yellow", "young", "zany",
)

#: The second word. **Plural**, so the verb below can agree with it without inflection.
NOUNS: Final[tuple[str, ...]] = (
    "apples", "apricots", "bananas", "bats", "beans", "bears", "beds", "bees", "bikes", "birds",
    "boats", "books", "bottles", "boxes", "brooms", "buckets", "bugs", "buses", "cameras",
    "candles",
    "carrots", "cars", "cats", "chairs", "cheetahs", "cherries", "chickens", "cities", "clocks",
    "clouds", "coats", "coins", "cooks", "cows", "crabs", "cups", "cycles", "dancers", "days",
    "dingos", "dogs", "dolls", "donkeys", "doors", "dots", "dragons", "drinks", "ducks", "eagles",
    "ears", "eels", "eggs", "elephants", "emus", "eyes", "falcons", "fans", "ferns", "fields",
    "files", "fires", "flies", "flowers", "foxes", "frogs", "games", "gates", "geese", "ghosts",
    "giraffes", "goats", "grapes", "guests", "hands", "hats", "hills", "hornets", "horses",
    "hotels",
    "houses", "icons", "insects", "islands", "jars", "jeans", "jobs", "kings", "kiwis", "ladybugs",
    "lamps", "lands", "lemons", "lions", "lizards", "llamas", "mangos", "maps", "masks", "melons",
    "mice", "mirrors", "moles", "monkeys", "moons", "mountains", "mugs", "nails", "needles",
    "olives", "onions", "otters", "owls", "pandas", "papers", "parrots", "peaches", "pears", "pens",
    "pianos", "pigs", "pillows", "planes", "planets", "plants", "plums", "poems", "pots", "pumas",
    "rabbits", "radios", "rats", "rings", "rivers", "rocks", "roses", "rules", "seals", "shirts",
    "shoes", "singers", "sloths", "snails", "snakes", "socks", "spoons", "squids", "stars",
    "streets", "suns", "swans", "tables", "teachers", "tigers", "times", "toes", "tools", "towns",
    "toys", "trains", "trees", "turtles", "walls", "wasps", "waves", "ways", "wolves", "words",
    "worms", "years", "zebras", "zoos",
)

#: The third word. Present tense, plural agreement (``words combine``, never ``words combines``).
VERBS: Final[tuple[str, ...]] = (
    "accept", "act", "agree", "appear", "applaud", "arrive", "ask", "attack", "bake", "bathe",
    "beam", "beg", "behave", "boil", "bow", "breathe", "burn", "call", "camp", "care",
    "carry", "cheat", "cheer", "chew", "clap", "clean", "climb", "collect", "combine", "complain",
    "cough", "count", "cover", "crash", "create", "cry", "dance", "decide", "deliver", "deny",
    "depend", "design", "develop", "divide", "double", "doubt", "drag", "dream", "dress", "drive",
    "drop", "drum", "eat", "encourage", "end", "enjoy", "escape", "exist", "explain", "fail",
    "fetch", "film", "fix", "float", "flow", "fly", "fold", "follow", "fry", "gather",
    "glow", "grab", "greet", "grin", "guess", "hammer", "hang", "happen", "heal", "hear",
    "help", "hide", "hope", "hug", "hunt", "invent", "invite", "joke", "judge", "jump",
    "kick", "kneel", "knock", "laugh", "learn", "leave", "lend", "lick", "listen", "live",
    "look", "love", "march", "matter", "melt", "mix", "move", "nod", "obey", "offer",
    "open", "paint", "pause", "peel", "permit", "pick", "play", "poke", "pour", "press",
    "pull", "pump", "punch", "race", "reach", "read", "refuse", "relax", "remain", "repair",
    "reply", "rescue", "rest", "retire", "return", "rhyme", "ride", "ring", "roll", "rule",
    "run", "rush", "sail", "save", "scream", "search", "sell", "send", "serve", "share",
    "shine", "shiver", "shop", "shout", "sing", "sip", "sit", "skate", "sleep", "slide",
    "smell", "smile", "sneeze", "sniff", "sort", "sparkle", "speak", "sprout", "stare", "start",
    "stay", "stop", "study", "swim", "talk", "tap", "taste", "teach", "tickle", "travel",
    "trot", "trust", "turn", "vanish", "visit", "wait", "walk", "wander", "wave", "whisper",
    "win", "wink", "wonder", "work", "worry", "yawn", "yell",
)
# fmt: on


def generate_changeset_id() -> str:
    """One ``adjective-noun-verb`` changeset id, lowercase, ``-`` separated.

    The seam ``tests/cli/test_add.py::seed_changeset_ids`` patches to make filenames deterministic
    (upstream's ``vi.mock("human-id")``), which is why it is a module-level function re-exported
    from :mod:`molt.changeset` rather than a method on a writer object.
    """
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.choice(VERBS)}"


def unique_changeset_id(
    directory: Path, generate: Callable[[], str] = generate_changeset_id
) -> str:
    """An id whose ``<id>.md`` does not already exist in ``directory``.

    The guard upstream does not have (research doc 02 section 12.6). ``generate`` is a parameter
    rather than a direct call so the caller can pass the module attribute it looked up itself --
    which is what keeps the test seam patchable through :mod:`molt.changeset`.

    Raises:
        MoltError: :data:`MAX_ID_ATTEMPTS` consecutive ids were already taken. Overwriting instead
            would silently destroy an unreleased changeset.
    """
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = generate()
        if not (directory / f"{candidate}.md").exists():
            return candidate
    raise MoltError(
        f"Could not find an unused changeset id after {MAX_ID_ATTEMPTS} attempts in {directory}."
    )
