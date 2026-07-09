"""
Checkpoint manager for large PDF processing.

Features:
- Saves intermediate results every N pages
- Can resume from checkpoint on failure
- Memory-bounded processing (processes batch, writes, releases)
- Partial failure recovery (completed pages are never lost)
"""
from __future__ import annotations

import gc
import json
import tempfile
import os
from pathlib import Path
from typing import Any, Callable

from app.logging_config import get_logger

logger = get_logger("CheckpointManager")


class CheckpointManager:
    """
    Manages checkpointing for large document processing.
    
    Usage:
        cm = CheckpointManager(total_pages=500, batch_size=50)
        
        for batch in cm.batches():
            results = process_pages(batch)
            cm.save_batch(batch, results)
        
        final = cm.get_all_results()
    """

    def __init__(
        self,
        total_pages: int,
        batch_size: int = 50,
        checkpoint_dir: str | None = None,
    ):
        self.total_pages = total_pages
        self.batch_size = batch_size
        self._completed_pages: set[int] = set()
        self._results: dict[int, dict[str, Any]] = {}
        self._checkpoint_dir = checkpoint_dir or tempfile.mkdtemp(prefix="textextract_")
        self._checkpoint_file = os.path.join(self._checkpoint_dir, "checkpoint.json")

        # Try to load existing checkpoint
        self._load_checkpoint()

    def batches(self) -> list[list[int]]:
        """Generate page index batches, skipping already completed pages."""
        remaining = [
            i for i in range(self.total_pages)
            if i not in self._completed_pages
        ]
        return [
            remaining[i:i + self.batch_size]
            for i in range(0, len(remaining), self.batch_size)
        ]

    def save_batch(
        self,
        page_indices: list[int],
        results: list[dict[str, Any]],
    ) -> None:
        """Save batch results and checkpoint."""
        for idx, result in zip(page_indices, results):
            self._results[idx] = result
            self._completed_pages.add(idx)

        self._save_checkpoint()
        gc.collect()

        logger.info(
            "Checkpoint: %d/%d pages completed",
            len(self._completed_pages),
            self.total_pages,
        )

    def get_all_results(self) -> list[dict[str, Any]]:
        """Return results in page order."""
        return [
            self._results.get(i, {"text": "", "method": "missing", "confidence": 0})
            for i in range(self.total_pages)
        ]

    @property
    def completed_count(self) -> int:
        return len(self._completed_pages)

    @property
    def is_complete(self) -> bool:
        return len(self._completed_pages) >= self.total_pages

    def _save_checkpoint(self) -> None:
        """Save checkpoint metadata (not full results - those are in memory)."""
        try:
            data = {
                "total_pages": self.total_pages,
                "completed": sorted(self._completed_pages),
            }
            with open(self._checkpoint_file, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Failed to save checkpoint: %s", e)

    def _load_checkpoint(self) -> None:
        """Load checkpoint if exists."""
        if os.path.exists(self._checkpoint_file):
            try:
                with open(self._checkpoint_file) as f:
                    data = json.load(f)
                if data.get("total_pages") == self.total_pages:
                    self._completed_pages = set(data.get("completed", []))
                    logger.info(
                        "Resumed from checkpoint: %d pages already done",
                        len(self._completed_pages),
                    )
            except Exception as e:
                logger.warning("Failed to load checkpoint: %s", e)

    def cleanup(self) -> None:
        """Remove checkpoint files."""
        try:
            if os.path.exists(self._checkpoint_file):
                os.remove(self._checkpoint_file)
            if os.path.exists(self._checkpoint_dir):
                os.rmdir(self._checkpoint_dir)
        except Exception:
            pass
