"""
Intelligence module — Document Intelligence System.

Replaces the linear OCR pipeline with a multi-phase
intelligence-driven architecture:

1. Document Intelligence (what is this document?)
2. Page Intelligence (what's on each page?)
3. Region Intelligence (what are the regions?)
4. Extraction (per-region optimal strategy)
5. Character Candidates (multiple hypotheses)
6. Knowledge Base Correction (domain-aware)
7. Cross-Page Validation
8. Semantic Validation
9. Error Memory (learning loop)
"""
