"""Prompt-injection screening on the raw document text.

A document is data, never instructions. The extraction prompt says so, but we also
check the text directly, because a model can't be relied on to report an attack on itself.
"""
from __future__ import annotations

import re

from .models import Flag

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any )?(the )?(previous|prior|above|earlier)",
    r"\byou are now\b",
    r"system prompt",
    r"(note|message|instruction)s? (to|for) (the )?(ai|assistant|model|llm|bot)",
    r"(approve|mark) (this|the) invoice (as )?(approved|paid|for (immediate )?payment)",
    r"do not (flag|report|review)",
    r"skip (the )?(review|approval|verification)",
]
_REGEX = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def detect_injection(text: str | None) -> list[Flag]:
    if not text:
        return []
    m = _REGEX.search(text)
    if not m:
        return []
    snippet = text[max(0, m.start() - 20): m.end() + 20].replace("\n", " ").strip()
    return [Flag(code="possible_prompt_injection", severity="critical",
                 message=f'The document contains text that looks like instructions to an AI: "...{snippet}..."')]
