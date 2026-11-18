import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from studio.device import DeviceConfig, parse_display_state, select_display
from studio.flow import advance, describe_rule, execute, rule_clicks, walk
from studio.models import (MODES, Project, Rule, TreeNode, TEXT_MATCHES, collect_images, encode_image, leaf_ids,
                           prune_tree, unique_names)
from studio.ocr import OCRText, text_match
from studio.vision import Match, TriggerGate, choose_rule, gate_reason, match_rule, match_rules

class VisionTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(17)
        self.frame = rng.integers(0, 256, (120, 180, 3), dtype=np.uint8)
        self.rule = Rule(template=encode_image(self.frame[40:60, 70:100]), threshold=.99)

    def test_translation_roi_and_exact_match(self):
        self.rule.region = [50, 30, 80, 60]
        match = match_rule(self.frame, self.rule)
        self.assertTrue(match.hit)
        self.assertEqual(match.rect, (70, 40, 30, 20))

    def test_text_rule_uses_one_region_and_returns_absolute_box(self):
        class FakeOCR:
            def __init__(self):
                self.calls = []
            def recognize(self, frame, region, frame_key=None):
                self.calls.append((frame.shape[:2], region, frame_key))
                return [OCRText('准备', 0.92, (region[0] + 10, region[1] + 5, 40, 20))]

        rule = Rule(mode='text', text='准备', text_region=[50, 30, 80, 40], action='click')
        ocr = FakeOCR()
        match = match_rule(self.frame, rule, ocr=ocr, frame_key=7)
        self.assertTrue(match.hit)
        self.assertEqual(match.text, '准备')
        self.assertEqual(match.rect, (60, 35, 40, 20))
        self.assertEqual(ocr.calls, [((120, 180), [50, 30, 80, 40], 7)])

    def test_text_rule_exact_and_confidence(self):
        class FakeOCR:
            def recognize(self, frame, region, frame_key=None):
                return [OCRText('准备按钮', 0.55, (0, 0, 20, 10))]

        exact = Rule(mode='text', text='准备', text_match='exact', text_region=[0, 0, 20, 20])
        contains = Rule(mode='text', text='准备', text_match='contains', text_confidence=0.5, text_region=[0, 0, 20, 20])
        self.assertFalse(match_rule(self.frame, exact, ocr=FakeOCR()).hit)
        self.assertTrue(match_rule(self.frame, contains, ocr=FakeOCR()).hit)

    def test_solid_template_and_missing_target(self):
        rule = Rule(template=encode_image(np.zeros((10, 10, 3), np.uint8)), threshold=.99)
        self.assertFalse(match_rule(np.full((40, 40, 3), 255, np.uint8), rule).hit)
        self.assertTrue(match_rule(np.zeros((40, 40, 3), np.uint8), rule).hit)

    def test_resolution_and_bounds(self):
        with self.assertRaises(ValueError):
            match_rules(self.frame, [self.rule], [200, 100])
        self.rule.region = [170, 110, 30, 20]
        self.assertFalse(match_rule(self.frame, self.rule).hit)

    def test_priority_and_observe(self):
        low = Rule(name='低优先级')
        observe = Rule(action='observe')
        candidates = [observe, self.rule, low]
        matches = [Match(observe.id, True, 1), Match(self.rule.id, True, .91), Match(low.id, True, .99)]
        self.assertEqual(choose_rule(candidates, matches, TriggerGate()).id, self.rule.id)
        # 已移除「监测阶段」：所有规则都参与识别，不再按阶段过滤。
        self.assertEqual(len(match_rules(self.frame, [self.rule], [180, 120])), 1)

    def test_trigger_modes(self):
        gate = TriggerGate()
        self.assertTrue(gate.ready(self.rule))
        gate.mark(self.rule)
        self.assertFalse(gate.ready(self.rule))
        self.rule.trigger = 'reappear'
        self.assertFalse(gate.ready(self.rule))
        gate.update([Match(self.rule.id, False)])
        self.assertTrue(gate.ready(self.rule))
        self.rule.trigger = 'interval'
        self.assertFalse(gate.ready(self.rule, gate.last[self.rule.id]+1))
        self.assertTrue(gate.ready(self.rule, gate.last[self.rule.id]+3))

    def test_once_rule_rearms_after_target_disappears(self):
        gate = TriggerGate()
        gate.window = 3.0
        gate.update([Match(self.rule.id, True)], now=0)
        gate.mark(self.rule)
        # Still on screen: the click must not repeat on every cycle.
        self.assertFalse(gate.ready(self.rule, now=1))
        self.assertFalse(gate.ready(self.rule, now=2))
        self.assertEqual(gate.update([Match(self.rule.id, True)], now=2), [])
        # A single noisy frame must not re-arm it either.
        self.assertEqual(gate.update([Match(self.rule.id, False)], now=2.4), [])
        self.assertFalse(gate.ready(self.rule, now=2.4))
        # Target gone for longer than the window: the rule becomes clickable again.
        self.assertEqual(gate.update([Match(self.rule.id, False)], now=5.2), [self.rule.id])
        self.assertTrue(gate.ready(self.rule, now=5.2))

    def test_gate_reason_is_specific(self):
        gate = TriggerGate()
        gate.mark(self.rule)
        self.assertIn('本轮已触发过', gate_reason(self.rule, gate))
        self.rule.trigger = 'reappear'
        self.assertIn('消失', gate_reason(self.rule, gate))
        self.rule.trigger = 'interval'
        self.rule.interval = 5
        self.assertIn('还需', gate_reason(self.rule, gate, now=gate.last[self.rule.id] + 2))

