"""文字匹配数据结构；实际 OCR 由 MaaFramework 驱动。"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Rule


@dataclass(frozen=True)
class OCRText:
    text: str
    score: float
    rect: tuple[int, int, int, int]


def text_match(texts: list[OCRText], rule: Rule):
    """Return the best matching OCR box for a rule, or None."""
    wanted = rule.text.strip()
    candidates = []
    for item in texts:
        left, right = item.text.strip(), wanted
        matched = left == right if rule.text_match == 'exact' else right in left
        if matched and item.score >= rule.text_confidence:
            candidates.append(item)
    return max(candidates, key=lambda item: item.score, default=None)
