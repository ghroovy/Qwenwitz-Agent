# Owner: ACTIVE
"""Parse politics from a user prompt into HOI4 party popularity values."""

from __future__ import annotations

import re

IDEOLOGY_WORDS = {
    "democratic": "democratic", "democracy": "democratic", "democrat": "democratic",
    "fascist": "fascism", "fascism": "fascism", "authoritarian": "fascism",
    "communist": "communism", "communism": "communism", "soviet": "communism",
    "neutral": "neutrality", "neutrality": "neutrality", "non-aligned": "neutrality",
    "non-aligned": "neutrality", "monarchist": "neutrality", "monarchy": "neutrality",
}


def parse_politics(text: str) -> dict:
    """Return {"parties": {ideology: float}, "ruling_party": str, "source": text}.

    Accepts "democracy", "fascism", "communism", or "20% fascist, 80% democratic".
    Defaults to 100% democratic when nothing parses.
    """
    low = (text or "").lower()
    percentages = re.findall(r"(\d+(?:\.\d+)?)\s*%\s*([a-z-]+)", low)
    parties: dict[str, float] = {}
    if percentages:
        for value, word in percentages:
            ideology = IDEOLOGY_WORDS.get(word)
            if ideology:
                parties[ideology] = parties.get(ideology, 0.0) + float(value) / 100.0
    else:
        for word, ideology in IDEOLOGY_WORDS.items():
            if word in low:
                parties[ideology] = 1.0
                break
    if not parties:
        parties = {"democratic": 1.0}
    # normalize to 1.0
    total = sum(parties.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        parties = {k: round(v / total, 4) for k, v in parties.items()}
    ruling = max(parties, key=parties.get)
    return {"parties": parties, "ruling_party": ruling, "source": text or ""}