class DisplaySelectionTests(unittest.TestCase):
    """MuMu 12 renders app windows on extra virtual displays, so the desktop display
    must never win over the display that owns the running game."""
    PHYSICAL = '\n'.join([
        'Display 4619827820427265280 (HWC display 0): port=0 pnpId=GGL displayName="mumuscreen000"',
        'Display 4619827052952829958 (HWC display 6): port=6 pnpId=GGL displayName="mumuscreen006"',
        'Display 4619827621058019847 (HWC display 7): port=7 pnpId=GGL displayName="mumuscreen007"',
    ])
    DISPLAY = 'mViewports=[' + ', '.join(
        'DisplayViewport{type=INTERNAL, valid=true, isActive=true, displayId=%d, uniqueId=\'local:%s\', physicalPort=%d, '
        'orientation=1, logicalFrame=Rect(0, 0 - 1280, 900), physicalFrame=Rect(0, 0 - 1280, 900), '
        'deviceWidth=1280, deviceHeight=900}' % (logical, physical, port)
        for logical, physical, port in (
            (0, '4619827820427265280', 0), (7, '4619827052952829958', 6), (8, '4619827621058019847', 7)
        )
    ) + ']'
    WINDOW = '\n'.join([
        '  Display: mDisplayId=8 (organized)',
        '  mCurrentFocus=null',
        '  mFocusedApp=null',
        '  Display: mDisplayId=0 (organized)',
        '  mCurrentFocus=null',
        '  mFocusedApp=ActivityRecord{138325733 u0 app.lawnchair/.LawnchairLauncher t2}',
        '  Display: mDisplayId=7 (organized)',
        '  mCurrentFocus=Window{dfd511f u0 com.pandadastudio.ninjamustdie3/com.pandadastudio.ninjamustdie3.MainActivity.alias2}',
        '  mFocusedApp=ActivityRecord{180414293 u0 com.pandadastudio.ninjamustdie3/.MainActivity.alias2 t29}',
        '  mTopFocusedDisplayId=7',
    ])
    DEMO_WINDOW = '\n'.join([
        '  Display: mDisplayId=7 (organized)',
        '  mCurrentFocus=null',
        '  mFocusedApp=null',
        '  Display: mDisplayId=0 (organized)',
        '  mCurrentFocus=null',
        '  mFocusedApp=ActivityRecord{138325733 u0 app.lawnchair/.LawnchairLauncher t2}',
        '  mTopFocusedDisplayId=0',
    ])
    DESKTOP = '4619827820427265280'
    GAME = '4619827052952829958'

    def state(self, window=None):
        return parse_display_state(self.PHYSICAL, self.DISPLAY, window or self.WINDOW)

    def test_parses_physical_to_logical_mapping(self):
        state = self.state()
        self.assertEqual(state.focused_id, 7)
        ids = {d.logical_id: d.physical_id for d in state.displays}
        self.assertEqual(ids, {0: self.DESKTOP, 7: self.GAME, 8: '4619827621058019847'})

    def test_auto_follows_the_game_display(self):
        self.assertEqual(select_display(DeviceConfig(), self.state()).physical_id, self.GAME)

    def test_auto_keeps_the_desktop_when_no_game_runs(self):
        self.assertEqual(select_display(DeviceConfig(), self.state(self.DEMO_WINDOW)).physical_id, self.DESKTOP)

    def test_auto_recovers_when_focus_keeps_the_desktop(self):
        # MuMu can keep the launcher focused while a game loads on its own display.
        stuck = self.WINDOW.replace('mTopFocusedDisplayId=7', 'mTopFocusedDisplayId=0')
        self.assertEqual(select_display(DeviceConfig(), self.state(stuck)).physical_id, self.GAME)

    def test_manual_uses_the_pinned_display(self):
        manual = DeviceConfig(capture_mode='manual', adb_display_id=self.DESKTOP)
        self.assertEqual(select_display(manual, self.state()).physical_id, self.DESKTOP)
        missing = DeviceConfig(capture_mode='manual', adb_display_id='999')
        with self.assertRaises(RuntimeError):
            select_display(missing, self.state())

    def test_ambiguous_focus_and_apps_is_rejected(self):
        two_apps = self.WINDOW.replace('mTopFocusedDisplayId=7', '').replace(
            'mFocusedApp=ActivityRecord{138325733 u0 app.lawnchair/.LawnchairLauncher t2}',
            'mFocusedApp=ActivityRecord{1 u0 com.heytap.market/.MainActivity t3}')
        with self.assertRaises(RuntimeError):
            select_display(DeviceConfig(capture_mode='auto'), self.state(two_apps))


