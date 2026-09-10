"""Turn noisy OCR text into a known item.

Two modes. Open-set: fuzzy match against every known part name (~750 strings
including aliases). Constrained: when we know which relic was opened, match
against only its six possible rewards, which removes almost all OCR ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

from voidsight.data.catalog import Catalog, Part, normalize

#: Below this similarity we report no match rather than a wrong one.
OPEN_SET_THRESHOLD = 80.0
#: A six-way choice can be far more forgiving.
CONSTRAINED_THRESHOLD = 60.0

#: Whole-string similarity, deliberately not one of rapidfuzz's partial
#: scorers. Partial matching rates a fragment ("NIKANA PRIME") as good as the
#: full name, which makes a reading that split one reward down the middle look
#: better than the one that framed it correctly — see `pipeline.scan`, which
#: compares candidate readings by how well they match.
SCORER = fuzz.ratio


@dataclass(frozen=True)
class Match:
    raw_text: str
    part: Part | None
    score: float
    constrained: bool
    runner_up: tuple[str, float] | None = None

    @property
    def ok(self) -> bool:
        return self.part is not None

    @property
    def ambiguous(self) -> bool:
        """True when the second-best candidate is nearly as good as the best."""
        return bool(self.runner_up and self.score - self.runner_up[1] < 5.0)


def match(
    text: str,
    catalog: Catalog,
    *,
    candidates: list[str] | None = None,
) -> Match:
    """Resolve OCR text to a part.

    `candidates` restricts matching to those item names (a relic's drop table).
    """
    query = normalize(text)
    constrained = candidates is not None

    if constrained:
        keys = {normalize(name): name for name in candidates or []}
        for name in candidates or []:
            if part := catalog.lookup(name):
                keys[normalize(part.display_name)] = name
        threshold = CONSTRAINED_THRESHOLD
    else:
        keys = {key: key for key in catalog.match_keys}
        threshold = OPEN_SET_THRESHOLD

    if not query or not keys:
        return Match(raw_text=text, part=None, score=0.0, constrained=constrained)

    results = process.extract(query, list(keys), scorer=SCORER, limit=2)
    best_key, best_score = results[0][0], results[0][1]
    runner_up = (results[1][0], results[1][1]) if len(results) > 1 else None

    if best_score < threshold:
        return Match(
            raw_text=text,
            part=None,
            score=best_score,
            constrained=constrained,
            runner_up=runner_up,
        )

    return Match(
        raw_text=text,
        part=catalog.lookup(keys[best_key]),
        score=best_score,
        constrained=constrained,
        runner_up=runner_up,
    )
