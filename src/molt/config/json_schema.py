"""JSON Schema generation for ``.molt/config.json``.

changesets hand-runs ``@valibot/to-json-schema`` in a build script and commits the result
(``packages/config/scripts/generate-json-schema.ts`` -> ``packages/config/schema.json``), purely so
editors can autocomplete the config file. molt gets the same artifact from pydantic's
``model_json_schema()`` -- that, plus ``AliasChoices``, is what reversed the msgspec decision
(tech-stack section 6; research README section 6).

The schema publishes the **canonical** ``snake_case`` name for every option, because
``AliasChoices`` puts it first and pydantic names the property after the first choice. The
``camelCase`` migration aliases are accepted by the parser but deliberately not advertised: an
editor should complete the spelling molt documents, not the one it tolerates.

What it must never offer is equally load-bearing. An option molt dropped appearing here would have
editors happily autocompleting something the parser refuses -- so the npm scope-permission option,
the peer-dependency flag, the experimental wrapper and the private-packages sub-key molt dropped are
absent from the model and therefore absent from here.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SCHEMA_DIALECT", "config_json_schema"]

#: pydantic emits draft 2020-12, matching the dialect upstream generates with.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def config_json_schema() -> dict[str, Any]:
    """Generate the configuration JSON Schema.

    The document is ``Config.model_json_schema()`` plus the dialect declaration editors need in
    order to pick a validator. The model's own output is what the conformance snapshot pins, so the
    two stay in step by construction rather than by a build step someone has to remember to run.
    """
    from molt.config.models import Config

    schema = Config.model_json_schema()
    return {"$schema": SCHEMA_DIALECT, **schema}
