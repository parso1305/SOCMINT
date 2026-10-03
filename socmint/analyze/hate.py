"""Hate / communal content: INTERFACE ONLY in v0.

Disabled by default (`hate.enabled: false` in thresholds.yaml). Analyst-only: results must never be
shown to citizens or used in tipline replies. No model ships with v0.
"""

from __future__ import annotations

from typing import Protocol

from socmint.core.settings import threshold


class HateClassifier(Protocol):
    """Return a score in [0,1] plus labels for a text, or None when not enabled."""

    def classify(self, text: str) -> dict | None: ...


class DisabledHateClassifier:
    def classify(self, text: str) -> dict | None:
        return None


def is_enabled() -> bool:
    return bool(threshold("hate.enabled", False))


def get_classifier() -> HateClassifier:
    """v0 always returns the disabled classifier, even if the flag is flipped (no model is bundled)."""
    return DisabledHateClassifier()


def run() -> dict:
    return {"enabled": False, "status": "not enabled (interface only in v0)"}
