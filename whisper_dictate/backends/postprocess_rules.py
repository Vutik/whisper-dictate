"""Rule-based clean-up of a raw transcript.

Two jobs. First, drop the phrases Whisper invents over silence. Second, put
back the Latin spelling of technical terms: dictating Russian with English
words mixed in, Whisper transliterates them ("мохоз" for "macOS"), so a
replacement table restores them.

The vocabulary also feeds the decoder as an initial prompt, which prevents
most of these before they happen; the table is the safety net.
"""
from __future__ import annotations

import re
import unicodedata

from ..interfaces import TextProcessor, Transcript
from ..log import log
from ..registry import register


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^\w\s]", "", text).strip()


@register("postprocess", "rules", priority=100)
class RuleProcessor(TextProcessor):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._compiled: list[tuple[re.Pattern, str]] = []
        self._build()

    def _build(self):
        table = self.cfg.get("replacements") or {}
        # Longest first, so "pull request" wins over "request".
        for src in sorted(table, key=len, reverse=True):
            dst = table[src]
            escaped = r"\s+".join(re.escape(w) for w in src.split())
            try:
                self._compiled.append(
                    (re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE | re.UNICODE), dst))
            except re.error as exc:
                log(f"postprocess: bad replacement {src!r}: {exc}")
        if self._compiled:
            log(f"postprocess: {len(self._compiled)} replacement rules")

    def process(self, transcript: Transcript) -> str:
        text = transcript.text.strip()
        if not text:
            return ""

        blacklist = {normalise(p) for p in self.cfg.get("hallucination_phrases", [])}
        if normalise(text) in blacklist:
            log(f"dropped hallucination: {text!r}")
            return ""

        for pattern, replacement in self._compiled:
            text = pattern.sub(replacement, text)

        return re.sub(r"\s{2,}", " ", text).strip()


@register("postprocess", "none", priority=-100)
class PassthroughProcessor(TextProcessor):
    def process(self, transcript: Transcript) -> str:
        return transcript.text.strip()
