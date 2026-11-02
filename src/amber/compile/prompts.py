"""Fixed prompts for the synthesizer (``docs/04-compiler.md`` §D2) and runtime judgment
(``docs/05-runtime.md`` §2.3). Kept in one module so they can be reviewed and diffed."""

from __future__ import annotations

SYNTHESIZER_SYSTEM = (
    "You are the hole-filler of a compiler. You receive a typed hole and recordings of the "
    "values it must produce. Write the smallest correct Python (for transforms) or the "
    "shortest prompt and schema (for judgments) that reproduces the recordings and "
    "generalizes to similar inputs. Do not invent data. Do not call tools or network. Use "
    "only: math, datetime, statistics, re, json, itertools, collections. Explain your "
    "reasoning in `rationale` in one paragraph."
)

TRANSFORM_RULES = [
    "Define exactly the function named in `signature`, with exactly those parameters.",
    "Return a dict with exactly the keys listed in `produces`.",
    "Use only the parameters that are needed; ignore the rest.",
    "Imports allowed: math, datetime, statistics, re, json, itertools, collections.",
    "No I/O, no randomness, no clocks: the result must depend only on the parameters.",
    "Prefer simple, readable code; handle the recorded cases exactly.",
]

JUDGMENT_RULES = [
    "The prompt is at most 1200 characters and addresses a small model.",
    "Refer to the available context only by the keys listed in `context_keys`.",
    "Do not mention tool names, trace ids, or the recordings themselves.",
    "Fields: one per expected output key, plus `confidence` (float 0..1).",
    "Use `enum` for fields whose recorded values form a small fixed set.",
    "Ask for the same style and length as the recorded outputs.",
]

GATE_RULES = [
    "The prompt asks whether the conditional step should run and why.",
    "Fields are exactly `go` (bool) and `reason` (str).",
    "Refer to the available context only by the keys listed in `context_keys`.",
]

JUDGMENT_SYSTEM = (
    "You are one step of a compiled workflow. Answer only with the requested fields. If the "
    "context is insufficient, set confidence below 0.5 and explain in `notes` if present."
)