class BatchImportTests(unittest.TestCase):
    def test_collect_images_filters_and_sorts_naturally(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'sub').mkdir()
            for name in ('10.png', '2.PNG', '1.jpg', 'note.txt', 'sub/3.BMP', 'sub/skip.webp'):
                (root / name).write_bytes(b'x')
            found = [path.relative_to(root).as_posix() for path in collect_images(root)]
            self.assertEqual(found, ['1.jpg', '2.PNG', '10.png', 'sub/3.BMP', 'sub/skip.webp'])
            self.assertEqual(len(collect_images(root, limit=2)), 2)
            with self.assertRaises(ValueError):
                collect_images(root / 'missing')

    def test_unique_names_keeps_stems_when_possible(self):
        self.assertEqual(unique_names(['a', 'b'], ['x', 'y']), ['a', 'b'])
        self.assertEqual(unique_names(['a', 'a', 'b'], ['', '', '']), ['a', 'a (2)', 'b'])
        self.assertEqual(unique_names(['a', 'a'], ['x', 'y']), ['a · x', 'a · y'])


class BatchEditTests(unittest.TestCase):
    def test_common_value_detects_mixed_fields(self):
        from studio.window import MainWindow
        rules = [Rule(threshold=0.9), Rule(threshold=0.95)]
        self.assertEqual(MainWindow.common_value(rules, lambda rule: rule.threshold), (None, True))
        self.assertEqual(MainWindow.common_value(rules[:1], lambda rule: rule.threshold), (0.9, False))
        same = [Rule(name='同名'), Rule(name='同名')]
        self.assertEqual(MainWindow.common_value(same, lambda rule: rule.name), ('同名', False))


