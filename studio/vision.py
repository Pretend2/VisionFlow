from __future__ import annotations

from dataclasses import dataclass
import math
import time
import cv2
import numpy as np
from .maa_vision import MaaVision
from .models import Rule, decode_image
from .ocr import OCRText, text_match

# A rule counts as "gone" only after it stays unmatched for this long, so a single
# noisy frame cannot re-arm a click. The GUI raises it to two capture cycles.
ARM_WINDOW = 2.0


@dataclass
class Match:
    rule_id: str
    hit: bool = False
    score: float = 0.0
    rect: tuple[int, int, int, int] | None = None
    text: str = ''
    reason: str = ''


def match_rule(frame: np.ndarray, rule: Rule, ocr=None, frame_key=None) -> Match:
    if not rule.enabled:
        return Match(rule.id, reason='已停用')
    if rule.mode == 'text':
        if ocr is None:
            ocr = MaaVision()
        try:
            if hasattr(ocr, 'text_match'):
                found = ocr.text_match(frame, rule)
            else:
                found = text_match(ocr.recognize(frame, rule.text_region, frame_key=frame_key), rule)
        except RuntimeError as exc:
            return Match(rule.id, reason=str(exc))
        if found is None:
            return Match(rule.id, False, 0.0, None, '', '未找到目标文字')
        return Match(rule.id, True, found.score, found.rect, found.text, '找到目标文字')
    if not rule.template:
        return Match(rule.id, reason='尚未设置模板')
    recognizer = ocr or MaaVision()
    if hasattr(recognizer, 'match_template'):
        try:
            hit, score, rect, reason = recognizer.match_template(frame, rule)
        except RuntimeError as exc:
            return Match(rule.id, reason=str(exc))
        return Match(rule.id, hit, score, rect, '', reason)
    template = decode_image(rule.template)
    fh, fw = frame.shape[:2]
    x, y, w, h = rule.region or (0, 0, fw, fh)
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > fw or y + h > fh:
        return Match(rule.id, reason='搜索区域超出当前画面')
    th, tw = template.shape[:2]
    if tw > w or th > h:
        return Match(rule.id, reason='模板大于搜索区域')
    # Squared error handles solid-color templates, unlike normalized correlation.
    search = frame[y:y+h, x:x+w].astype(np.float32) / 255.0
    target = template.astype(np.float32) / 255.0
    result = cv2.matchTemplate(search, target, cv2.TM_SQDIFF)
    minimum, _, location, _ = cv2.minMaxLoc(result)
    score = float(np.clip(1.0 - np.sqrt(max(0.0, minimum) / (tw * th * 3)), 0.0, 1.0))
    rect = (x + location[0], y + location[1], tw, th)
    return Match(rule.id, score >= rule.threshold, score, rect, '',
                 '达到阈值' if score >= rule.threshold else '低于阈值')


def match_rules(frame: np.ndarray, rules: list[Rule], resolution: list[int] | None, ocr=None, frame_key=None) -> list[Match]:
    if resolution and resolution != [frame.shape[1], frame.shape[0]]:
        raise ValueError(f'当前画面 {frame.shape[1]} × {frame.shape[0]} 与基准 {resolution[0]} × {resolution[1]} 不一致；请统一模拟器分辨率或新建方案')
    return [match_rule(frame, rule, ocr=ocr, frame_key=frame_key) for rule in rules]


class TriggerGate:
    def __init__(self):
        self.last: dict[str, float] = {}
        self.latched: set[str] = set()
        self.fired: set[str] = set()
        self.seen: dict[str, float] = {}
        self.window = ARM_WINDOW

    def update(self, matches: list[Match], now: float | None = None) -> list[str]:
        """Refresh hit timestamps and re-arm rules that left the screen.

        Returns the ids that became ready again, so the caller can log them.
        """
        now = time.monotonic() if now is None else now
        rearmed = []
        for match in matches:
            if match.hit:
                self.seen[match.rule_id] = now
            elif match.rule_id in self.latched and now - self.seen.get(match.rule_id, -math.inf) >= self.window:
                self.latched.discard(match.rule_id)
                self.fired.discard(match.rule_id)
                rearmed.append(match.rule_id)
        return rearmed

    def ready(self, rule: Rule, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if rule.trigger == 'once':
            return rule.id not in self.fired
        if rule.trigger == 'reappear':
            return rule.id not in self.latched
        return now - self.last.get(rule.id, -float('inf')) >= rule.interval

    def mark(self, rule: Rule):
        self.last[rule.id] = time.monotonic()
        self.latched.add(rule.id)
        self.fired.add(rule.id)


def gate_reason(rule: Rule, gate: TriggerGate, now: float | None = None) -> str:
    """Explain why a matched rule is not executed this cycle."""
    now = time.monotonic() if now is None else now
    if rule.trigger == 'once':
        return '本轮已触发过，目标消失后会自动重新允许（或点「重置触发」）'
    if rule.trigger == 'reappear':
        return '需先让目标从画面消失'
    remaining = rule.interval - (now - gate.last.get(rule.id, -math.inf))
    return f'未到触发间隔，还需 {max(0.0, remaining):.1f} 秒'


def choose_rule(rules: list[Rule], matches: list[Match], gate: TriggerGate, allowed: list[str] | None = None) -> Rule | None:
    """First executable rule in priority order; ``allowed`` restricts which rules the conditions opened."""
    hits = {match.rule_id for match in matches if match.hit}
    order = allowed if allowed is not None else [rule.id for rule in rules]
    by_id = {rule.id: rule for rule in rules}
    for rule_id in order:
        rule = by_id.get(rule_id)
        if rule and rule.enabled and rule.id in hits and rule.action != 'observe' and gate.ready(rule):
            return rule
    return None
