"""
Character Candidate Engine — maintains multiple hypotheses per character.

Instead of:
    OCR → single character → repair

We do:
    OCR → N candidates per character → scoring → selection

This addresses Problems 6, 7, 13 from the architectural critique.

Each character position maintains a ranked list of candidates with
confidence scores. The best candidate is selected using:
1. OCR engine confidence
2. Knowledge base word matching
3. Character bigram/trigram probability
4. Cross-engine agreement
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("CharacterCandidates")


@dataclass
class CharCandidate:
    """A single candidate for a character position."""
    char: str
    confidence: float  # 0-100
    source: str  # Which engine/method produced this
    is_devanagari: bool = False


@dataclass
class CharPosition:
    """All candidates for a single character position."""
    position: int
    candidates: list[CharCandidate] = field(default_factory=list)
    selected: str = ""
    selection_confidence: float = 0.0
    selection_reason: str = ""

    @property
    def best_candidate(self) -> CharCandidate | None:
        if not self.candidates:
            return None
        return max(self.candidates, key=lambda c: c.confidence)

    def add_candidate(self, char: str, confidence: float, source: str) -> None:
        is_deva = "\u0900" <= char <= "\u097F" if char else False
        self.candidates.append(CharCandidate(
            char=char,
            confidence=confidence,
            source=source,
            is_devanagari=is_deva,
        ))

    def select_best(self) -> str:
        """Select the best candidate using all available evidence."""
        if not self.candidates:
            self.selected = ""
            return ""

        # If all candidates agree, high confidence
        chars = [c.char for c in self.candidates if c.char]
        if len(set(chars)) == 1:
            self.selected = chars[0]
            self.selection_confidence = min(
                100, max(c.confidence for c in self.candidates) + 10
            )
            self.selection_reason = "unanimous"
            return self.selected

        # Majority voting
        from collections import Counter
        votes = Counter(chars)
        if votes:
            winner, count = votes.most_common(1)[0]
            total = len(chars)
            agreement = count / total

            # Weighted by confidence
            weighted_scores: dict[str, float] = {}
            for c in self.candidates:
                if c.char:
                    weighted_scores[c.char] = (
                        weighted_scores.get(c.char, 0) + c.confidence
                    )

            weighted_winner = max(weighted_scores, key=weighted_scores.get)

            if weighted_winner == winner:
                self.selected = winner
                self.selection_confidence = agreement * 100
                self.selection_reason = f"majority_vote ({count}/{total})"
            else:
                # Weighted score disagrees with simple majority
                # Trust weighted score (accounts for engine quality)
                self.selected = weighted_winner
                self.selection_confidence = (
                    weighted_scores[weighted_winner] /
                    max(sum(weighted_scores.values()), 1) * 100
                )
                self.selection_reason = "weighted_vote"
        else:
            best = self.best_candidate
            self.selected = best.char if best else ""
            self.selection_confidence = best.confidence if best else 0
            self.selection_reason = "best_single"

        return self.selected


@dataclass
class WordCandidates:
    """Character-level candidates for a complete word."""
    char_positions: list[CharPosition] = field(default_factory=list)
    word_confidence: float = 0.0
    is_known_word: bool = False
    corrected_word: str = ""
    correction_source: str = ""

    @property
    def assembled_word(self) -> str:
        """Assemble word from best candidates at each position."""
        return "".join(cp.selected or "" for cp in self.char_positions)

    def apply_word_correction(self, domain: str | None = None) -> str:
        """
        After character-level selection, apply word-level correction.

        This is where the knowledge base improves OCR output.
        """
        from app.intelligence.nepal_knowledge_base import (
            is_known_word, correct_word,
        )

        word = self.assembled_word
        if not word:
            return ""

        # Check if already a known word
        if is_known_word(word):
            self.is_known_word = True
            self.corrected_word = word
            self.word_confidence = 95.0
            self.correction_source = "known_word"
            return word

        # Try correction
        corrected, conf, source = correct_word(word, domain)
        if corrected != word and conf >= 65:
            self.corrected_word = corrected
            self.word_confidence = conf
            self.correction_source = source
            return corrected

        self.corrected_word = word
        self.word_confidence = self.char_positions[0].selection_confidence if self.char_positions else 50
        self.correction_source = "uncorrected"
        return word


def merge_engine_outputs(
    outputs: list[tuple[str, str, float]],
) -> list[CharPosition]:
    """
    Merge outputs from multiple OCR engines at character level.

    Args:
        outputs: List of (text, engine_name, confidence) tuples.

    Returns:
        List of CharPosition objects with candidates from all engines.
    """
    if not outputs:
        return []

    # Find the longest output to use as reference
    max_len = max(len(text) for text, _, _ in outputs)
    if max_len == 0:
        return []

    positions = [CharPosition(position=i) for i in range(max_len)]

    for text, engine, base_conf in outputs:
        for i, char in enumerate(text):
            if i < len(positions):
                positions[i].add_candidate(char, base_conf, engine)

    # Select best at each position
    for pos in positions:
        pos.select_best()

    return positions


def build_word_candidates(
    char_positions: list[CharPosition],
) -> list[WordCandidates]:
    """
    Group character positions into words and apply word-level correction.
    """
    words: list[WordCandidates] = []
    current_word_positions: list[CharPosition] = []

    for pos in char_positions:
        char = pos.selected
        if not char or char.isspace():
            if current_word_positions:
                wc = WordCandidates(char_positions=current_word_positions)
                words.append(wc)
                current_word_positions = []
        else:
            current_word_positions.append(pos)

    if current_word_positions:
        wc = WordCandidates(char_positions=current_word_positions)
        words.append(wc)

    return words