class ModeTests(unittest.TestCase):
    """规则模式：图像识别 / 始终执行 / 终止。"""
    def test_advance_walks_one_row_at_a_time(self):
        first = Rule(name='A', mode='always', action='points', points=[[10, 20]])
        second = Rule(name='B')
        last = Rule(name='C', mode='stop')
        rules = [first, second, last]
        order = [rule.id for rule in rules]
        gate = TriggerGate()
        # 第一条是「始终执行」：不看画面直接执行，游标推到第二条。
        rule, cursor, event = advance(order, {}, rules, gate, None)
        self.assertEqual((rule, cursor, event), (first, second.id, 'run'))
        # 第二条是图像识别，未命中时停在这一行等待。
        rule, cursor, event = advance(order, {}, rules, gate, second.id)
        self.assertEqual((rule, cursor, event), (second, second.id, 'wait'))
        # 命中后执行，游标推到终止行。
        rule, cursor, event = advance(order, {second.id: True}, rules, gate, second.id)
        self.assertEqual((rule, cursor, event), (second, last.id, 'run'))
        # 终止行不执行动作，游标回到第一条。
        rule, cursor, event = advance(order, {}, rules, gate, last.id)
        self.assertEqual((rule, cursor, event), (None, first.id, 'stop'))

    def test_cursor_falls_back_when_condition_switches_branch(self):
        first, second = Rule(name='甲'), Rule(name='乙')
        rule, cursor, event = advance([second.id], {}, [first, second], TriggerGate(), first.id)
        self.assertEqual((rule, cursor, event), (second, second.id, 'wait'))

    def test_mode_field_is_validated(self):
        self.assertEqual(set(MODES), {'match', 'text', 'always', 'delay', 'stop'})
        self.assertEqual(Rule.from_dict({'name': '常', 'mode': 'always', 'action': 'points', 'points': [[5, 5]]}).mode, 'always')
        with self.assertRaises(ValueError):
            Rule.from_dict({'name': '常', 'mode': 'always', 'action': 'click'})
        with self.assertRaises(ValueError):
            Rule.from_dict({'name': '错', 'mode': 'unknown'})

    def test_text_rule_roundtrip(self):
        rule = Rule(mode='text', text='准备', text_match='exact', text_confidence=0.8, text_region=[10, 20, 300, 60])
        restored = Rule.from_dict(__import__('dataclasses').asdict(rule))
        self.assertEqual(restored.text_region, [10, 20, 300, 60])
        self.assertEqual(restored.text_match, 'exact')
        self.assertEqual(restored.text_confidence, 0.8)

    def test_delay_mode_advances_after_waiting(self):
        delay = Rule(name='等 2 秒', mode='delay', delay=2.0)
        after = Rule(name='后一条')
        order = [delay.id, after.id]
        rule, cursor, event = advance(order, {}, [delay, after], TriggerGate(), None)
        self.assertEqual((rule, cursor, event), (delay, after.id, 'delay'))
        self.assertEqual(Rule.from_dict({'name': '等', 'mode': 'delay', 'delay': 3}).delay, 3)
        self.assertEqual(Rule().delay, 1.0)
        with self.assertRaises(ValueError):
            Rule.from_dict({'name': '等', 'mode': 'delay', 'delay': 0})



class TreeWalkTests(unittest.TestCase):
    """The rule list is a tree: condition rows gate which rules may run."""
    def test_condition_picks_then_or_otherwise(self):
        condition, then_rule, else_rule = Rule(name='宝箱'), Rule(name='点开'), Rule(name='等待')
        branch = TreeNode.branch(condition.id)
        branch.then = [TreeNode.leaf(then_rule.id)]
        branch.otherwise = [TreeNode.leaf(else_rule.id)]
        self.assertEqual(walk([branch], {condition.id: True}), [condition.id, then_rule.id])
        self.assertEqual(walk([branch], {condition.id: False}), [condition.id, else_rule.id])
        # A missing condition behaves like a miss.
        self.assertEqual(walk([branch], {}), [condition.id, else_rule.id])

    def test_flat_tree_keeps_priority_order(self):
        rules = [Rule(name=str(index)) for index in range(3)]
        tree = [TreeNode.leaf(rule.id) for rule in rules]
        self.assertEqual(walk(tree, {}), [rule.id for rule in rules])
        self.assertEqual(leaf_ids(tree), [rule.id for rule in rules])

    def test_nested_conditions(self):
        outer, inner = Rule(name='外'), Rule(name='内')
        first, second, third = Rule(name='甲'), Rule(name='乙'), Rule(name='丙')
        child = TreeNode.branch(inner.id)
        child.then = [TreeNode.leaf(first.id)]
        child.otherwise = [TreeNode.leaf(second.id)]
        branch = TreeNode.branch(outer.id)
        branch.then = [child]
        branch.otherwise = [TreeNode.leaf(third.id)]
        self.assertEqual(walk([branch], {outer.id: True, inner.id: True}), [outer.id, inner.id, first.id])
        self.assertEqual(walk([branch], {outer.id: True, inner.id: False}), [outer.id, inner.id, second.id])
        self.assertEqual(walk([branch], {}), [outer.id, third.id])

    def test_condition_row_runs_then_opens_its_branch(self):
        condition, action, otherwise = Rule(name='条件'), Rule(name='则'), Rule(name='否则')
        branch = TreeNode.branch(condition.id)
        branch.then = [TreeNode.leaf(action.id)]
        branch.otherwise = [TreeNode.leaf(otherwise.id)]
        order = walk([branch], {condition.id: True})
        self.assertEqual(order, [condition.id, action.id])
        rule, cursor, event = advance(order, {condition.id: True}, [condition, action], TriggerGate(), None, judges={condition.id})
        self.assertEqual((rule.id, cursor, event), (condition.id, action.id, 'run'))
        # 未命中：条件行不等待，直接交给「否则」。
        order = walk([branch], {condition.id: False})
        rule, cursor, event = advance(order, {}, [condition, otherwise], TriggerGate(), None, judges={condition.id})
        self.assertEqual((rule.id, cursor, event), (condition.id, otherwise.id, 'branch'))
        self.assertEqual(leaf_ids([branch]), [action.id, otherwise.id])

    def test_prune_tree_drops_deleted_rule_and_its_condition(self):
        keep, gone, child = Rule(), Rule(), Rule()
        branch = TreeNode.branch(gone.id)
        branch.then = [TreeNode.leaf(child.id)]
        nodes, removed = prune_tree([TreeNode.leaf(keep.id), branch], {gone.id})
        self.assertEqual(removed, 1)
        self.assertEqual(leaf_ids(nodes), [keep.id])
        nodes, removed = prune_tree([TreeNode.leaf(keep.id), TreeNode.leaf(child.id)], {child.id})
        self.assertEqual(leaf_ids(nodes), [keep.id])
        self.assertEqual(removed, 1)


