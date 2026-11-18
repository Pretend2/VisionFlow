from __future__ import annotations

import time

from .models import Rule, TreeNode
from .vision import Match


def hit_map(matches: list[Match]) -> dict[str, bool]:
    return {match.rule_id: bool(match.hit) for match in matches}


def walk(nodes: list[TreeNode], hits: dict[str, bool]) -> list[str]:
    """Rule ids in execution order.

    条件行自身也是一步（命中时可以执行它的动作），之后只打开命中的那个分支。
    """
    result: list[str] = []

    def visit(items):
        for node in items:
            result.append(node.rule_id)
            if node.kind == 'if':
                visit(node.then if hits.get(node.rule_id, False) else node.otherwise)

    visit(nodes)
    return result


def flat_order(rules: list[Rule]) -> list[str]:
    return [rule.id for rule in rules]


def advance(order: list[str], hits: dict[str, bool], rules: list[Rule], gate, cursor: str | None, now: float | None = None, judges=()):
    """Walk the list one rule at a time and return the next step.

    Returns ``(rule, cursor, event)``:

    - ``run``: execute ``rule``; the cursor already points at the following row.
    - ``delay``: wait ``rule.delay`` seconds, then continue from the next row.
    - ``wait``: the cursor stays on that row because its icon is not on screen
      (``match`` mode) or its trigger is cooling down.
    - ``stop``: a ``终止`` row was reached, so the cursor goes back to row one.
    - ``empty``: nothing can run (no rules, or all disabled).

    ``judges`` holds the ids of condition rows: they are judged by ``walk`` already,
    so they always move on instead of waiting for their own icon.
    """
    by_id = {rule.id: rule for rule in rules}
    ids = [rule_id for rule_id in order if rule_id in by_id and by_id[rule_id].enabled]
    if not ids:
        return None, cursor, 'empty'
    start = ids.index(cursor) if cursor in ids else 0
    for offset in range(len(ids)):
        index = (start + offset) % len(ids)
        rule_id = ids[index]
        rule = by_id[rule_id]
        if rule.mode == 'stop':
            return None, ids[0], 'stop'
        if rule_id in judges:
            # 条件行：命中与否已由 walk 决定走哪个分支，这里只推进一步。
            event = 'run' if hits.get(rule_id) or rule.mode == 'always' else 'branch'
            return rule, ids[(index + 1) % len(ids)], event
        if rule.mode == 'delay':
            return rule, ids[(index + 1) % len(ids)], 'delay'
        if rule.mode == 'always':
            # 始终执行不比对画面，也不消耗触发次数。
            return rule, ids[(index + 1) % len(ids)], 'run'
        if not hits.get(rule_id):
            return rule, rule_id, 'wait'
        if gate is not None and not gate.ready(rule, now):
            return rule, rule_id, 'wait'
        return rule, ids[(index + 1) % len(ids)], 'run'
    return None, ids[0], 'empty'


def resolve_point(step: dict, matches: dict[str, Match], size: tuple[int, int]) -> tuple[int, int] | None:
    width, height = size
    if step['kind'] == 'click':
        x, y = step['x'], step['y']
    else:
        match = matches.get(step['rule_id'])
        if not match or not match.hit or not match.rect:
            return None
        mx, my, mw, mh = match.rect
        x = mx + mw // 2 + step['offset'][0]
        y = my + mh // 2 + step['offset'][1]
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f'点击位置 ({x}, {y}) 超出画面 {width} × {height}，请调整坐标或偏移')
    return x, y


def execute(plan: list[dict], tap, matches: dict[str, Match], size: tuple[int, int], sleep=time.sleep) -> tuple[list[str], bool, list[list[int]]]:
    """Run click steps. Returns log lines, whether it requested a stop, and tapped points."""
    done: list[str] = []
    clicks: list[list[int]] = []
    stopped = False
    for index, step in enumerate(plan):
        if step['kind'] == 'stop':
            done.append('暂停监测')
            stopped = True
            break
        point = resolve_point(step, matches, size)
        if point is None:
            done.append('匹配目标已消失，跳过点击')
            continue
        tap(*point)
        clicks.append(list(point))
        done.append('点击 (%d, %d)' % point)
        if step.get('interval') and index + 1 < len(plan):
            sleep(step['interval'])
    return done, stopped, clicks


def describe(plan: list[dict], matches: dict[str, Match], size: tuple[int, int]) -> list[str]:
    """Preview the plan without clicking."""
    lines = []
    for step in plan:
        if step['kind'] == 'stop':
            lines.append('暂停监测')
        else:
            try:
                point = resolve_point(step, matches, size)
            except ValueError as exc:
                lines.append(str(exc))
                continue
            lines.append('点击 (%d, %d)' % point if point else '匹配目标已消失，跳过点击')
    return lines


def rule_steps(rule: Rule) -> list[dict]:
    if rule.action == 'pause':
        return [{'kind': 'stop'}]
    if rule.action == 'observe':
        return []
    if rule.action == 'points':
        return [{'kind': 'click', 'x': point[0], 'y': point[1], 'interval': rule.points_interval}
                for _ in range(max(1, rule.points_repeat)) for point in rule.points]
    return [{'kind': 'click_match', 'rule_id': rule.id, 'offset': list(rule.offset), 'interval': 0}]


def rule_clicks(rule: Rule, matches: dict[str, Match], size: tuple[int, int], sleep=time.sleep, tap=None) -> tuple[list[str], bool, list[list[int]]]:
    """Execute one rule action: click the match center, every configured point, or stop."""
    tap = tap or (lambda x, y: None)
    if rule.action == 'observe':
        return ['仅识别，不发送点击'], False, []
    return execute(rule_steps(rule), tap, matches, size, sleep)


def describe_rule(rule: Rule, matches: dict[str, Match], size: tuple[int, int]) -> list[str]:
    if rule.action == 'observe':
        return ['仅识别，不发送点击']
    return describe(rule_steps(rule), matches, size)
