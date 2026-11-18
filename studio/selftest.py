from __future__ import annotations
import copy
import sys
import time
from pathlib import Path
from unittest.mock import patch
import cv2
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFileDialog, QMessageBox
from .demo import demo_project
from .device import DeviceConfig
from .models import Project, Rule, TreeNode, decode_image, encode_image
from .vision import Match, TriggerGate
import numpy as np


def wait_worker(window, timeout=25):
    deadline = time.monotonic() + timeout
    while window.worker and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(20)
    if window.worker:
        raise AssertionError('Background operation did not finish')
    QApplication.processEvents()


def rule_count(window):
    return len(window.project.rules)


def payload_diff(left, right, path=''):
    if type(left) is not type(right):
        return [f'TYPE {path} {type(left).__name__} != {type(right).__name__}']
    if isinstance(left, dict):
        out = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                out.append(f'KEY {path}/{key} {key in left} {key in right}')
            else:
                out.extend(payload_diff(left[key], right[key], f'{path}/{key}'))
        return out
    if isinstance(left, list):
        out = []
        if len(left) != len(right):
            out.append(f'LEN {path} {len(left)} != {len(right)}')
        for index, (x, y) in enumerate(zip(left, right)):
            out.extend(payload_diff(x, y, f'{path}[{index}]'))
        return out
    if left != right:
        return [f'VAL {path} {left!r} != {right!r}']
    return []