class RulePointsTests(unittest.TestCase):
    def test_rule_points_roundtrip(self):
        rule = Rule(action='points', points=[[10, 20], [30, 40]], points_interval=0.8, points_repeat=2)
        raw = {field: getattr(rule, field) for field in Rule.__dataclass_fields__}
        restored = Rule.from_dict(raw)
        self.assertEqual(restored.points, [[10, 20], [30, 40]])
        self.assertEqual(restored.points_interval, 0.8)
        self.assertEqual(restored.points_repeat, 2)
        with self.assertRaises(ValueError):
            Rule.from_dict(dict(raw, points=[], action='points'))

    def test_rule_points_clicking(self):
        rule = Rule(action='points', points=[[5, 6], [7, 8]], points_interval=0.3, points_repeat=2)
        taps, sleeps = [], []
        actions, stopped, clicks = rule_clicks(rule, {}, (100, 100), sleep=sleeps.append, tap=lambda x, y: taps.append((x, y)))
        self.assertEqual(taps, [(5, 6), (7, 8), (5, 6), (7, 8)])
        self.assertEqual(sleeps, [0.3, 0.3, 0.3])
        self.assertFalse(stopped)
        self.assertEqual(clicks[-1], [7, 8])
        self.assertEqual(len(actions), 4)

    def test_click_and_pause_actions(self):
        rule = Rule(action='click', offset=[5, -5])
        match = Match(rule.id, True, 0.99, (10, 20, 30, 40))
        taps = []
        rule_clicks(rule, {rule.id: match}, (200, 200), sleep=lambda seconds: None, tap=lambda x, y: taps.append((x, y)))
        self.assertEqual(taps, [(30, 35)])
        done, stopped, clicks = rule_clicks(Rule(action='pause'), {}, (200, 200), sleep=lambda seconds: None, tap=lambda x, y: None)
        self.assertTrue(stopped)
        self.assertEqual(clicks, [])
        self.assertEqual(done, ['暂停监测'])
        self.assertEqual(rule_clicks(Rule(action='observe'), {}, (200, 200), sleep=lambda s: None, tap=lambda x, y: None)[0],
                         ['仅识别，不发送点击'])

    def test_missing_match_is_skipped_and_bounds_rejected(self):
        rule = Rule(action='click')
        taps = []
        done, _, _ = rule_clicks(rule, {}, (200, 200), sleep=lambda seconds: None, tap=lambda x, y: taps.append((x, y)))
        self.assertEqual(taps, [])
        self.assertIn('跳过点击', done[0])
        with self.assertRaises(ValueError):
            execute([{'kind': 'click', 'x': 500, 'y': 10, 'interval': 0}], lambda x, y: None, {}, (200, 200), lambda seconds: None)

    def test_preview_does_not_click(self):
        rule = Rule(action='click')
        match = Match(rule.id, True, 0.9, (0, 0, 10, 10))
        self.assertEqual(describe_rule(rule, {rule.id: match}, (100, 100)), ['点击 (5, 5)'])
        self.assertEqual(describe_rule(Rule(action='observe'), {}, (100, 100)), ['仅识别，不发送点击'])

    def test_bad_points_rejected(self):
        base = {field: getattr(Rule(), field) for field in Rule.__dataclass_fields__}
        for bad in ({'points': 'x'}, {'points': [[1]]}, {'points': [[1.5, 2]]}, {'points_interval': -1}, {'points_repeat': 0}):
            with self.assertRaises(ValueError):
                Rule.from_dict(dict(base, **bad))


