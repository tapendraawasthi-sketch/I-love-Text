"""
Probabilistic Document Model — every element carries candidates and
confidence scores that are propagated until final serialization.

This replaces deterministic "extract → done" with:
    extract → N candidates → score → propagate → serialize best path

Key concepts:
    - CandidateText: a text variant with confidence score and source
    - ReadingOrderEdge: a weighted edge between two elements
    - ProbabilisticElement: an element with multiple candidate interpretations
    - DocumentGraph: the full document as a directed weighted graph

Nothing is committed until serialization. The graph carries ALL possibilities.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ElementRelation(Enum):
    """Types of relationships between document elements."""
    READING_NEXT = "reading_next"       # A is read before B
    PARENT_CHILD = "parent_child"       # A contains B
    CAPTION_OF = "caption_of"           # A is caption of B
    FOOTNOTE_OF = "footnote_of"         # A is footnote for B
    CELL_OF = "cell_of"                 # A is cell in table B
    HEADER_OF = "header_of"             # A is header of section B
    CONTINUATION = "continuation"       # A continues from previous page into B
    ALTERNATIVE = "alternative"         # A and B are alternative readings


@dataclass
class CandidateText:
    """A single text candidate with provenance and confidence."""
    text: str
    confidence: float               # 0-100
    source: str                     # Engine/method that produced this
    unicode_valid: bool = True      # Passed Unicode validation?
    visual_verified: bool = False   # Passed visual verification?
    language_score: float = 0.0     # Language model score (0-100)

    # Repair history
    repairs: list[str] = field(default_factory=list)

    @property
    def composite_score(self) -> float:
        """Weighted composite score for ranking candidates."""
        base = self.confidence * 0.4
        if self.unicode_valid:
            base += 20.0
        if self.visual_verified:
            base += 15.0
        base += self.language_score * 0.25
        return min(100.0, base)


@dataclass
class ReadingOrderEdge:
    """A weighted directed edge in the reading order graph."""
    source_id: str
    target_id: str
    weight: float                   # Higher = more likely this order is correct
    relation: ElementRelation = ElementRelation.READING_NEXT
    evidence: list[str] = field(default_factory=list)  # Why we believe this edge


@dataclass
class ProbabilisticElement:
    """
    A document element with multiple candidate interpretations.

    Instead of one text string, this carries N candidates ranked by
    composite score. The best candidate is selected at serialization time.
    """
    element_id: str
    page_number: int = 0

    # Position
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0

    # Multiple text candidates
    candidates: list[CandidateText] = field(default_factory=list)

    # Element type candidates (might be heading OR paragraph)
    type_candidates: list[tuple[str, float]] = field(default_factory=list)

    # Semantic classification
    semantic_type: str = "body"     # Best guess
    semantic_confidence: float = 0.0

    # Parent/child relationships
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)

    # Font properties (for classification)
    font_size: float = 0.0
    is_bold: bool = False

    @property
    def best_text(self) -> str:
        """Return the highest-scoring text candidate."""
        if not self.candidates:
            return ""
        return max(self.candidates, key=lambda c: c.composite_score).text

    @property
    def best_confidence(self) -> float:
        """Return confidence of the best candidate."""
        if not self.candidates:
            return 0.0
        return max(c.composite_score for c in self.candidates)

    @property
    def best_type(self) -> str:
        """Return the most likely element type."""
        if not self.type_candidates:
            return self.semantic_type
        return max(self.type_candidates, key=lambda t: t[1])[0]

    def add_candidate(self, text: str, confidence: float, source: str,
                      **kwargs) -> None:
        """Add a text candidate from a specific extraction source."""
        candidate = CandidateText(
            text=text,
            confidence=confidence,
            source=source,
            **kwargs,
        )
        self.candidates.append(candidate)

    def add_type_candidate(self, element_type: str, confidence: float) -> None:
        """Add a type classification candidate."""
        self.type_candidates.append((element_type, confidence))


class DocumentGraph:
    """
    The full document represented as a probabilistic directed graph.

    Nodes = ProbabilisticElements
    Edges = ReadingOrderEdges (with weights)

    Multiple reading orders coexist until serialization picks the best path.
    """

    def __init__(self):
        self.elements: dict[str, ProbabilisticElement] = {}
        self.edges: list[ReadingOrderEdge] = []
        self.page_count: int = 0
        self.metadata: dict[str, Any] = {}

    def add_element(self, element: ProbabilisticElement) -> None:
        """Add an element to the graph."""
        self.elements[element.element_id] = element

    def add_edge(self, source_id: str, target_id: str, weight: float,
                 relation: ElementRelation = ElementRelation.READING_NEXT,
                 evidence: list[str] | None = None) -> None:
        """Add a directed weighted edge between two elements."""
        edge = ReadingOrderEdge(
            source_id=source_id,
            target_id=target_id,
            weight=weight,
            relation=relation,
            evidence=evidence or [],
        )
        self.edges.append(edge)

    def get_reading_order(self) -> list[str]:
        """
        Compute the best reading order as a topological sort of the graph,
        weighted by edge confidence.

        Uses a modified Dijkstra / priority-based topological sort.
        """
        if not self.elements:
            return []

        # Build adjacency list
        adjacency: dict[str, list[tuple[str, float]]] = {
            eid: [] for eid in self.elements
        }
        in_degree: dict[str, int] = {eid: 0 for eid in self.elements}

        for edge in self.edges:
            if edge.relation == ElementRelation.READING_NEXT:
                if edge.source_id in adjacency and edge.target_id in in_degree:
                    adjacency[edge.source_id].append((edge.target_id, edge.weight))
                    in_degree[edge.target_id] += 1

        # Priority queue: (negative_weight, element_id) — higher weight first
        # Start with all elements that have no incoming reading edges
        queue: list[tuple[float, str]] = []
        for eid, deg in in_degree.items():
            if deg == 0:
                elem = self.elements[eid]
                # Priority: page number first, then y position, then confidence
                priority = -(elem.page_number * 1_000_000 +
                             (1_000_000 - elem.y0 * 1000) +
                             elem.best_confidence)
                heapq.heappush(queue, (priority, eid))

        result: list[str] = []
        visited: set[str] = set()

        while queue:
            _, eid = heapq.heappop(queue)
            if eid in visited:
                continue
            visited.add(eid)
            result.append(eid)

            for neighbor, weight in adjacency.get(eid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] <= 0 and neighbor not in visited:
                    elem = self.elements[neighbor]
                    priority = -(elem.page_number * 1_000_000 +
                                 (1_000_000 - elem.y0 * 1000) +
                                 weight)
                    heapq.heappush(queue, (priority, neighbor))

        # Add any unvisited elements (disconnected components)
        for eid in self.elements:
            if eid not in visited:
                result.append(eid)

        return result

    def get_children(self, element_id: str) -> list[str]:
        """Get child elements in parent-child hierarchy."""
        return [
            edge.target_id for edge in self.edges
            if edge.source_id == element_id and
               edge.relation == ElementRelation.PARENT_CHILD
        ]

    def get_caption_for(self, element_id: str) -> str | None:
        """Find caption element for a figure/table."""
        for edge in self.edges:
            if edge.target_id == element_id and edge.relation == ElementRelation.CAPTION_OF:
                return edge.source_id
        return None

    def serialize_best_path(
        self,
        *,
        include_page_separators: bool = True,
        include_headers: bool = True,
    ) -> str:
        """
        Serialize the document graph to text by following the best reading order
        and selecting the best candidate for each element.
        """
        order = self.get_reading_order()
        parts: list[str] = []
        current_page = 0

        for eid in order:
            elem = self.elements.get(eid)
            if not elem:
                continue

            # Page separator
            if include_page_separators and elem.page_number > current_page:
                current_page = elem.page_number
                if self.page_count > 1:
                    parts.append(
                        f"\n{'=' * 60}\n"
                        f"  PAGE {current_page} / {self.page_count}\n"
                        f"{'=' * 60}\n"
                    )

            text = elem.best_text.strip()
            if not text:
                continue

            best_type = elem.best_type

            # Format based on element type
            if best_type == "heading_1":
                parts.append(f"\n# {text}\n")
            elif best_type == "heading_2":
                parts.append(f"\n## {text}\n")
            elif best_type == "heading_3":
                parts.append(f"\n### {text}\n")
            elif best_type == "table":
                parts.append(f"\n{text}\n")
            elif best_type == "caption":
                parts.append(f"[Caption] {text}")
            elif best_type == "footnote":
                parts.append(f"[{text}]")
            elif best_type in ("header", "footer"):
                if include_headers:
                    parts.append(f"[{best_type.upper()}] {text}")
            else:
                parts.append(text)

        return "\n\n".join(parts)

    def confidence_report(self) -> dict[str, Any]:
        """Generate a confidence report for the entire document."""
        if not self.elements:
            return {"overall": 0, "elements": 0}

        confidences = [e.best_confidence for e in self.elements.values()]
        low_confidence = [
            e.element_id for e in self.elements.values()
            if e.best_confidence < 60
        ]

        return {
            "overall": round(sum(confidences) / len(confidences), 1),
            "min": round(min(confidences), 1),
            "max": round(max(confidences), 1),
            "elements": len(self.elements),
            "edges": len(self.edges),
            "low_confidence_count": len(low_confidence),
            "low_confidence_ids": low_confidence[:20],
        }
