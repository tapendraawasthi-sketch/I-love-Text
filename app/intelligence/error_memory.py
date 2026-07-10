"""
Error Memory — remembers OCR corrections to improve over time.

Stores:
- Font-specific character mappings that have been corrected
- Common OCR confusion patterns (र↔व, म↔भ, etc.)
- Domain-specific corrections
- Document-specific patterns

This creates a feedback loop where every corrected document
makes future extractions better.

Addresses Problems 19, 20 from the architectural critique.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.logging_config import get_logger

logger = get_logger("ErrorMemory")

# Default storage path
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "error_memory.json",
)


@dataclass
class CorrectionRecord:
    """A single recorded correction."""
    original: str
    corrected: str
    font_name: str = ""
    domain: str = ""
    confidence: float = 0.0
    count: int = 1
    last_seen: float = 0.0


class ErrorMemoryDB:
    """
    Persistent error memory database.

    Stores correction patterns and learns from them over time.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._corrections: dict[str, CorrectionRecord] = {}
        self._char_confusions: Counter = Counter()
        self._font_mappings: dict[str, dict[str, str]] = defaultdict(dict)
        self._domain_vocabulary: dict[str, Counter] = defaultdict(Counter)
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()
            self._loaded = True

    def record_correction(
        self,
        original: str,
        corrected: str,
        font_name: str = "",
        domain: str = "",
        confidence: float = 80.0,
    ) -> None:
        """Record a correction for future reference."""
        self._ensure_loaded()

        key = f"{original}|{font_name}|{domain}"
        if key in self._corrections:
            self._corrections[key].count += 1
            self._corrections[key].last_seen = time.time()
            if confidence > self._corrections[key].confidence:
                self._corrections[key].confidence = confidence
        else:
            self._corrections[key] = CorrectionRecord(
                original=original,
                corrected=corrected,
                font_name=font_name,
                domain=domain,
                confidence=confidence,
                last_seen=time.time(),
            )

        # Record character-level confusions
        if len(original) == len(corrected):
            for o, c in zip(original, corrected):
                if o != c:
                    self._char_confusions[(o, c)] += 1

        # Record font-specific mapping
        if font_name:
            self._font_mappings[font_name][original] = corrected

        # Record domain vocabulary
        if domain:
            self._domain_vocabulary[domain][corrected] += 1

    def suggest_correction(
        self,
        text: str,
        font_name: str = "",
        domain: str = "",
    ) -> tuple[str, float] | None:
        """
        Look up a known correction for this text.

        Returns (corrected_text, confidence) or None.
        """
        self._ensure_loaded()

        # Try exact match with font and domain
        key = f"{text}|{font_name}|{domain}"
        if key in self._corrections:
            rec = self._corrections[key]
            if rec.count >= 2 and rec.confidence >= 60:
                return rec.corrected, min(95, rec.confidence + rec.count * 2)

        # Try with font only
        key_font = f"{text}|{font_name}|"
        if key_font in self._corrections:
            rec = self._corrections[key_font]
            if rec.count >= 3:
                return rec.corrected, min(90, rec.confidence)

        # Try font-specific mapping
        if font_name in self._font_mappings:
            if text in self._font_mappings[font_name]:
                return self._font_mappings[font_name][text], 80.0

        # Try without context
        key_bare = f"{text}||"
        if key_bare in self._corrections:
            rec = self._corrections[key_bare]
            if rec.count >= 5:
                return rec.corrected, min(85, rec.confidence)

        return None

    def get_common_confusions(self, top_n: int = 20) -> list[tuple[str, str, int]]:
        """Get the most common character confusions."""
        self._ensure_loaded()
        return [
            (pair[0], pair[1], count)
            for pair, count in self._char_confusions.most_common(top_n)
        ]

    def get_domain_vocabulary(self, domain: str) -> set[str]:
        """Get learned vocabulary for a domain."""
        self._ensure_loaded()
        return set(self._domain_vocabulary.get(domain, {}).keys())

    def _load(self) -> None:
        """Load from disk."""
        if not os.path.exists(self.db_path):
            return
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, rec in data.get("corrections", {}).items():
                self._corrections[key] = CorrectionRecord(**rec)

            for pair_str, count in data.get("char_confusions", {}).items():
                parts = pair_str.split("|")
                if len(parts) == 2:
                    self._char_confusions[(parts[0], parts[1])] = count

            self._font_mappings = defaultdict(
                dict, data.get("font_mappings", {})
            )

            for domain, vocab in data.get("domain_vocabulary", {}).items():
                self._domain_vocabulary[domain] = Counter(vocab)

            logger.info(
                "Error memory loaded: %d corrections, %d confusions",
                len(self._corrections),
                len(self._char_confusions),
            )
        except Exception as e:
            logger.warning("Failed to load error memory: %s", e)

    def save(self) -> None:
        """Save to disk."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

            data = {
                "corrections": {
                    k: {
                        "original": v.original,
                        "corrected": v.corrected,
                        "font_name": v.font_name,
                        "domain": v.domain,
                        "confidence": v.confidence,
                        "count": v.count,
                        "last_seen": v.last_seen,
                    }
                    for k, v in self._corrections.items()
                },
                "char_confusions": {
                    f"{k[0]}|{k[1]}": v
                    for k, v in self._char_confusions.items()
                },
                "font_mappings": dict(self._font_mappings),
                "domain_vocabulary": {
                    domain: dict(counter)
                    for domain, counter in self._domain_vocabulary.items()
                },
                "saved_at": time.time(),
            }

            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("Error memory saved: %d corrections", len(self._corrections))
        except Exception as e:
            logger.warning("Failed to save error memory: %s", e)

    @property
    def stats(self) -> dict[str, int]:
        """Get database statistics."""
        self._ensure_loaded()
        return {
            "total_corrections": len(self._corrections),
            "char_confusions": len(self._char_confusions),
            "font_mappings": sum(
                len(m) for m in self._font_mappings.values()
            ),
            "domain_vocabularies": len(self._domain_vocabulary),
        }


# Global singleton
_global_db: ErrorMemoryDB | None = None


def get_error_memory() -> ErrorMemoryDB:
    """Get the global error memory database."""
    global _global_db
    if _global_db is None:
        _global_db = ErrorMemoryDB()
    return _global_db