class TreeModelTests(unittest.TestCase):
    def test_project_tree_save_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tree.mumu.json'
            condition, action = Rule(name='宝箱'), Rule(name='点开')
            branch = TreeNode.branch(condition.id)
            branch.then = [TreeNode.leaf(action.id)]
            project = Project(rules=[action, condition], tree=[branch])
            project.save(path)
            restored = Project.load(path)
            self.assertEqual(restored.payload(), project.payload())
            self.assertEqual(restored.tree[0].then[0].rule_id, action.id)
            # Priority always matches the list: leaves first, then conditions.
            self.assertEqual([rule.id for rule in restored.rules], [action.id, condition.id])

    def test_duplicate_and_ghost_rows_are_cleaned(self):
        rule = Rule()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dup.mumu.json'
            payload = Project(rules=[rule]).payload()
            payload['tree'] = [
                {'kind': 'rule', 'rule_id': rule.id, 'then': [], 'otherwise': []},
                {'kind': 'rule', 'rule_id': rule.id, 'then': [], 'otherwise': []},
                {'kind': 'rule', 'rule_id': 'ghost', 'then': [], 'otherwise': []},
            ]
            path.write_text(json.dumps(payload), encoding='utf-8')
            restored = Project.load(path)
            self.assertEqual(leaf_ids(restored.tree), [rule.id])
            self.assertTrue(restored.load_notes)

    def test_branch_survives_a_duplicate_leaf_from_old_files(self):
        # 旧版「添加条件」会在原位保留一条普通行，导致同一规则出现两次；
        # 加载时必须优先保留条件分支，否则用户的条件会「消失」。
        condition, action = Rule(name='条件'), Rule(name='则')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dup-branch.mumu.json'
            payload = Project(rules=[condition, action]).payload()
            payload['tree'] = [
                {'kind': 'rule', 'rule_id': condition.id, 'then': [], 'otherwise': []},
                {'kind': 'if', 'rule_id': condition.id, 'then': [{'kind': 'rule', 'rule_id': action.id, 'then': [], 'otherwise': []}], 'otherwise': []},
            ]
            path.write_text(json.dumps(payload), encoding='utf-8')
            restored = Project.load(path)
            self.assertEqual([node.kind for node in restored.tree], ['if'])
            self.assertEqual(restored.tree[0].then[0].rule_id, action.id)
            self.assertEqual(leaf_ids(restored.tree), [action.id])
            restored.save(path)
            again = Project.load(path)
            self.assertEqual(again.tree[0].kind, 'if')
            self.assertEqual(again.tree[0].then[0].rule_id, action.id)

    def test_rule_row_cannot_have_children(self):
        rule = Rule()
        with self.assertRaises(ValueError):
            TreeNode.from_dict({'kind': 'rule', 'rule_id': rule.id,
                                'then': [{'kind': 'rule', 'rule_id': rule.id}], 'otherwise': []})

    def test_old_version_falls_back_to_a_flat_list(self):
        rule = Rule()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'old.mumu.json'
            payload = Project(rules=[rule]).payload()
            payload['version'] = 2
            payload.pop('tree')
            path.write_text(json.dumps(payload), encoding='utf-8')
            restored = Project.load(path)
            self.assertEqual(leaf_ids(restored.tree), [rule.id])
            self.assertTrue(restored.load_notes)

    def test_removed_stage_fields_are_ignored(self):
        rule = Rule(name='旧规则')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'staged.mumu.json'
            payload = Project(rules=[rule]).payload()
            payload['version'] = 3
            payload['stage'] = '选卡'
            for item in payload['rules']:
                item['stage'] = '准备'
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            restored = Project.load(path)
            self.assertFalse(hasattr(restored, 'stage'))
            self.assertFalse(hasattr(restored.rules[0], 'stage'))
            self.assertEqual(restored.rules[0].name, '旧规则')


class StorageTests(unittest.TestCase):
    def test_portable_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '中文方案.json'
            project = Project('方案', rules=[Rule(name='准备')])
            project.save(path)
            self.assertEqual(Project.load(path).payload(), project.payload())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_invalid_config_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'invalid.json'
            for mutate in [lambda d: d.update(version=999), lambda d: d['rules'][0].update(threshold=float('nan')), lambda d: d['rules'][0].update(template='not-an-image'), lambda d: d['rules'].append(d['rules'][0])]:
                payload = Project(rules=[Rule()]).payload()
                mutate(payload)
                path.write_text(json.dumps(payload), encoding='utf-8')
                with self.assertRaises(ValueError):
                    Project.load(path)

if __name__ == '__main__':
    unittest.main()