def run_smoke(window, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    errors = []
    original_error = window.error
    window.error = lambda message: errors.append(message)
    rule_attr = window.rule_tree.RULE
    assert window.isVisible() and rule_count(window) == 4
    checks.append('main_window_visible_and_demo_loaded')
    QTest.mouseClick(window.test_btn, Qt.MouseButton.LeftButton)
    wait_worker(window)
    assert not errors, errors
    # 已移除「监测阶段」，四条规则全部参与识别。
    assert sum(m.hit for m in window.matches) == 4
    checks.append('background_matching_button_and_priority_decision')
    before = window.project.rules[0].id
    window.rule_tree.setCurrentItem(window.rule_items()[0])
    QTest.mouseClick(window.down_btn, Qt.MouseButton.LeftButton)
    assert window.project.rules[1].id == before
    assert window.project.tree[1].rule_id == before
    QTest.mouseClick(window.up_btn, Qt.MouseButton.LeftButton)
    assert window.project.rules[0].id == before
    checks.append('priority_move_buttons_sync_tree')
    count = rule_count(window)
    window.rule_tree.setCurrentItem(window.rule_items()[0])
    QTest.mouseClick(window.copy_btn, Qt.MouseButton.LeftButton)
    assert rule_count(window) == count + 1
    assert len({r.id for r in window.project.rules}) == count + 1
    QTest.mouseClick(window.delete_btn, Qt.MouseButton.LeftButton)
    assert rule_count(window) == count
    checks.append('rule_copy_delete_unique_ids')
    window.rule_tree.setCurrentItem(window.rule_items()[0])
    QTest.mouseClick(window.add_btn, Qt.MouseButton.LeftButton)
    new_rule = window.selected_rule()
    window.rule_name.setText('测试规则 · 中文')
    window.rule_name.editingFinished.emit()
    window.threshold.setValue(0.95)
    window.action.setCurrentIndex(window.action.findData('observe'))
    assert new_rule.name == '测试规则 · 中文' and new_rule.threshold == 0.95 and new_rule.action == 'observe'
    checks.append('property_editing')
    window.canvas.fit_image()
    QApplication.processEvents()
    window.select_mode('template')
    start = window.canvas.mapFromScene(QPointF(130, 260))
    end = window.canvas.mapFromScene(QPointF(270, 390))
    QTest.mousePress(window.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window.canvas.viewport(), end, 30)
    QTest.mouseRelease(window.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert new_rule.template
    template = decode_image(new_rule.template)
    assert 125 <= template.shape[0] <= 135 and 135 <= template.shape[1] <= 145
    checks.append('mouse_crop_maps_view_to_image_coordinates')
    window.canvas_selected('region', [100, 200, 400, 480])
    assert new_rule.region == [100, 200, 400, 480]
    window.canvas_selected('point', [200, 325])
    assert new_rule.action == 'click' and max(abs(v) for v in new_rule.offset) <= 3
    checks.append('search_region_and_click_anchor')
    # Multi selection: mismatched parameters show "—", edits apply to every selected rule.
    names_before = [rule.name for rule in window.project.rules]
    window.select_all.click()
    QApplication.processEvents()
    assert len(window.selected_ids()) == rule_count(window)
    assert window.mixed.get('name') and window.mixed.get('threshold')
    assert window.rule_name.text() == '' and window.rule_name.placeholderText() == '—'
    assert window.threshold.specialValueText() == '—'
    assert window.region_text.text() == '多个搜索区域'
    assert window.template_preview.text().startswith('多个模板')
    window.rule_name.setFocus()
    QTest.qWait(30)
    window.canvas.setFocus()
    QTest.qWait(30)
    assert [rule.name for rule in window.project.rules] == names_before
    window.threshold.setValue(0.95)
    QApplication.processEvents()
    assert all(rule.threshold == 0.95 for rule in window.project.rules)
    assert window.threshold.specialValueText() == ''
    window.action.setCurrentIndex(window.action.findData('observe'))
    QApplication.processEvents()
    assert all(rule.action == 'observe' for rule in window.project.rules)
    window.enabled.setCheckState(Qt.CheckState.Unchecked)
    QApplication.processEvents()
    assert not any(rule.enabled for rule in window.project.rules)
    assert window.rule_items()[0].checkState(0) == Qt.CheckState.Unchecked
    checks.append('multi_select_batch_edit_applies_to_all')
    window.rule_tree.clearSelection()
    window.rule_tree.setFocus()
    QTest.keyClick(window.rule_tree, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QApplication.processEvents()
    assert len(window.selected_ids()) == rule_count(window)
    assert window.select_all.checkState() == Qt.CheckState.Checked
    checks.append('multi_select_ctrl_a_shortcut')
    saved = output_dir / 'smoke-roundtrip.mumu.json'
    window.path = saved
    assert window.save_project()
    restored = Project.load(saved)
    diff = payload_diff(window.project.payload(), restored.payload())
    assert not diff, diff[:12]
    window.replace_project(restored, saved)
    assert not window.dirty and rule_count(window) == 5
    checks.append('save_reload_with_embedded_images')
    # Batch import every image of a folder as its own rule, in file name order.
    folder = output_dir / 'batch-templates'
    folder.mkdir(parents=True, exist_ok=True)
    for index, rule in enumerate(window.project.rules[:2], start=1):
        image = decode_image(rule.template)
        (folder / f'{index:02d}.png').write_bytes(cv2.imencode('.png', image)[1].tobytes())
    (folder / 'note.txt').write_text('not an image')
    before = rule_count(window)
    with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(folder)):
        window.batch_import_rules()
        wait_worker(window)
    assert not errors, errors
    imported = window.project.rules[before:]
    assert [rule.name for rule in imported] == ['01', '02'], [rule.name for rule in imported]
    assert all(rule.action == 'click' and rule.threshold == 0.90 for rule in imported)
    assert all(decode_image(rule.template).size for rule in imported)
    assert rule_count(window) == before + 2
    checks.append('batch_import_folder_of_templates')
    imported_ids = {rule.id for rule in imported}
    for item in window.rule_items():
        item.setSelected(item.data(0, rule_attr) in imported_ids)
    QApplication.processEvents()
    assert len(window.selected_ids()) == 2
    window.delete_rule()
    QApplication.processEvents()
    assert rule_count(window) == before
    checks.append('multi_select_delete_selected_rules')
    # Condition branch: a condition row gates which rules may run this cycle.
    window.replace_project(demo_project())
    condition = window.project.rules[0]
    then_rule, else_rule = window.project.rules[1], window.project.rules[2]
    branch = TreeNode.branch(condition.id)
    branch.then = [TreeNode.leaf(then_rule.id)]
    branch.otherwise = [TreeNode.leaf(else_rule.id)]
    window.project.tree = [branch, TreeNode.leaf(window.project.rules[3].id)]
    window.sync_rule_order()
    window.refresh_rules()
    QApplication.processEvents()
    assert len(window.tree_nodes()) >= 2 and any(node.kind == 'if' for node in window.tree_nodes())
    assert window.rule_tree.topLevelItem(0).text(0).startswith('条件：识别到「')
    # Simulate a hit on the condition: only the "then" rule may run.
    window.auto_click.setChecked(True)
    taps = []
    frame = window.frame.copy()
    blank = np.zeros_like(frame)

    class FakeDevice:
        config = DeviceConfig(adb_display_id='0')
        source_description = '模拟设备 / 自动跟随前台'
        current = frame

        def screenshot(self):
            return FakeDevice.current.copy()

        def tap(self, x, y, verify=True):
            assert verify is False, '监测点击应复用本次截图对应的显示器'
            taps.append((x, y))

    window.monitoring = True
    window.gate = TriggerGate()
    window.gate.window = 0
    with patch.object(window, 'sync_device', return_value=FakeDevice()):
        FakeDevice.current = frame
        window.monitor_tick()
        wait_worker(window)
        # 条件行自己也是一步：命中时先执行它的动作，游标随即进入「则」分支。
        assert taps, '条件行命中时应执行它自己的动作'
        assert window.cursor == then_rule.id, window.cursor
        first_click = taps[-1]
        window.monitor_tick()
        wait_worker(window)
        assert taps[-1] != first_click, '第二次应执行「则」里的规则，而不是重复条件行'
    window.monitoring = False
    window.timer.stop()
    window.auto_click.setChecked(False)
    checks.append('condition_row_runs_then_enters_then_branch')
    # 条件未命中：走「否则」，不点击，也不停在条件行上。
    taps.clear()
    window.cursor = None
    window.gate = TriggerGate()
    window.gate.window = 0
    window.monitoring = True
    with patch.object(window, 'sync_device', return_value=FakeDevice()):
        FakeDevice.current = blank
        window.monitor_tick()
        wait_worker(window)
    window.monitoring = False
    window.timer.stop()
    assert not taps, taps
    assert window.cursor == else_rule.id, window.cursor
    assert '条件不成立' in window.logs.toPlainText()
    FakeDevice.current = frame
    checks.append('condition_miss_enters_otherwise_without_clicking')
    # Flipping the condition off makes the "otherwise" rule the only candidate.
    from .flow import hit_map, walk
    matches = [Match(condition.id, False, 1.0), Match(then_rule.id, True, 0.99), Match(else_rule.id, True, 0.99)]
    allowed = walk(window.project.tree, hit_map(matches))
    assert then_rule.id not in allowed and else_rule.id in allowed, allowed
    assert condition.id in allowed
    checks.append('condition_miss_switches_to_otherwise')
    # Tree can be saved and reloaded with the branch structure intact.
    tree_path = output_dir / 'smoke-tree.mumu.json'
    window.path = tree_path
    assert window.save_project()
    reloaded = Project.load(tree_path)
    diff = payload_diff(window.project.payload(), reloaded.payload())
    assert not diff, diff[:12]
    assert reloaded.tree[0].kind == 'if' and reloaded.tree[0].then[0].rule_id == then_rule.id
    window.replace_project(reloaded, tree_path)
    assert window.rule_tree.topLevelItem(0).text(0).startswith('条件：识别到「')
    checks.append('condition_tree_save_and_reload')
    # Dragging a rule into a condition's「则」keeps the rule and leaves the branch expanded.
    window.replace_project(demo_project())
    first, second, third = (rule.id for rule in window.project.rules[:3])
    branch = TreeNode.branch(first)
    branch.then = [TreeNode.leaf(second)]
    window.project.tree = [branch, TreeNode.leaf(third)]
    window.sync_rule_order()
    window.refresh_rules()
    QApplication.processEvents()
    condition_item = next(item for item in window.rule_items() if item.data(0, window.rule_tree.KIND) == 'if')
    moved_item = next(item for item in window.rule_items() if item.data(0, window.rule_tree.RULE) == third)
    window.rule_tree.setCurrentItem(moved_item)
    window.tree_dropped(item=condition_item.child(0), position=QAbstractItemView.DropIndicatorPosition.OnItem, source_item=moved_item)
    QApplication.processEvents()
    assert window.project.tree[0].kind == 'if', window.project.tree
    assert [node.rule_id for node in window.project.tree[0].then] == [second, third], window.project.tree[0].then
    assert window.rule_tree.topLevelItem(0).isExpanded()
    assert not any(item.data(0, window.rule_tree.RULE) == third and item.data(0, window.rule_tree.KIND) == 'rule' and item.parent() is None for item in window.rule_items())
    checks.append('drag_into_condition_branch_keeps_rule_and_expands')
    # Dropping straight onto the condition row joins its「则」instead of losing the rule.
    window.tree_dropped(item=window.rule_tree.topLevelItem(0), position=QAbstractItemView.DropIndicatorPosition.OnItem, source_item=window.rule_tree.topLevelItem(0).child(0).child(0))
    QApplication.processEvents()
    assert any(node.rule_id == third for node in window.tree_nodes())
    checks.append('drag_onto_condition_row_joins_then')
    # Deleting a condition's rule removes the branch referencing it.
    window.replace_project(demo_project())
    victim = window.project.rules[0]
    branch = TreeNode.branch(victim.id)
    branch.then = [TreeNode.leaf(window.project.rules[1].id)]
    window.project.tree = [branch]
    window.sync_rule_order()
    window.refresh_rules()
    window.rule_tree.setCurrentItem(window.rule_items()[0])
    window.delete_rule()
    QApplication.processEvents()
    assert not any(node.kind == 'if' for node in window.tree_nodes())
    checks.append('delete_rule_prunes_condition_branch')
    # Rule-level manual multi point clicks: several coordinates + interval/repeat.
    window.replace_project(demo_project())
    window.rule_tree.setCurrentItem(window.rule_items()[0])
    window.action.setCurrentIndex(window.action.findData('points'))
    QApplication.processEvents()
    assert window.rule_points.isEnabled()
    window.rule_point_x.setValue(300)
    window.rule_point_y.setValue(420)
    window.add_rule_point()
    window.rule_point_x.setValue(320)
    window.rule_point_y.setValue(460)
    window.add_rule_point()
    target = window.project.rules[0]
    assert target.action == 'points' and [list(p) for p in target.points][-2:] == [[300, 420], [320, 460]], target.points
    assert window.rule_points.count() == len(target.points)
    window.rule_points_interval.setValue(0)
    window.rule_points_repeat.setValue(2)
    QApplication.processEvents()
    assert target.points_interval == 0 and target.points_repeat == 2
    taps.clear()

    class PointDevice(FakeDevice):
        def tap(self, x, y, verify=True):
            assert verify is False
            taps.append((x, y))
    with patch.object(window, 'sync_device', return_value=PointDevice()), patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        window.single_step()
        wait_worker(window)
    assert len(taps) == 2 * len(target.points), taps
    checks.append('rule_manual_multi_point_clicks')
    window.resize(1180, 760)
    QApplication.processEvents()
    assert window.width() == 1180
    assert window.property_scroll.horizontalScrollBar().maximum() == 0, 'Property panel overflows horizontally'
    window.grab().save(str(output_dir / 'gui-points.png'))
    window.replace_project(demo_project())
    # Single-step wiring with a fake device never clicks the real game.
    real_config = copy.deepcopy(window.config)
    taps.clear()
    demo_frame = window.frame.copy()

    class SingleDevice(FakeDevice):
        def tap(self, x, y, verify=True):
            assert verify is False
            taps.append((x, y))
    with patch.object(window, 'sync_device', return_value=SingleDevice()), patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        window.single_step()
        wait_worker(window)
    assert taps == [(260, 440)], taps
    assert not window.gate.ready(window.project.rules[0])
    checks.append('single_step_mock_click_coordinates_and_once_gate')
    # 规则模式：始终执行不比对画面，图像识别未命中则停在原地，终止行回到第一条。
    base = window.frame.copy()
    always = Rule(name='始终点击', mode='always', action='points', points=[[300, 300]])
    waiting = Rule(name='等待图标', template=encode_image(np.zeros((20, 20, 3), np.uint8)), region=[0, 0, 60, 60], threshold=0.99)
    stop = Rule(name='回到开头', mode='stop')
    modes = [always, waiting, stop]
    mode_project = Project('模式测试', modes, encode_image(base), [base.shape[1], base.shape[0]],
                           [TreeNode.leaf(rule.id) for rule in modes])
    window.replace_project(mode_project)
    assert always.mode == 'always' and not always.template
    taps.clear()
    window.auto_click.setChecked(True)
    window.monitoring = True
    window.gate = TriggerGate()
    window.gate.window = 0
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.monitor_tick()
        wait_worker(window)
    assert taps == [(300, 300)], taps
    assert window.active_id == always.id and window.cursor == waiting.id
    highlighted = [item for item in window.rule_items() if item.background(0).color().name() == '#1d4738']
    assert len(highlighted) == 1 and highlighted[0].data(0, rule_attr) == always.id, '执行到的行应在列表里高亮'
    checks.append('mode_always_runs_without_a_template')
    before = len(taps)
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.monitor_tick()
        wait_worker(window)
    assert len(taps) == before, taps
    assert window.cursor == waiting.id and window.active_id == waiting.id
    assert '等待：等待图标' in window.logs.toPlainText()
    checks.append('match_mode_waits_on_its_row')
    window.cursor = stop.id
    before = len(taps)
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.monitor_tick()
        wait_worker(window)
    assert len(taps) == before, taps
    assert window.cursor == always.id and window.active_id == always.id
    assert '回到列表第一条' in window.logs.toPlainText()
    checks.append('stop_mode_rewinds_to_the_first_rule')
    window.monitoring = False
    window.timer.stop()
    window.auto_click.setChecked(False)
    modes_path = output_dir / 'smoke-modes.mumu.json'
    window.path = modes_path
    assert window.save_project()
    assert [rule.mode for rule in Project.load(modes_path).rules] == ['always', 'match', 'stop']
    checks.append('mode_roundtrip_in_project_file')
    # 文字识别模式的 UI 配置：单个区域、模板控件禁用，保存后可回读。
    window.replace_project(demo_project())
    window.mode.setCurrentIndex(window.mode.findData('text'))
    window.mode.activated.emit(window.mode.currentIndex())
    window.text_target.setText('准备')
    window.text_target.editingFinished.emit()
    window.canvas_selected('text_region', [400, 300, 300, 100])
    QApplication.processEvents()
    text_rule = window.project.rules[0]
    assert text_rule.mode == 'text' and text_rule.text == '准备' and text_rule.text_region == [400, 300, 300, 100]
    assert window.text_region_btn.isEnabled() and not window.template_import_btn.isEnabled()
    text_path = output_dir / 'smoke-text.mumu.json'
    window.path = text_path
    assert window.save_project()
    text_reloaded = Project.load(text_path)
    assert text_reloaded.rules[0].mode == 'text' and text_reloaded.rules[0].text_region == [400, 300, 300, 100]
    checks.append('text_mode_region_roundtrip')
    # 模式只改当前规则：即使处于多选状态，也不应批量应用到所有规则。
    window.replace_project(demo_project())
    window.select_all.click()
    QApplication.processEvents()
    window.rule_tree.setCurrentItem(window.rule_items()[1])
    QApplication.processEvents()
    window.mode.setCurrentIndex(window.mode.findData('always'))
    window.mode.activated.emit(window.mode.currentIndex())
    QApplication.processEvents()
    assert [rule.mode for rule in window.project.rules] == ['match', 'always', 'match', 'match'], [r.mode for r in window.project.rules]
    checks.append('mode_edit_only_touches_the_current_rule')
    # 延迟执行：等到指定秒数后游标前进，不点击也不需要模板。
    window.replace_project(demo_project())
    delay_rule = window.project.rules[0]
    delay_rule.mode = 'delay'
    delay_rule.delay = 0.05
    window.refresh_rules(delay_rule.id)
    window.cursor = delay_rule.id
    taps.clear()
    window.monitoring = True
    window.gate = TriggerGate()
    window.gate.window = 0
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.monitor_tick()
        wait_worker(window)
    window.monitoring = False
    window.timer.stop()
    assert taps == [], taps
    assert window.cursor == window.project.rules[1].id, window.cursor
    assert window.active_id == delay_rule.id
    assert '已延迟' in window.logs.toPlainText()
    checks.append('delay_mode_waits_then_continues')
    # Auto execution: monitor loop sends the click, then the once gate holds.
    window.replace_project(demo_project())
    taps.clear()
    window.auto_click.setChecked(True)
    window.monitoring = True
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        # 4 条规则全部命中，每轮点一条，4 轮后每条都已触发过。
        for _ in range(4):
            window.monitor_tick()
            wait_worker(window)
        clicked = len(taps)
        for _ in range(2):
            window.monitor_tick()
            wait_worker(window)
    window.monitoring = False
    window.timer.stop()
    window.auto_click.setChecked(False)
    assert clicked == 4 and len(set(taps)) == 4, taps
    assert len(taps) == clicked, taps
    assert taps[0] == (260, 440), taps
    assert '已自动点击' in window.logs.toPlainText()
    checks.append('monitor_auto_click_each_rule_once_then_gated')
    # 回归：监测循环必须由定时器驱动，不能只靠 toggle_monitor 里那一次手动调用。
    window.replace_project(demo_project())
    taps.clear()
    window.auto_click.setChecked(True)
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.toggle_monitor()
        assert window.timer.isActive(), '开始监测后定时器应在运行'
        wait_worker(window)
        assert window.worker is None
        clicked_first = len(taps)
        cursor_first = window.cursor
        # 直接触发定时器信号：不手动调用 monitor_tick，模拟真实监测循环。
        window.timer.timeout.emit()
        wait_worker(window)
        assert len(taps) > clicked_first, '定时器超时后应继续推进下一条规则'
        assert window.cursor != cursor_first, (cursor_first, window.cursor)
    window.stop_monitor()
    window.auto_click.setChecked(False)
    checks.append('monitor_timer_signal_drives_cycles')
    # Once a target disappears the once-trigger re-arms automatically.
    window.replace_project(demo_project())
    rule = window.project.rules[0]
    window.gate = TriggerGate()
    window.gate.window = 0
    window.gate.update([Match(rule.id, True)], now=0)
    window.gate.mark(rule)
    assert not window.gate.ready(rule, now=1)
    assert window.gate.update([Match(rule.id, False)], now=10) == [rule.id]
    assert window.gate.ready(rule, now=10)
    checks.append('once_trigger_rearms_after_target_disappears')
    window.replace_project(demo_project())
    taps.clear()
    blank = np.zeros_like(window.frame)
    good = window.frame.copy()

    class Switchable(SingleDevice):
        current = None

        def screenshot(self):
            return Switchable.current.copy()
    window.auto_click.setChecked(True)
    window.monitoring = True
    window.gate = TriggerGate()
    window.gate.window = 0
    with patch.object(window, 'sync_device', return_value=Switchable()):
        Switchable.current = good.copy()
        for _ in range(2):
            window.monitor_tick()
            wait_worker(window)
        clicked_before = len(taps)
        Switchable.current = blank
        window.monitor_tick()
        wait_worker(window)
        Switchable.current = good.copy()
        window.monitor_tick()
        wait_worker(window)
    window.monitoring = False
    window.timer.stop()
    window.auto_click.setChecked(False)
    assert clicked_before == 2, taps
    assert len(taps) == 3, taps
    assert '重新允许触发' in window.logs.toPlainText()
    checks.append('monitor_reclicks_after_target_disappears')
    # 测试当前画面时暂停实时预览，便于观察识别框；在画面上点一下恢复实时更新。
    window.replace_project(demo_project())
    window.test_frame()
    wait_worker(window)
    assert window.preview_paused, '测试当前画面后应暂停实时预览'
    assert '暂停' in window.canvas_hint.text(), window.canvas_hint.text()
    window.canvas.pressed.emit()
    assert not window.preview_paused, '点击画面后应恢复实时预览'
    checks.append('test_frame_pauses_live_preview_until_canvas_click')
    # 监测期间界面不应逐轮禁用/启用（会闪烁）；规则树保持可点选以便观察每条规则。
    window.replace_project(demo_project())
    with patch.object(window, 'sync_device', return_value=SingleDevice()):
        window.toggle_monitor()
        wait_worker(window)
        assert window.rule_tree.isEnabled(), '监测时规则树应保持可点选'
        assert not window.form.isEnabled() and not window.save_btn.isEnabled(), '监测时编辑控件应保持禁用'
        # 模拟定时器逐轮起任务：busy 切换不得改变启用状态。
        window.set_busy(False)
        window.set_busy(True)
        assert not window.form.isEnabled() and not window.save_btn.isEnabled()
        assert window.rule_tree.isEnabled()
        window.rule_tree.setCurrentItem(window.rule_items()[0])
        QApplication.processEvents()
        assert window.rule_tree.currentItem() is not None, '监测时应能选中规则行'
        assert not window.crop_btn.isEnabled(), '监测时不允许截取模板'
        window.stop_monitor()
        wait_worker(window)
    assert window.save_btn.isEnabled() and window.rule_tree.isEnabled(), '停止监测后应恢复编辑'
    assert window.rule_tree.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    checks.append('monitor_keeps_ui_locked_without_flicker')
    # Real device read-only integration through the same GUI worker path.
    window.replace_project(demo_project())
    window.config = real_config
    window.connect_device()
    wait_worker(window)
    window.capture()
    wait_worker(window)
    assert not errors, errors
    assert window.frame_source.startswith(('实时设备画面', '实时画面')) and window.frame.size > 0
    assert window.device.last_display is not None, 'Auto mode did not resolve a screenshot display'
    real_size = [window.frame.shape[1], window.frame.shape[0]]
    real_display = window.device.source_description
    checks.append('real_adb_connection_and_gui_screenshot')
    window.replace_project(demo_project())
    window.test_frame()
    wait_worker(window)
    assert not errors, errors
    window.resize(1180, 760)
    QApplication.processEvents()
    assert window.width() == 1180 and window.height() == 760
    assert window.centralWidget().rect().contains(window.columns.geometry().topLeft())
    assert window.property_scroll.horizontalScrollBar().maximum() == 0, 'Property panel overflows horizontally'
    window.grab().save(str(output_dir / 'gui-compact.png'))
    window.resize(1500, 980)
    QApplication.processEvents()
    window.canvas.fit_image()
    window.dirty = False
    window.update_title()
    window.error = original_error
    checks.append('compact_and_normal_window_render')
    return {'status': 'passed', 'frozen_executable': bool(getattr(sys, 'frozen', False)),
            'checks': checks, 'check_count': len(checks), 'real_device_frame': real_size,
            'real_device_display': real_display, 'actual_game_clicks_sent': 0}
