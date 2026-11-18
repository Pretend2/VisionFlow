from __future__ import annotations
import copy
import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QSize, QItemSelectionModel
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from .canvas import ScreenshotCanvas, image_to_pixmap
from .demo import demo_project
from .device import Device, DeviceConfig, app_directory, select_display
from .flow import advance, describe_rule, flat_order, hit_map, rule_clicks, walk as walk_tree
from .maa_vision import MaaVision
from .models import ACTIONS, MAX_POINTS, MODES, SHORT_MODES, TEXT_MATCHES, TRIGGERS, Project, Rule, TreeNode, collect_images, condition_ids, decode_image, encode_image, leaf_ids, prune_tree, read_image, unique_names
from .theme import STYLE
from .vision import TriggerGate, choose_rule, gate_reason, match_rules

FIELD_LABELS = {'name': '名称', 'mode': '模式', 'text': '目标文字', 'text_match': '文字匹配方式', 'text_confidence': '文字置信度', 'delay': '延迟', 'enabled': '启用', 'threshold': '匹配阈值', 'action': '动作', 'offset_x': '中心偏移 X', 'offset_y': '中心偏移 Y', 'trigger': '触发方式', 'interval': '触发间隔', 'points': '点击位置', 'points_interval': '位置间隔', 'points_repeat': '位置重复'}
SHORT_ACTIONS = {'observe': '识别', 'click': '点击', 'points': '多点', 'pause': '暂停'}
MODE_HINTS = {
    'match': '图像识别：在画面里找到模板后才执行本条，没找到就停在这一条等待。',
    'text': '文字识别：只扫描一个自定义矩形区域，找到目标文字后执行本条。',
    'always': '始终执行：不比对画面，直接执行「点击指定位置」里的坐标。',
    'delay': '延迟执行：执行到这里等待指定秒数，然后继续下一条。',
    'stop': '终止：执行到这里就回到列表第一条，本条不执行动作。',
    'mixed': '所选规则模式不一致，控件按最宽松方式开放；修改「模式」只影响当前这一条。',
}
ACTIVE_COLOR = '#1d4738'

class Worker(QThread):
    result = Signal(object)
    failed = Signal(str)
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
    def run(self):
        try:
            self.result.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))

def label(text, name=''):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class RuleTree(QTreeWidget):
    """Rule list as a tree: drag/drop reorders rules and moves them into condition branches.

    Qt 自带的 InternalMove 会把行重建一次，自定义角色（规则 id / 条件标记）在某些
    情况下会丢失，导致条件行退化成普通规则。因此这里不交 Qt 搬行，只上报落点，
    由 MainWindow 修改数据模型后重建整棵树。
    """
    dropped = Signal(object, object, object)
    RULE, NODE, BRANCH, KIND = (Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1,
                                Qt.ItemDataRole.UserRole + 2, Qt.ItemDataRole.UserRole + 3)

    def __init__(self):
        super().__init__()
        self.setObjectName('ruleTree')
        self.setColumnCount(2)
        self.setHeaderLabels(['规则 / 条件', '动作'])
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 62)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        position = self.dropIndicatorPosition()
        source = self.currentItem()
        # 拒绝默认行为（会让 Qt 自己移动/重建行），改由数据模型处理。
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.dropped.emit(item, position, source)

def panel():
    frame = QFrame()
    frame.setObjectName('panel')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    return frame, layout

class MainWindow(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle('VisionFlow · Android 视觉自动化')
        self.resize(1500, 980)
        self.setMinimumSize(1180, 760)
        self.setStyleSheet(STYLE)
        self.config = DeviceConfig.load()
        self.device = Device(self.config)
        self.project = Project()
        self.path = None
        self.dirty = False
        self.loading = False
        self.worker = None
        self.frame = None
        self.frame_source = ''
        self.matches = []
        self.anchors = {}
        self.ocr = MaaVision()
        self.frame_counter = 0
        self.mixed = {}
        self.capture_target = None
        self.cursor = None
        self.active_id = None
        self.gate = TriggerGate()
        self.monitoring = False
        self.timer = QTimer(self)
        # 持续监测不应在每轮截图完成后再空等 1.5 秒；截图线程本身会限速。
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.monitor_tick)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(33)
        self.preview_timer.timeout.connect(self.refresh_live_preview)
        self.preview_sequence = -1
        self.preview_paused = False
        self.gate.window = self.gate_window()
        self.build_ui()
        self.setup_shortcuts()
        self.replace_project(demo_project() if demo else Project())
        self.log('就绪。可加载演示方案，或连接 Android 模拟器 / 设备后获取画面。')

    def button(self, text, callback, name=''):
        item = QPushButton(text)
        if name:
            item.setObjectName(name)
        item.clicked.connect(lambda checked=False: callback())
        return item

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(18, 14, 18, 8)
        main.setSpacing(12)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.setSpacing(2)
        brand.addWidget(label('VisionFlow', 'title'))
        brand.addWidget(label('视觉自动化工作台  /  图像识别 · 文字识别 · 条件分支 · 自动点击', 'muted'))
        header.addLayout(brand)
        header.addStretch()
        self.project_title = label('未命名方案', 'badge')
        header.addWidget(self.project_title)
        self.demo_btn = self.button('体验示例', self.load_demo)
        self.new_btn = self.button('新建', self.new_project)
        self.open_btn = self.button('打开方案', self.open_project)
        self.save_btn = self.button('保存方案', self.save_project, 'primary')
        for item in (self.demo_btn, self.new_btn, self.open_btn, self.save_btn):
            header.addWidget(item)
        main.addLayout(header)
        device_panel, device_layout = panel()
        device_layout.setContentsMargins(12, 9, 12, 9)
        row = QHBoxLayout()
        row.addWidget(label('设备'))
        self.serial = QLineEdit(self.config.adb_serial)
        self.serial.setPlaceholderText('例如 127.0.0.1:16384')
        self.serial.setMaximumWidth(215)
        self.serial.setAccessibleName('ADB 设备地址')
        row.addWidget(self.serial)
        self.connect_btn = self.button('连接设备', self.connect_device)
        self.capture_btn = self.button('获取截图', self.capture)
        self.settings_btn = self.button('设备设置', self.device_settings)
        for item in (self.connect_btn, self.capture_btn, self.settings_btn):
            row.addWidget(item)
        self.device_status = label('尚未连接', 'muted')
        row.addWidget(self.device_status)
        row.addStretch()
        self.monitor_btn = self.button('开始监测', self.toggle_monitor)
        self.auto_click = QCheckBox('自动执行')
        self.auto_click.setToolTip('勾选后监测循环会真实点击优先规则；不勾选仅预览动作')
        self.reset_btn = self.button('重置触发', self.reset_gate)
        row.addWidget(self.auto_click)
        row.addWidget(self.monitor_btn)
        row.addWidget(self.reset_btn)
        device_layout.addLayout(row)
        main.addWidget(device_panel)
        self.vertical = QSplitter(Qt.Orientation.Vertical)
        self.columns = QSplitter(Qt.Orientation.Horizontal)
        left, layout = panel()
        left.setMinimumWidth(300)
        title_row = QHBoxLayout()
        title_row.addWidget(label('规则与条件', 'section'))
        title_row.addStretch()
        self.select_all = QCheckBox('全选')
        self.select_all.setTristate(True)
        self.select_all.setToolTip('Ctrl+A 全选规则；多选时右侧参数不一致显示为 —，修改后应用到所有选中规则')
        title_row.addWidget(self.select_all)
        layout.addLayout(title_row)
        hint = label('拖动排序 · 拖进「则 / 否则」生效', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.rule_count = label('0 条规则', 'muted')
        layout.addWidget(self.rule_count)
        self.rule_tree = RuleTree()
        self.rule_tree.setMinimumHeight(180)
        self.rule_tree.itemSelectionChanged.connect(self.selected_changed)
        self.rule_tree.itemChanged.connect(self.item_changed)
        self.rule_tree.dropped.connect(self.tree_dropped)
        layout.addWidget(self.rule_tree, 1)
        actions = QGridLayout()
        actions.setSpacing(6)
        self.add_btn = self.button('＋ 新增', self.add_rule)
        self.condition_btn = self.button('＋ 条件', self.add_condition)
        self.copy_btn = self.button('复制', self.copy_rule)
        self.delete_btn = self.button('删除', self.delete_rule, 'danger')
        actions.addWidget(self.add_btn, 0, 0)
        actions.addWidget(self.condition_btn, 0, 1)
        actions.addWidget(self.copy_btn, 1, 0)
        actions.addWidget(self.delete_btn, 1, 1)
        layout.addLayout(actions)
        ordering = QHBoxLayout()
        self.batch_btn = self.button('批量导入', self.batch_import_rules)
        self.up_btn = self.button('↑ 上移', lambda: self.move_rule(-1))
        self.down_btn = self.button('↓ 下移', lambda: self.move_rule(1))
        ordering.addWidget(self.batch_btn)
        ordering.addWidget(self.up_btn)
        ordering.addWidget(self.down_btn)
        layout.addLayout(ordering)
        self.columns.addWidget(left)
        center, layout = panel()
        center.setMinimumWidth(400)
        canvas_header = QHBoxLayout()
        canvas_header.addWidget(label('画面与识别', 'section'))
        canvas_header.addStretch()
        self.frame_info = label('等待截图', 'muted')
        canvas_header.addWidget(self.frame_info)
        layout.addLayout(canvas_header)
        tools = QHBoxLayout()
        self.import_frame_btn = self.button('导入画面', self.import_frame)
        self.crop_btn = self.button('截取模板', lambda: self.select_mode('template'))
        self.region_btn = self.button('搜索区域', lambda: self.select_mode('region'))
        self.point_btn = self.button('点击位置', lambda: self.select_mode('point'))
        self.fit_btn = self.button('适应', lambda: self.canvas.fit_image())
        for item in (self.import_frame_btn, self.crop_btn, self.region_btn, self.point_btn, self.fit_btn):
            tools.addWidget(item)
        layout.addLayout(tools)
        self.canvas = ScreenshotCanvas()
        self.canvas.setMinimumSize(280, 240)
        self.canvas.selection.connect(self.canvas_selected)
        self.canvas.cursor_position.connect(lambda text: self.statusBar().showMessage(text))
        self.canvas.pressed.connect(self.resume_live_preview)
        layout.addWidget(self.canvas, 1)
        self.canvas_hint = label('导入一张游戏截图，或点击「体验示例」开始配置。', 'muted')
        self.canvas_hint.setWordWrap(True)
        layout.addWidget(self.canvas_hint)
        self.decision = label('等待识别  ·  优先级决定执行顺序', 'badge')
        self.decision.setWordWrap(True)
        layout.addWidget(self.decision)
        test_row = QHBoxLayout()
        self.test_btn = self.button('测试当前画面', self.test_frame, 'primary')
        self.step_btn = self.button('单步执行优先动作', self.single_step)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.step_btn)
        layout.addLayout(test_row)
        self.columns.addWidget(center)
        right, layout = panel()
        right.setMinimumWidth(330)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.property_scroll = scroll
        self.form = QWidget()
        form_layout = QVBoxLayout(self.form)
        form_layout.setContentsMargins(0, 0, 3, 0)
        form_layout.setSpacing(10)
        self.enabled = QCheckBox('启用此规则')
        self.enabled.setTristate(True)
        self.enabled.setToolTip('多选且启用状态不一致时显示为部分选中；点击后统一应用')
        form_layout.addWidget(self.enabled)
        fields_layout = QFormLayout()
        fields_layout.setSpacing(10)
        self.rule_name = QLineEdit()
        self.rule_name.setAccessibleName('规则名称')
        self.mode = QComboBox()
        for key, text in MODES.items():
            self.mode.addItem(text, key)
        self.mode.setToolTip('图像识别：找到模板才执行；始终执行：不做比对直接执行动作；终止：回到第一条规则')
        fields_layout.addRow('名称', self.rule_name)
        fields_layout.addRow('模式', self.mode)
        form_layout.addLayout(fields_layout)
        self.mode_hint = label('图像识别：在画面里找到模板后才执行本条。', 'muted')
        self.mode_hint.setWordWrap(True)
        form_layout.addWidget(self.mode_hint)
        text_fields = QFormLayout()
        text_fields.setSpacing(8)
        self.text_target = QLineEdit()
        self.text_target.setPlaceholderText('例如：准备、胜利、继续')
        self.text_match = QComboBox()
        for key, text in TEXT_MATCHES.items():
            self.text_match.addItem(text, key)
        self.text_confidence = QDoubleSpinBox()
        self.text_confidence.setRange(0.05, 1.0)
        self.text_confidence.setDecimals(2)
        self.text_confidence.setSingleStep(0.05)
        self.text_confidence.setToolTip('MaaFramework OCR 返回的最低文字置信度')
        self.text_region_text = label('尚未设置', 'muted')
        self.text_region_text.setWordWrap(True)
        self.text_region_btn = self.button('绘制识别区域', lambda: self.select_mode('text_region'))
        self.clear_text_region_btn = self.button('清除识别区域', self.clear_text_region)
        text_region_actions = QHBoxLayout()
        text_region_actions.addWidget(self.text_region_btn)
        text_region_actions.addWidget(self.clear_text_region_btn)
        text_region_actions.addStretch()
        text_fields.addRow('目标文字', self.text_target)
        text_fields.addRow('匹配方式', self.text_match)
        text_fields.addRow('文字置信度', self.text_confidence)
        text_fields.addRow('识别区域', self.text_region_text)
        text_fields.addRow('', text_region_actions)
        form_layout.addLayout(text_fields)
        delay_row = QHBoxLayout()
        self.rule_delay = QDoubleSpinBox()
        self.rule_delay.setRange(0.05, 600)
        self.rule_delay.setDecimals(2)
        self.rule_delay.setSingleStep(0.5)
        self.rule_delay.setSuffix(' 秒')
        self.rule_delay.setToolTip('延迟执行模式下，执行到本条时等待的秒数')
        delay_row.addWidget(label('延迟'))
        delay_row.addWidget(self.rule_delay)
        delay_row.addStretch()
        form_layout.addLayout(delay_row)
        self.template_preview = label('未设置模板\n从画面截取，或导入图片', 'templatePreview')
        self.template_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.template_preview.setFixedHeight(120)
        form_layout.addWidget(self.template_preview)
        template_row = QHBoxLayout()
        self.template_import_btn = self.button('导入模板', self.import_template)
        self.template_crop_btn = self.button('从画面截取', lambda: self.select_mode('template'))
        template_row.addWidget(self.template_import_btn)
        template_row.addWidget(self.template_crop_btn)
        form_layout.addLayout(template_row)
        details = QFormLayout()
        details.setSpacing(10)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.5, 1.0)
        self.threshold.setDecimals(3)
        self.threshold.setSingleStep(0.01)
        self.threshold.setToolTip('归一化像素相似分数，不是识别概率；建议从 0.90 开始，根据测试结果调整。')
        details.addRow('匹配阈值', self.threshold)
        self.region_text = label('全画面', 'muted')
        self.region_text.setWordWrap(True)
        details.addRow('搜索区域', self.region_text)
        form_layout.addLayout(details)
        self.clear_region_btn = self.button('清除搜索区域', self.clear_region)
        form_layout.addWidget(self.clear_region_btn)
        form_layout.addWidget(label('识别后动作', 'section'))
        action_fields = QFormLayout()
        action_fields.setSpacing(10)
        self.action = QComboBox()
        self.action.setPlaceholderText('—')
        for key, text in ACTIONS.items():
            self.action.addItem(text, key)
        action_fields.addRow('动作', self.action)
        self.offset_x, self.offset_y = QSpinBox(), QSpinBox()
        for spin in (self.offset_x, self.offset_y):
            spin.setRange(-12000, 12000)
            spin.setFixedWidth(72)
        offset_row = QHBoxLayout()
        offset_row.addWidget(label('X'))
        offset_row.addWidget(self.offset_x)
        offset_row.addWidget(label('Y'))
        offset_row.addWidget(self.offset_y)
        action_fields.addRow('中心偏移', offset_row)
        self.trigger = QComboBox()
        self.trigger.setPlaceholderText('—')
        for key, text in TRIGGERS.items():
            self.trigger.addItem(text, key)
        action_fields.addRow('触发方式', self.trigger)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 3600)
        self.interval.setSuffix(' 秒')
        action_fields.addRow('触发间隔', self.interval)
        self.rule_points = QListWidget()
        self.rule_points.setFixedHeight(92)
        action_fields.addRow('点击位置', self.rule_points)
        point_row = QHBoxLayout()
        self.rule_point_x, self.rule_point_y = QSpinBox(), QSpinBox()
        for spin in (self.rule_point_x, self.rule_point_y):
            spin.setRange(0, 12000)
            spin.setFixedWidth(64)
        point_row.addWidget(label('X'))
        point_row.addWidget(self.rule_point_x)
        point_row.addWidget(label('Y'))
        point_row.addWidget(self.rule_point_y)
        self.rule_point_add = self.button('添加', self.add_rule_point)
        point_row.addWidget(self.rule_point_add)
        point_row.addStretch()
        action_fields.addRow('', point_row)
        pick_row = QHBoxLayout()
        self.rule_point_pick = self.button('画面取点', lambda: self.capture_point('rule'))
        self.rule_point_delete = self.button('删除位置', self.delete_rule_point)
        pick_row.addWidget(self.rule_point_pick)
        pick_row.addWidget(self.rule_point_delete)
        pick_row.addStretch()
        action_fields.addRow('', pick_row)
        self.rule_points_interval = QDoubleSpinBox()
        self.rule_points_interval.setRange(0, 60)
        self.rule_points_interval.setDecimals(2)
        self.rule_points_interval.setSingleStep(0.1)
        self.rule_points_interval.setSuffix(' 秒')
        self.rule_points_interval.setToolTip('多个点击位置之间的等待时间')
        action_fields.addRow('位置间隔', self.rule_points_interval)
        self.rule_points_repeat = QSpinBox()
        self.rule_points_repeat.setRange(1, 100)
        self.rule_points_repeat.setToolTip('整套位置序列重复执行的次数')
        action_fields.addRow('位置重复', self.rule_points_repeat)
        form_layout.addLayout(action_fields)
        self.rule_result = label('尚未测试', 'muted')
        self.rule_result.setWordWrap(True)
        form_layout.addWidget(self.rule_result)
        hint = label('未勾选「自动执行」时，监测只预览计划动作。\n单步执行会重新截图，并按规则树里的条件和优先级执行一个动作。', 'muted')
        hint.setWordWrap(True)
        form_layout.addWidget(hint)
        form_layout.addStretch()
        scroll.setWidget(self.form)
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 6, 0, 0)
        settings_layout.setSpacing(8)
        settings_layout.addWidget(label('修改即时生效 · 保存后可再次加载', 'muted'))
        settings_layout.addWidget(scroll, 1)
        layout.addWidget(label('规则设置', 'section'))
        layout.addWidget(settings_page, 1)
        self.columns.addWidget(right)
        self.columns.setSizes([300, 640, 340])
        self.columns.setStretchFactor(1, 1)
        self.vertical.addWidget(self.columns)
        logs, layout = panel()
        layout.setContentsMargins(12, 8, 12, 8)
        log_header = QHBoxLayout()
        log_header.addWidget(label('运行记录', 'section'))
        log_header.addWidget(label('识别分数、决策与设备状态', 'muted'))
        log_header.addStretch()
        log_header.addWidget(self.button('清空', lambda: self.logs.clear()))
        layout.addLayout(log_header)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(500)
        layout.addWidget(self.logs)
        self.vertical.addWidget(logs)
        self.vertical.setSizes([700, 140])
        self.vertical.setStretchFactor(0, 1)
        main.addWidget(self.vertical, 1)
        self.statusBar().addPermanentWidget(label('v0.1  ·  本地配置 / Windows'))
        self.statusBar().showMessage('就绪')
        for signal in (self.enabled.stateChanged, self.rule_name.editingFinished, self.text_target.editingFinished, self.text_match.currentIndexChanged, self.text_confidence.valueChanged, self.threshold.valueChanged, self.action.currentIndexChanged, self.offset_x.valueChanged, self.offset_y.valueChanged, self.trigger.currentIndexChanged, self.interval.valueChanged, self.rule_delay.valueChanged, self.rule_points_interval.valueChanged, self.rule_points_repeat.valueChanged):
            signal.connect(self.edit_rule)
        # 模式只在用户真正操作下拉时生效：程序化 setCurrentIndex（例如切换选中规则）
        # 不能触发写入，否则会把模式写到别的规则上。
        self.mode.activated.connect(self.edit_rule)
        self.select_all.clicked.connect(self.toggle_select_all)
        self.auto_click.toggled.connect(self.auto_click_changed)

    def setup_shortcuts(self):
        for key, callback in [('Ctrl+S', self.save_project), ('Ctrl+Shift+S', lambda: self.save_project(True)), ('Ctrl+O', self.open_project), ('Ctrl+N', self.new_project), ('F5', self.test_frame)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda checked=False, fn=callback: fn())
            self.addAction(action)

    def log(self, message):
        self.logs.appendPlainText(f'{datetime.now():%H:%M:%S}  {message}')

    def error(self, message):
        self.stop_monitor()
        self.log(f'失败：{message}')
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, '操作未完成', message)

    def changed(self):
        self.dirty = True
        self.matches = []
        self.gate = TriggerGate()
        self.canvas.clear_overlays()
        self.decision.setText('规则已更新 · 请重新测试画面')
        self.update_title()

    def update_title(self):
        self.project_title.setText(self.project.name + (' *' if self.dirty else ''))
        self.setWindowTitle(f'{self.project.name}{" *" if self.dirty else ""} — VisionFlow')

    def selected_rule(self):
        item = self.rule_tree.currentItem()
        if item:
            return next((rule for rule in self.project.rules if rule.id == item.data(0, RuleTree.RULE)), None)
        return None

    def tree_items(self, items=None):
        """Every item of the rule tree, depth first."""
        if items is None:
            items = [self.rule_tree.topLevelItem(index) for index in range(self.rule_tree.topLevelItemCount())]
        result = []
        for item in items:
            result.append(item)
            result.extend(self.tree_items([item.child(index) for index in range(item.childCount())]))
        return result

    def rule_items(self):
        """Items that show a rule or a condition, skipping branch labels."""
        return [item for item in self.tree_items() if item.data(0, RuleTree.RULE)]

    def selected_ids(self):
        return [item.data(0, RuleTree.RULE) for item in self.rule_items() if item.isSelected()]

    def selected_rules(self):
        mapping = {rule.id: rule for rule in self.project.rules}
        rules = [mapping[rule_id] for rule_id in self.selected_ids() if rule_id in mapping]
        if rules:
            return rules
        rule = self.selected_rule()
        return [rule] if rule else []

    def toggle_select_all(self, checked=False):
        total = len(self.project.rules)
        if total and len(set(self.selected_ids())) >= total:
            self.rule_tree.clearSelection()
        else:
            self.rule_tree.selectAll()
        self._sync_select_all()

    def _sync_select_all(self):
        total = len(self.project.rules)
        picked = len(set(self.selected_ids()))
        state = Qt.CheckState.Unchecked
        if total and picked == total:
            state = Qt.CheckState.Checked
        elif picked:
            state = Qt.CheckState.PartiallyChecked
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(state)
        self.select_all.blockSignals(False)

    def tree_nodes(self, nodes=None):
        """Every node of the project tree, depth first."""
        result = []
        for node in (self.project.tree if nodes is None else nodes):
            result.append(node)
            result.extend(self.tree_nodes(node.then))
            result.extend(self.tree_nodes(node.otherwise))
        return result

    def make_item(self, rule, index):
        item = QTreeWidgetItem([f'{index:02d}  {rule.name}', SHORT_MODES[rule.mode] if rule.mode != 'match' else SHORT_ACTIONS[rule.action]])
        item.setData(0, RuleTree.RULE, rule.id)
        item.setData(0, RuleTree.KIND, 'rule')
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        item.setCheckState(0, Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
        item.setToolTip(0, f'{rule.name}\n模式：{MODES[rule.mode]} · 动作：{ACTIONS[rule.action]} · 触发：{TRIGGERS[rule.trigger]}')
        return item

    def make_condition(self, rule):
        item = QTreeWidgetItem([f'条件：识别到「{rule.name}」', '则 / 否则'])
        item.setData(0, RuleTree.RULE, rule.id)
        item.setData(0, RuleTree.KIND, 'if')
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        item.setCheckState(0, Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
        item.setToolTip(0, f'条件：识别到「{rule.name}」时执行「则」里的规则，否则执行「否则」里的规则')
        return item

    def refresh_rules(self, selected_id=None):
        """Rebuild the rule tree, keeping the selection and the current rule."""
        previous = set(self.selected_ids())
        current = self.selected_rule()
        keep = {selected_id} if selected_id else previous
        current_id = selected_id or (current.id if current else None)
        self.loading = True
        self.rule_tree.clear()
        rules = {rule.id: rule for rule in self.project.rules}
        target = None
        counter = [0]

        def add(nodes, parent):
            nonlocal target
            for node in nodes:
                rule = rules.get(node.rule_id)
                if rule is None:
                    continue
                if node.kind == 'if':
                    item = self.make_condition(rule)
                else:
                    counter[0] += 1
                    item = self.make_item(rule, counter[0])
                if parent is None:
                    self.rule_tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                if rule.id in keep:
                    item.setSelected(True)
                if rule.id == current_id:
                    target = item
                if node.kind == 'if':
                    for key, text in (('then', '则'), ('otherwise', '否则')):
                        branch = QTreeWidgetItem([text, f'{len(getattr(node, key))} 项'])
                        branch.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
                        branch.setData(0, RuleTree.BRANCH, key)
                        item.addChild(branch)
                        add(getattr(node, key), branch)

        add(self.project.tree, None)
        # 重建后默认全部展开，否则条件分支会看起来像是被「收起来了」。
        self.rule_tree.expandAll()
        if target is None and self.rule_tree.topLevelItemCount():
            target = self.rule_tree.topLevelItem(0)
            target.setSelected(True)
        if target is not None:
            self.rule_tree.setCurrentItem(target, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.loading = False
        self.update_rule_count()
        self.selected_changed()
        self.highlight_active()

    def update_rule_count(self):
        conditions = sum(1 for node in self.tree_nodes() if node.kind == 'if')
        enabled = sum(rule.enabled for rule in self.project.rules)
        suffix = f' · {conditions} 个条件' if conditions else ''
        self.rule_count.setText(f'{len(self.project.rules)} 条规则 · {enabled} 条启用{suffix}')

    @staticmethod
    def common_value(rules, getter):
        values = [getter(rule) for rule in rules]
        if not values:
            return None, True
        first = values[0]
        if all(value == first for value in values):
            return first, False
        return None, True

    def spin_value(self, spin, value):
        if value is None:
            spin.setSpecialValueText('—')
            spin.setValue(spin.minimum())
        else:
            spin.setSpecialValueText('')
            spin.setValue(value)

    def selected_changed(self, *args):
        if self.loading:
            return
        rules = self.selected_rules()
        # 监测期间表单保持禁用，但仍会刷新显示选中规则的值，便于核对识别结果。
        if not self.monitoring:
            self.form.setEnabled(bool(rules))
            self.copy_btn.setEnabled(bool(rules))
            self.delete_btn.setEnabled(bool(rules))
        self._sync_select_all()
        if not rules:
            self.template_preview.clear()
            self.template_preview.setText('尚无规则\n点击「新增」开始')
            return
        self.loading = True
        self.load_form(rules)
        self.loading = False
        self.show_selected_overlay()

    def form_current_rule(self, rules):
        """Which of ``rules`` the mode dropdown speaks for (always one single rule)."""
        current = self.selected_rule()
        if current is not None and any(rule.id == current.id for rule in rules):
            return current
        return rules[0] if rules else None

    def load_form(self, rules):
        """Fill the property panel; values that differ across the selection show 「—」."""
        self.mixed = {}
        enabled, mixed = self.common_value(rules, lambda rule: rule.enabled)
        self.mixed['enabled'] = mixed
        self.enabled.setCheckState(Qt.CheckState.PartiallyChecked if mixed else (Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked))
        self.enabled.setText('启用此规则' + (f'（已选 {len(rules)} 条）' if len(rules) > 1 else ''))
        # 模式只作用于当前规则，所以下拉始终显示当前规则的模式。
        current = self.form_current_rule(rules)
        current_mode = current.mode if current is not None else 'match'
        self.mode.setCurrentIndex(max(0, self.mode.findData(current_mode)))
        name, mixed = self.common_value(rules, lambda rule: rule.name)
        self.mixed['name'] = mixed
        self.rule_name.setPlaceholderText('—' if mixed else '规则名称')
        self.rule_name.setText('' if mixed else name)
        threshold, mixed = self.common_value(rules, lambda rule: rule.threshold)
        self.mixed['threshold'] = mixed
        self.spin_value(self.threshold, None if mixed else threshold)
        delay, mixed = self.common_value(rules, lambda rule: rule.delay)
        self.mixed['delay'] = mixed
        self.spin_value(self.rule_delay, None if mixed else delay)
        text, mixed = self.common_value(rules, lambda rule: rule.text)
        self.mixed['text'] = mixed
        self.text_target.setPlaceholderText('—' if mixed else '例如：准备、胜利、继续')
        self.text_target.setText('' if mixed else text)
        text_match, mixed = self.common_value(rules, lambda rule: rule.text_match)
        self.mixed['text_match'] = mixed
        self.text_match.setCurrentIndex(-1 if mixed else max(0, self.text_match.findData(text_match)))
        confidence, mixed = self.common_value(rules, lambda rule: rule.text_confidence)
        self.mixed['text_confidence'] = mixed
        self.spin_value(self.text_confidence, None if mixed else confidence)
        text_region, mixed = self.common_value(rules, lambda rule: rule.text_region)
        self.mixed['text_region'] = mixed
        if mixed:
            self.text_region_text.setText('多个识别区域')
        elif text_region:
            x, y, w, h = text_region
            self.text_region_text.setText(f'X {x} · Y {y} · {w} × {h}')
        else:
            self.text_region_text.setText('尚未设置（文字识别必须设置一个区域）')
        for key, spin, index in (('offset_x', self.offset_x, 0), ('offset_y', self.offset_y, 1)):
            value, mixed = self.common_value(rules, lambda rule, i=index: rule.offset[i])
            self.mixed[key] = mixed
            self.spin_value(spin, None if mixed else value)
        for key, combo, getter in (('action', self.action, lambda rule: rule.action), ('trigger', self.trigger, lambda rule: rule.trigger)):
            value, mixed = self.common_value(rules, getter)
            self.mixed[key] = mixed
            combo.setCurrentIndex(-1 if mixed else max(0, combo.findData(value)))
        for key, spin, getter in (('interval', self.interval, lambda rule: rule.interval), ('points_interval', self.rule_points_interval, lambda rule: rule.points_interval), ('points_repeat', self.rule_points_repeat, lambda rule: rule.points_repeat)):
            value, mixed = self.common_value(rules, getter)
            self.mixed[key] = mixed
            self.spin_value(spin, None if mixed else value)
        self.refresh_template_preview(rules)
        region, mixed = self.common_value(rules, lambda rule: rule.region)
        self.mixed['region'] = mixed
        if mixed:
            self.region_text.setText('多个搜索区域')
        elif not region:
            self.region_text.setText('全画面')
        else:
            x, y, w, h = region
            self.region_text.setText(f'X {x} · Y {y} · {w} × {h}')
        self.refresh_rule_points(rules)
        self.apply_mode_state(rules)

    def refresh_template_preview(self, rules):
        if not rules:
            return
        templates = [rule.template for rule in rules]
        if len(rules) > 1:
            same = all(template == templates[0] for template in templates)
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText('所选规则使用同一模板' if same else f'多个模板（已选 {len(rules)} 条）')
            return
        if not templates[0]:
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText('未设置模板\n从画面截取，或导入图片')
            return
        image = decode_image(templates[0])
        width = max(self.template_preview.width(), 120)
        pixmap = image_to_pixmap(image).scaled(width, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.template_preview.setText('')
        self.template_preview.setPixmap(pixmap)

    def edit_rule(self, *args):
        """Apply the edited fields to every selected rule; 「—」fields are left alone."""
        if self.loading:
            return
        rules = self.selected_rules()
        if not rules:
            return
        values = {}
        # 模式属于单条规则的行为，不做多选批量，避免误伤其它规则。
        mode = self.mode.currentData()
        target = self.selected_rule()
        if mode is not None and target is not None and target.mode != mode:
            self.loading = True
            target.mode = mode
            if target.mode == 'stop':
                target.action = 'observe'
            elif target.mode == 'always' and target.action in ('click', 'observe'):
                target.action = 'points'
            self.loading = False
            self.changed()
            self.refresh_tree_labels()
            self.update_rule_count()
            self.log(f'「{target.name}」模式已改为「{MODES[mode]}」')
            self.apply_mode_state()
            return
        if self.enabled.checkState() != Qt.CheckState.PartiallyChecked:
            values['enabled'] = self.enabled.isChecked()
        if self.rule_name.text().strip():
            values['name'] = self.rule_name.text().strip()
        if self.text_target.text().strip() and not self.mixed.get('text'):
            values['text'] = self.text_target.text().strip()
        if self.text_match.currentData() is not None:
            values['text_match'] = self.text_match.currentData()
        if self.action.currentData() is not None:
            values['action'] = self.action.currentData()
        if self.trigger.currentData() is not None:
            values['trigger'] = self.trigger.currentData()
        for key, spin in (('threshold', self.threshold), ('offset_x', self.offset_x), ('offset_y', self.offset_y), ('interval', self.interval), ('delay', self.rule_delay), ('text_confidence', self.text_confidence), ('points_interval', self.rule_points_interval), ('points_repeat', self.rule_points_repeat)):
            if not self.mixed.get(key) or spin.value() != spin.minimum():
                values[key] = spin.value()
        if not values:
            return
        # Drop fields that would not actually change anything, so the log and the
        # mixed markers only follow real edits.
        current = {}
        for key in values:
            if key in ('offset_x', 'offset_y'):
                index = 0 if key == 'offset_x' else 1
                current[key] = [rule.offset[index] for rule in rules]
            else:
                current[key] = [getattr(rule, key) for rule in rules]
        values = {key: value for key, value in values.items() if any(existing != value for existing in current[key])}
        if not values:
            return
        self.loading = True
        for rule in rules:
            if 'offset_x' in values or 'offset_y' in values:
                rule.offset = [values.get('offset_x', rule.offset[0]), values.get('offset_y', rule.offset[1])]
            for key in ('name', 'enabled', 'threshold', 'text', 'text_match', 'text_confidence', 'action', 'trigger', 'interval', 'delay', 'points_interval', 'points_repeat'):
                if key in values:
                    setattr(rule, key, values[key])
            if rule.mode == 'stop':
                rule.action = 'observe'
            elif rule.mode == 'always' and rule.action in ('click', 'observe'):
                # 没有匹配中心，只能用绝对坐标。
                rule.action = 'points'
        spinners = {'threshold': self.threshold, 'offset_x': self.offset_x, 'offset_y': self.offset_y, 'interval': self.interval, 'delay': self.rule_delay, 'text_confidence': self.text_confidence, 'points_interval': self.rule_points_interval, 'points_repeat': self.rule_points_repeat}
        for key in values:
            self.mixed[key] = False
            if key in spinners:
                spinners[key].setSpecialValueText('')
        self.loading = False
        self.changed()
        self.refresh_tree_labels()
        self.update_rule_count()
        self.refresh_template_preview(rules)
        self.refresh_rule_points(rules)
        self.apply_mode_state(rules)
        if len(values) == 1:
            self.log(f'已修改 {FIELD_LABELS.get(next(iter(values)), next(iter(values)))}：应用到 {len(rules)} 条规则')
        else:
            self.log(f'已修改 {len(values)} 项参数：应用到 {len(rules)} 条规则')

    def refresh_tree_labels(self):
        """Update row labels, numbering and check states without rebuilding the tree."""
        rules = {rule.id: rule for rule in self.project.rules}
        counter = 0
        self.loading = True
        for item in self.rule_items():
            rule = rules.get(item.data(0, RuleTree.RULE))
            if rule is None:
                continue
            item.setCheckState(0, Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            if item.data(0, RuleTree.KIND) == 'if':
                item.setText(0, f'条件：识别到「{rule.name}」')
                item.setToolTip(0, f'条件：识别到「{rule.name}」时执行「则」里的规则，否则执行「否则」里的规则')
            else:
                counter += 1
                item.setText(0, f'{counter:02d}  {rule.name}')
                item.setText(1, SHORT_MODES[rule.mode] if rule.mode != 'match' else SHORT_ACTIONS[rule.action])
                item.setToolTip(0, f'{rule.name}\n模式：{MODES[rule.mode]} · 动作：{ACTIONS[rule.action]} · 触发：{TRIGGERS[rule.trigger]}')
        self.loading = False
        self.highlight_active()

    def apply_mode_state(self, rules=None):
        """Only the fields the current mode actually uses stay editable."""
        rules = self.selected_rules() if rules is None else rules
        modes = {rule.mode for rule in rules}
        mode = next(iter(modes)) if len(modes) == 1 else 'mixed'
        current = self.form_current_rule(rules)
        hint = current.mode if current is not None else 'match'
        self.mode_hint.setText(MODE_HINTS.get(hint, MODE_HINTS['mixed']))
        needs_template = mode in ('match', 'mixed')
        needs_text = mode in ('text', 'mixed')
        runs_action = mode in ('match', 'text', 'always', 'mixed')
        for widget in (self.threshold, self.template_import_btn, self.template_crop_btn, self.clear_region_btn, self.region_text):
            widget.setEnabled(needs_template)
        for widget in (self.text_target, self.text_match, self.text_confidence, self.text_region_text, self.text_region_btn, self.clear_text_region_btn):
            widget.setEnabled(needs_text)
        for widget in (self.action, self.trigger, self.interval, self.offset_x, self.offset_y, self.rule_points_interval, self.rule_points_repeat):
            widget.setEnabled(runs_action)
        self.rule_delay.setEnabled(mode in ('delay', 'mixed'))
        self.refresh_rule_points(rules)
        points_editable = runs_action and (mode in ('always', 'mixed') or self.action.currentData() in (None, 'points'))
        for widget in (self.rule_points, self.rule_point_x, self.rule_point_y, self.rule_point_add, self.rule_point_pick, self.rule_point_delete):
            widget.setEnabled(points_editable)
        # 「始终执行」只能用绝对坐标或暂停，「终止」行不改动作。
        allowed = {'always': ('points', 'pause'), 'stop': ()}.get(mode)
        model = self.action.model()
        for index in range(self.action.count()):
            entry = model.item(index) if hasattr(model, 'item') else None
            if entry is not None:
                entry.setEnabled(allowed is None or self.action.itemData(index) in allowed)
        if mode == 'always':
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText('不需要模板\n始终执行不比对画面')
        elif mode == 'delay':
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText(f'不需要模板\n延迟 {self.rule_delay.value():.2f} 秒后继续')
        elif mode == 'text':
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText('不需要图像模板\n只识别自定义文字区域')
        elif mode == 'stop':
            self.template_preview.setPixmap(QPixmap())
            self.template_preview.setText('不需要模板\n终止行不执行动作')

    def highlight_active(self, rule_id=None):
        """Highlight the row the monitor loop is currently waiting on or just ran."""
        scroll = rule_id is not None
        if rule_id is not None:
            self.active_id = rule_id
        rules = {rule.id: rule for rule in self.project.rules}
        for item in self.rule_items():
            branch = item.data(0, RuleTree.BRANCH)
            kind = item.data(0, RuleTree.KIND)
            active = branch is None and item.data(0, RuleTree.RULE) == self.active_id
            item.setBackground(0, QBrush(QColor(ACTIVE_COLOR)) if active else QBrush())
            item.setForeground(0, QBrush(QColor('#c4ffdf')) if active else QBrush())
            # 选中行的背景会被系统选中色盖掉，所以再加粗并加「▶」标记，保证看得见。
            font = item.font(0)
            font.setBold(active)
            item.setFont(0, font)
            if kind == 'rule':
                rule = rules.get(item.data(0, RuleTree.RULE))
                if rule is not None:
                    label = SHORT_MODES[rule.mode] if rule.mode != 'match' else SHORT_ACTIONS[rule.action]
                    item.setText(1, f'▶ {label}' if active else label)
            elif kind == 'if':
                item.setText(1, '▶ 则 / 否则' if active else '则 / 否则')
            if active and scroll:
                self.rule_tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def show_selected_overlay(self):
        rule = self.selected_rule()
        self.canvas.clear_overlays()
        if not rule:
            return
        if rule.region:
            self.canvas.draw_box(rule.region, '#75a9fa', '搜索区域')
        if rule.text_region:
            self.canvas.draw_box(rule.text_region, '#d6a85e', '文字识别区域')
        match = next((match for match in self.matches if match.rule_id == rule.id), None)
        if match is None:
            self.rule_result.setText('尚未测试当前规则')
        else:
            self.rule_result.setText(f'{"命中" if match.hit else "未命中"} · 分数 {match.score:.3f}\n{match.text + " · " if match.text else ""}{match.reason}')
            if match.rect:
                self.canvas.draw_box(match.rect, '#54d6b3' if match.hit else '#ffb57b', match.text or f'{match.score:.3f}')
                if match.hit and rule.action == 'click':
                    x, y, w, h = match.rect
                    self.canvas.draw_point(x+w//2+rule.offset[0], y+h//2+rule.offset[1])
        if rule.action == 'points':
            for index, point in enumerate(rule.points, 1):
                self.canvas.draw_box([point[0]-18, point[1]-18, 36, 36], '#ffcc7a', f'{index}')
                self.canvas.draw_point(point[0], point[1])

    def item_changed(self, item):
        if self.loading or item.data(0, RuleTree.BRANCH):
            return
        rule = next((r for r in self.project.rules if r.id == item.data(0, RuleTree.RULE)), None)
        if rule is None:
            return
        if self.monitoring:
            # 监测期间规则树只供查看和选中，不允许改启用状态；把勾选复原。
            self.loading = True
            item.setCheckState(0, Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
            self.loading = False
            self.log('监测进行中，暂停监测后才能修改规则')
            return
        state = item.checkState(0) == Qt.CheckState.Checked
        if rule.enabled == state:
            return
        rule.enabled = state
        self.changed()
        self.update_rule_count()
        self.selected_changed()

    def tree_to_nodes(self, items=None) -> list:
        """Read the widget rows back into rule-tree nodes."""
        items = items if items is not None else [self.rule_tree.topLevelItem(index) for index in range(self.rule_tree.topLevelItemCount())]
        nodes = []
        for item in items:
            if item.data(0, RuleTree.BRANCH):
                continue
            rule_id = item.data(0, RuleTree.RULE)
            if not rule_id:
                continue
            if item.data(0, RuleTree.KIND) == 'if':
                node = TreeNode.branch(rule_id)
                for index in range(item.childCount()):
                    branch = item.child(index)
                    target = node.then if branch.data(0, RuleTree.BRANCH) == 'then' else node.otherwise
                    target.extend(self.tree_to_nodes([branch.child(i) for i in range(branch.childCount())]))
                nodes.append(node)
            else:
                nodes.append(TreeNode.leaf(rule_id))
        return nodes

    def node_for(self, rule_id, kind=None):
        """Return the TreeNode carrying ``rule_id`` (anywhere in the tree)."""
        for node in self.tree_nodes():
            if node.rule_id == rule_id and (kind is None or node.kind == kind):
                return node
        return None

    def locate(self, rule_id):
        """Return ``(container_list, index)`` holding the row for ``rule_id``."""
        for container in self.containers():
            for index, node in enumerate(container):
                if node.rule_id == rule_id:
                    return container, index
        return None, -1

    def subtree_containers(self, node):
        """Every list nested inside ``node`` (used to refuse self-nesting drops)."""
        found = []

        def walk(current):
            found.extend([current.then, current.otherwise])
            for child in current.then + current.otherwise:
                if child.kind == 'if':
                    walk(child)

        if node is not None and node.kind == 'if':
            walk(node)
        return found

    def tree_dropped(self, item=None, position=None, source_item=None):
        """Apply a drag/drop by editing the tree model, then rebuild the widget."""
        if self.loading:
            return
        # 被拖的行：条件行（if）不搬，避免整个分支和规则混在一起。
        rule_id = source_item.data(0, RuleTree.RULE) if source_item is not None else None
        source_kind = (source_item.data(0, RuleTree.KIND) if source_item is not None else None) or 'rule'
        if source_item is not None and source_item.data(0, RuleTree.BRANCH):
            return
        if source_item is not None and source_kind == 'if':
            self.log('条件行不能拖动，请拖动「则 / 否则」里的规则行')
            return
        source = next((rule for rule in self.project.rules if rule.id == rule_id), None) or self.selected_rule()
        if source is None:
            return
        on_item = getattr(QAbstractItemView.DropIndicatorPosition, 'OnItem', None)
        below = getattr(QAbstractItemView.DropIndicatorPosition, 'BelowItem', None)
        if item is None or position == QAbstractItemView.DropIndicatorPosition.OnViewport:
            container, index = self.project.tree, len(self.project.tree)
            hint = '末尾'
        else:
            branch = item.data(0, RuleTree.BRANCH)
            kind = item.data(0, RuleTree.KIND)
            target_id = item.data(0, RuleTree.RULE)
            if branch:
                # 「则 / 否则」行：插到该分支末尾。
                owner = self.node_for(item.parent().data(0, RuleTree.RULE), 'if') if item.parent() else None
                container = (owner.then if branch == 'then' else owner.otherwise) if owner else self.project.tree
                index = len(container)
                hint = '分支末尾'
            elif kind == 'if' and position == on_item:
                # 直接拖到条件行上：归入它的「则」分支，不要丢掉。
                owner = self.node_for(target_id, 'if')
                container = owner.then if owner else self.project.tree
                index = len(container)
                hint = '「则」分支'
            else:
                container, found = self.locate(target_id, 'rule')
                if container is None:
                    # 目标是条件行但不是 OnItem（如拖到它上方/下方），按顶层位置处理。
                    container, found = self.locate(target_id, 'if')
                if container is None:
                    return
                index = found + (0 if position == QAbstractItemView.DropIndicatorPosition.AboveItem else 1)
                hint = '上方' if index == found else '下方'
        source_node = self.node_for(source.id, 'rule')
        if id(container) in {id(child) for child in self.subtree_containers(source_node)}:
            self.log('不能把条件分支拖进它自己的分支')
            self.refresh_rules(source.id)
            return
        holder, holder_index = self.locate(source.id, 'rule')
        if holder is None:
            self.log(f'「{source.name}」不在列表里，无法移动')
            self.refresh_rules(source.id)
            return
        holder.pop(holder_index)
        if holder is container and holder_index < index:
            index -= 1
        container.insert(max(0, min(index, len(container))), TreeNode.leaf(source.id))
        self.sync_rule_order()
        self.changed()
        self.log(f'「{source.name}」已移动到{hint}')
        self.refresh_rules(source.id)

    def sync_rule_order(self):
        """Keep project.rules aligned with the list order so priority matches the tree."""
        mapping = {rule.id: rule for rule in self.project.rules}
        order = [rule_id for rule_id in leaf_ids(self.project.tree) if rule_id in mapping]
        seen = set(order)
        self.project.rules = [mapping[rule_id] for rule_id in order] + [rule for rule in self.project.rules if rule.id not in seen]

    def containers(self):
        """Every node list in the tree, so entries can be located and moved."""
        result = [self.project.tree]
        for node in self.tree_nodes():
            if node.kind == 'if':
                result.extend([node.then, node.otherwise])
        return result

    def locate(self, rule_id, kind='rule'):
        """Return ``(container_list, index)`` holding the row for ``rule_id``."""
        for container in self.containers():
            for index, node in enumerate(container):
                if node.rule_id == rule_id and node.kind == kind:
                    return container, index
        return None, -1

    def move_rule(self, direction):
        rule = self.selected_rule()
        if rule is None:
            return
        container, index = self.locate(rule.id)
        if container is None:
            return
        target = index + direction
        if not 0 <= target < len(container):
            return
        container.insert(target, container.pop(index))
        self.sync_rule_order()
        self.changed()
        self.refresh_rules(rule.id)

    def add_rule(self):
        if len(self.project.rules) >= 200:
            self.error('最多支持 200 条规则')
            return
        rule = Rule(name=f'新规则 {len(self.project.rules)+1}')
        self.project.rules.append(rule)
        current = self.selected_rule()
        container, index = self.locate(current.id) if current else (None, -1)
        if container is None:
            self.project.tree.append(TreeNode.leaf(rule.id))
        else:
            container.insert(index + 1, TreeNode.leaf(rule.id))
        self.sync_rule_order()
        self.changed()
        self.refresh_rules(rule.id)

    def add_condition(self):
        if not self.project.rules:
            self.error('请先添加至少一条规则，再用它作为条件')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('添加条件分支')
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)
        hint = label('识别到所选规则时执行「则」里的规则，否则执行「否则」里的规则。\n条件行本身也是一步：命中时先执行它自己的动作，然后进入「则」；未命中则直接进入「否则」。\n把规则拖进「则 / 否则」即可分组。', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        picker = QComboBox()
        for rule in self.project.rules:
            picker.addItem(f'{rule.name}  ·  {ACTIONS[rule.action]}', rule.id)
        current = self.selected_rule()
        if current:
            picker.setCurrentIndex(max(picker.findData(current.id), 0))
        layout.addWidget(picker)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('添加')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not picker.currentData():
            return
        rule = next(r for r in self.project.rules if r.id == picker.currentData())
        node = TreeNode.branch(rule.id)
        container, index = self.locate(rule.id, 'rule')
        if container is None:
            self.project.tree.append(node)
        else:
            # 原地把这条规则换成条件行：同一规则不能又当普通行又当条件行，
            # 否则保存时会被「只保留第一处」的校验把分支丢掉。
            container[index] = node
        # 同一规则同一分支里不能既当条件又当叶子：把分支里可能残留的旧叶子清掉。
        for child in self.containers():
            for offset in range(len(child) - 1, -1, -1):
                if child[offset] is not node and child[offset].rule_id == rule.id and child[offset].kind == 'rule':
                    child.pop(offset)
                    self.log(f'「{rule.name}」已成为条件行，已从原位置移除')
        self.changed()
        self.refresh_rules(rule.id)
        self.log(f'已添加条件：识别到「{rule.name}」→ 它是条件行，命中后进入「则」；把规则拖进「则 / 否则」即可分组')

    def batch_import_rules(self):
        if self.worker:
            return
        folder = QFileDialog.getExistingDirectory(self, '选择模板文件夹', str(Path.home()))
        if not folder:
            return
        remaining = 200 - len(self.project.rules)
        if remaining <= 0:
            self.error('最多支持 200 条规则，请先删除部分规则')
            return

        def operation():
            paths = collect_images(Path(folder))
            if not paths:
                raise ValueError('该文件夹内没有可导入的图片（支持 PNG / JPG / JPEG / BMP / WEBP）')
            images, skipped = [], []
            for path in paths:
                if len(images) >= remaining:
                    skipped.append(path.name)
                    continue
                try:
                    images.append((path, read_image(str(path))))
                except Exception:
                    skipped.append(path.name)
            if not images:
                raise ValueError('图片读取失败，请检查图片格式或文件权限')
            names = unique_names([p.stem.strip() or p.name for p, _ in images], [p.parent.name for p, _ in images])
            return [(name, encode_image(image)) for name, (_, image) in zip(names, images)], skipped

        def done(result):
            templates, skipped = result
            first = None
            for name, template in templates:
                rule = Rule(name=name, template=template)
                self.project.rules.append(rule)
                self.project.tree.append(TreeNode.leaf(rule.id))
                first = first or rule.id
            self.sync_rule_order()
            self.changed()
            self.refresh_rules(first)
            self.log(f'批量导入 {len(templates)} 条规则：动作「{ACTIONS["click"]}」· 阈值 0.90')
            if skipped:
                more = '—' if len(skipped) > 5 else ''
                self.log(f'已跳过 {len(skipped)} 个条目：' + '、'.join(skipped[:5]) + more)
            self.log('提示：批量导入的规则按文件名排序，可用上移 / 下移调整优先级。')
            self.statusBar().showMessage(f'已导入 {len(templates)} 条规则')
        self.start_work('批量导入规则…', operation, done)

    def copy_rule(self):
        original = self.selected_rule()
        if original and len(self.project.rules) < 200:
            values = asdict(original)
            values.pop('id')
            values['name'] += ' · 副本'
            rule = Rule(**values)
            index = self.project.rules.index(original)
            self.project.rules.insert(index + 1, rule)
            self.insert_node_after(original.id, TreeNode.leaf(rule.id))
            self.sync_rule_order()
            self.changed()
            self.refresh_rules(rule.id)

    def insert_node_after(self, rule_id, node):
        """Insert a rule-tree entry right after the entry that shows ``rule_id``."""
        def walk(nodes):
            for index, item in enumerate(nodes):
                if item.rule_id == rule_id:
                    nodes.insert(index + 1, node)
                    return True
                if walk(item.then) or walk(item.otherwise):
                    return True
            return False

        if not walk(self.project.tree):
            self.project.tree.append(node)

    def delete_rule(self):
        rules = self.selected_rules()
        if not rules:
            return
        names = [rule.name for rule in rules]
        ids = {rule.id for rule in rules}
        for rule in rules:
            self.project.rules.remove(rule)
            self.anchors.pop(rule.id, None)
        removed = self.remove_tree_entries(ids)
        self.sync_rule_order()
        self.changed()
        self.refresh_rules()
        if removed:
            self.log(f'已同时移除 {removed} 个引用这些规则的条件 / 条目')
        self.log(f'已删除 {len(rules)} 条规则：' + '、'.join(names[:5]) + ('—' if len(names) > 5 else ''))

    def remove_tree_entries(self, rule_ids):
        """Drop rows of deleted rules; conditions that used them disappear too."""
        self.project.tree, removed = prune_tree(self.project.tree, set(rule_ids))
        return removed

    def check_unsaved(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, '保存修改', '当前方案有未保存的修改，是否保存？', QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self.save_project() if answer == QMessageBox.StandardButton.Save else True

    def replace_project(self, project, path=None):
        self.stop_monitor()
        self.project = project
        self.path = path
        self.dirty = False
        self.matches = []
        self.anchors.clear()
        self.gate = TriggerGate()
        self.cursor = None
        self.active_id = None
        self.frame = None
        self.frame_source = ''
        self.canvas.clear_image()
        self.frame_info.setText('等待截图')
        self.canvas_hint.setText('导入游戏截图，或点击「体验示例」开始配置。')
        if project.reference:
            self.set_frame(decode_image(project.reference), '方案参考画面')
        self.refresh_rules()
        self.update_title()
        self.decision.setText('等待识别 · 按列表顺序逐条执行')

    def load_demo(self):
        if not self.worker and self.check_unsaved():
            self.replace_project(demo_project())
            self.log('已加载示例方案。点击「测试当前画面」体验优先级识别。')

    def new_project(self):
        if not self.worker and self.check_unsaved():
            self.replace_project(Project())
            self.log('已创建空白方案')

    def open_project(self):
        if self.worker or not self.check_unsaved():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '打开方案', '', 'VisionFlow 方案 (*.vision.json *.mumu.json *.json)')
        if filename:
            try:
                self.replace_project(Project.load(Path(filename)), Path(filename))
                self.log(f'已打开 {filename}')
            except Exception as exc:
                self.error(f'无法打开方案：{exc}')

    def save_project(self, save_as=False):
        if self.worker:
            return False
        path = self.path
        if save_as or not path:
            filename, _ = QFileDialog.getSaveFileName(self, '保存方案', str(path or Path.home() / 'Documents' / '我的方案.vision.json'), 'VisionFlow 方案 (*.vision.json)')
            if not filename:
                return False
            path = Path(filename)
            if not path.name.endswith('.json'):
                path = path.with_name(path.name + '.vision.json')
        try:
            if self.frame is not None and self.project.resolution == [self.frame.shape[1], self.frame.shape[0]]:
                self.project.reference = encode_image(self.frame)
            self.project.save(path)
            self.path = path
            self.dirty = False
            self.update_title()
            self.log(f'已保存方案与图片素材：{path}')
            return True
        except Exception as exc:
            self.error(f'保存失败：{exc}')
            return False

    def set_frame(self, image, source):
        self.frame = image
        self.frame_counter += 1
        self.frame_source = source
        self.matches = []
        self.anchors.clear()
        self.canvas.set_image(image)
        self.frame_info.setText(f'{image.shape[1]} × {image.shape[0]}  ·  {source}')
        if not self.project.resolution:
            self.project.resolution = [image.shape[1], image.shape[0]]
            self.project.reference = encode_image(image)
            self.changed()
        self.canvas_hint.setText('滚轮缩放 · 拖动画面 · 从画面截取模板后可测试识别')
        self.show_selected_overlay()

    def import_frame(self):
        filename, _ = QFileDialog.getOpenFileName(self, '导入画面', '', '图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if filename:
            try:
                self.pause_live_preview()
                self.set_frame(read_image(filename), '本地图片')
                self.changed()
                self.log('已导入画面')
            except Exception as exc:
                self.error(str(exc))

    def select_mode(self, mode):
        if self.frame is None:
            self.error('请先获取截图或导入画面')
            return
        if not self.selected_rule():
            self.error('请先新增或选择一条规则')
            return
        self.canvas.set_mode(mode)
        hints = {'template': '在画面上按住鼠标左键拖动，截取模板图片。', 'region': '在画面上拖动，限定当前规则的搜索范围。', 'text_region': '在画面上拖动，只框选一个文字识别区域。', 'point': '点击目标位置。请先截取模板或测试识别，以确定相对偏移。'}
        self.canvas_hint.setText(hints[mode])

    def canvas_selected(self, mode, value):
        if self.frame is None:
            return
        if mode == 'point' and self.capture_target:
            target, self.capture_target = self.capture_target, None
            if target == 'rule':
                self.add_rule_point([value[0], value[1]])
            self.canvas_hint.setText('点击位置已加入列表，可继续添加或点「测试当前画面」。')
            return
        rule = self.selected_rule()
        rules = self.selected_rules()
        if not rule or not rules:
            return
        if mode == 'template':
            x, y, w, h = value
            rule.template = encode_image(self.frame[y:y+h, x:x+w])
            self.anchors[rule.id] = value
            rule.offset = [0, 0]
            if len(rules) > 1:
                self.log(f'「{rule.name}」模板已截取：{w} × {h}（模板只应用到当前规则）')
            else:
                self.log(f'「{rule.name}」模板已截取：{w} × {h}')
        elif mode == 'region':
            for target in rules:
                target.region = list(value)
            suffix = f'（{len(rules)} 条规则）' if len(rules) > 1 else ''
            self.log(f'搜索区域已更新{suffix}')
        elif mode == 'text_region':
            for target in rules:
                target.text_region = list(value)
            suffix = f'（{len(rules)} 条规则）' if len(rules) > 1 else ''
            self.log(f'文字识别区域已更新{suffix}')
        elif mode == 'point':
            base = next((m.rect for m in self.matches if m.rule_id == rule.id and m.hit), None) or self.anchors.get(rule.id)
            if not base:
                self.error('请先截取模板或测试识别，再标记点击位置')
                return
            for target in rules:
                match = next((m for m in self.matches if m.rule_id == target.id and m.hit), None)
                rect = match.rect if match else (self.anchors.get(target.id) or base)
                x, y, w, h = rect
                target.offset = [value[0] - (x+w//2), value[1] - (y+h//2)]
                target.action = 'click'
            suffix = f'（{len(rules)} 条规则）' if len(rules) > 1 else ''
            self.log(f'点击偏移已设为 X {rule.offset[0]} / Y {rule.offset[1]}{suffix}')
        self.changed()
        self.selected_changed()
        self.canvas_hint.setText('设置已更新。点击「测试当前画面」查看效果。')

    def import_template(self):
        rule = self.selected_rule()
        if not rule:
            return
        filename, _ = QFileDialog.getOpenFileName(self, '导入模板', '', '图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if filename:
            try:
                rule.template = encode_image(read_image(filename))
                self.anchors.pop(rule.id, None)
                self.changed()
                self.selected_changed()
            except Exception as exc:
                self.error(str(exc))

    def clear_region(self):
        rules = self.selected_rules()
        if rules:
            for rule in rules:
                rule.region = None
            self.changed()
            self.selected_changed()
            self.log(f'已清除 {len(rules)} 条规则的搜索区域' if len(rules) > 1 else '已清除搜索区域')

    def clear_text_region(self):
        rules = self.selected_rules()
        if rules:
            for rule in rules:
                rule.text_region = None
            self.changed()
            self.selected_changed()
            self.log(f'已清除 {len(rules)} 条规则的文字识别区域' if len(rules) > 1 else '已清除文字识别区域')

    def start_work(self, title, operation, on_result):
        if self.worker:
            return False
        self.worker = Worker(operation, self)
        self.worker.result.connect(on_result)
        self.worker.failed.connect(self.error)
        self.worker.finished.connect(self.work_finished)
        self.set_busy(True)
        # 监测时状态栏保持同一句提示，避免每轮文字跳动。
        self.statusBar().showMessage('监测中…' if self.monitoring else title)
        self.worker.start()
        return True

    def edit_controls(self):
        """Background task and monitoring both lock these editor controls."""
        return (self.new_btn, self.open_btn, self.save_btn, self.demo_btn, self.connect_btn, self.capture_btn, self.settings_btn, self.import_frame_btn, self.test_btn, self.step_btn, self.form, self.serial, self.add_btn, self.condition_btn, self.batch_btn, self.copy_btn, self.delete_btn, self.crop_btn, self.region_btn, self.point_btn, self.reset_btn, self.up_btn, self.down_btn, self.select_all, self.canvas)

    def set_busy(self, busy):
        """Lock the editor while a background task runs.

        监测期间每 0.1 秒都会起一个后台识别任务，若每轮都先禁用再启用，控件会
        不停闪烁。这里在监测状态下保持同一种外观，只在开始/停止监测时切换一次。
        """
        if self.monitoring:
            return
        for control in self.edit_controls():
            control.setEnabled(not busy)
        self.rule_tree.setEnabled(not busy)
        if not busy:
            rules = self.selected_rules()
            self.form.setEnabled(bool(rules))
            self.copy_btn.setEnabled(bool(rules))
            self.delete_btn.setEnabled(bool(rules))
            self.apply_mode_state(rules)

    def lock_editing(self, locked):
        """One-shot editor lock used when the monitoring loop starts or stops.

        规则树保持可点击：监测时仍能选中规则，观察它的分数、高亮和动作状态。
        """
        for control in self.edit_controls():
            control.setEnabled(not locked)
        self.rule_tree.setEnabled(True)
        self.rule_tree.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop if locked
                                       else QAbstractItemView.DragDropMode.InternalMove)
        if not locked:
            rules = self.selected_rules()
            self.form.setEnabled(bool(rules))
            self.copy_btn.setEnabled(bool(rules))
            self.delete_btn.setEnabled(bool(rules))
            self.apply_mode_state(rules)

    def work_finished(self):
        worker = self.worker
        self.worker = None
        if worker:
            worker.deleteLater()
        self.set_busy(False)
        if not self.monitoring:
            # 监测被用户中途暂停时，这里把一次性锁定（含拖拽模式）恢复回来。
            self.lock_editing(False)
        self.statusBar().showMessage('监测中…' if self.monitoring else '就绪')

    def sync_device(self):
        self.config.adb_serial = self.serial.text().strip()
        return Device(copy.deepcopy(self.config))

    def pause_live_preview(self):
        self.preview_paused = True
        self.canvas_hint.setText('实时画面已暂停，方便查看识别结果 · 在画面上点一下即可恢复实时更新')

    def resume_live_preview(self):
        if not self.preview_paused:
            return
        self.preview_paused = False
        self.canvas_hint.setText('实时画面已恢复 · 滚轮缩放 · 拖动画面')
        self.refresh_live_preview()

    def refresh_live_preview(self):
        if self.preview_paused or self.monitoring or self.worker:
            return
        if not getattr(self.device, 'last_display', None):
            return
        if not self.device.ensure_stream():
            return
        image, sequence = self.device.stream.snapshot()
        if image is None or sequence == self.preview_sequence:
            return
        self.preview_sequence = sequence
        self.set_frame(image, f'实时画面 · 持续帧流 · {self.device.source_description}')

    def connect_device(self):
        device = self.sync_device()
        def done(message):
            self.config = device.config
            self.device = device
            status = f'● 已连接 · {device.last_display.title}' if device.last_display else '● 已连接'
            self.device_status.setText(status)
            self.device_status.setStyleSheet('color: #54d6b3')
            self.preview_sequence = -1
            self.preview_paused = False
            self.preview_timer.start()
            self.log(message)
        self.start_work('连接设备…', device.connect, done)

    def capture(self):
        device = self.sync_device()
        def done(frame):
            self.config = device.config
            self.device = device
            self.device_status.setText('● 已连接')
            self.device_status.setStyleSheet('color: #54d6b3')
            self.preview_sequence = -1
            self.preview_paused = False
            self.preview_timer.start()
            self.set_frame(frame, f'实时设备画面 · {device.source_description}')
            self.log(f'截图成功：{frame.shape[1]} × {frame.shape[0]} · {device.source_description}')
        self.start_work('获取截图…', device.screenshot, done)

    def test_frame(self):
        if self.frame is None:
            self.error('请先导入画面或获取截图')
            return
        self.pause_live_preview()
        frame = self.frame.copy()
        project = copy.deepcopy(self.project)
        self.start_work('正在识别…', lambda: match_rules(frame, project.rules, project.resolution, ocr=self.ocr, frame_key=self.frame_counter), self.present_matches)

    def present_matches(self, matches, executed=False, rule=None, event=None, cursor=None):
        self.matches = matches
        self.gate.update(matches)
        hits = sum(m.hit for m in matches)
        preview = not self.auto_click.isChecked()
        if cursor is not None:
            self.cursor = cursor
        allowed = walk_tree(self.project.tree, hit_map(matches)) if self.project.tree else None
        if rule is None and event is None:
            rule = choose_rule(self.project.rules, matches, self.gate, allowed)
        gated_out = {m.rule_id for m in matches if m.hit and allowed is not None and m.rule_id not in set(allowed)}
        if event == 'stop':
            self.decision.setText(f'命中 {hits} 条  ·  执行到终止规则，回到第一条')
            self.highlight_active(self.cursor)
        elif event == 'delay' and rule is not None:
            self.decision.setText(f'命中 {hits} 条  ·  延迟「{rule.name}」{rule.delay:.2f} 秒后继续')
            self.highlight_active(rule.id)
        elif event == 'branch' and rule is not None:
            self.decision.setText(f'命中 {hits} 条  ·  条件「{rule.name}」不成立，进入「否则」分支')
            self.highlight_active(rule.id)
        elif event == 'wait' and rule is not None:
            found = any(m.rule_id == rule.id and m.hit for m in matches)
            reason = '画面中没有找到' if not found else gate_reason(rule, self.gate)
            self.decision.setText(f'命中 {hits} 条  ·  等待「{rule.name}」：{reason}')
            self.highlight_active(rule.id)
        elif rule is not None:
            self.decision.setText(f'命中 {hits} 条  →  {rule.name}  /  {MODES[rule.mode]} · {ACTIONS[rule.action]}' + ('（仅预览，未勾选自动执行）' if preview and not executed else ''))
            self.highlight_active(rule.id)
        else:
            self.decision.setText(f'命中 {hits} 条  ·  当前没有可执行动作')
            self.highlight_active(None)
        names = {r.id: r.name for r in self.project.rules}
        for match in matches:
            if not match.hit and match.rule_id != getattr(rule, 'id', None):
                continue
            suffix = '  · 条件未满足，本轮跳过' if match.rule_id in gated_out and match.hit else ''
            self.log(f'{names[match.rule_id]}  {match.score:.3f}  ·  {match.reason}{suffix}')
        self.show_selected_overlay()

    def reset_gate(self):
        self.gate = TriggerGate()
        self.gate.window = self.gate_window()
        self.log('已重置本轮触发状态')
        if self.matches:
            self.present_matches(self.matches)

    def auto_click_changed(self, checked):
        if checked:
            self.log('已开启自动执行：监测循环会真实点击模拟器，请确认模拟器窗口可见且规则已调试完成。')
        else:
            self.log('已关闭自动执行：监测恢复为只识别预览。')

    def gate_window(self):
        """A rule counts as gone only after it stays unmatched for two capture cycles."""
        return max(2.0, self.timer.interval() / 1000 * 2)

    def toggle_monitor(self):
        if self.monitoring:
            self.stop_monitor()
        elif not self.worker:
            self.monitoring = True
            self.preview_paused = False
            self.gate = TriggerGate()
            self.gate.window = self.gate_window()
            self.monitor_btn.setText('暂停监测')
            self.lock_editing(True)
            self.timer.start()
            mode = '自动执行动作' if self.auto_click.isChecked() else '只预览动作（未勾选「自动执行」，不会点击）'
            self.log(f'开始监测：定时截图和识别，{mode}。')
            self.monitor_tick()

    def stop_monitor(self):
        self.monitoring = False
        self.timer.stop()
        if hasattr(self, 'monitor_btn'):
            self.monitor_btn.setText('开始监测')
        if hasattr(self, 'rule_tree'):
            self.active_id = None
            self.highlight_active()
        # 后台任务仍在跑时先不加锁，等任务结束后由 work_finished 恢复界面。
        if hasattr(self, 'rule_tree') and not self.worker:
            self.lock_editing(False)
    def decide_and_act(self, device, project, gate, execute, cursor):
        """One recognition cycle: walk the list one step and optionally run it."""
        frame = device.screenshot()
        # Maa OCR 每轮接收当前截图；帧号保留给识别日志和后续缓存策略。
        self.frame_counter += 1
        matches = match_rules(frame, project.rules, project.resolution, ocr=self.ocr, frame_key=self.frame_counter)
        hits = hit_map(matches)
        rearmed = gate.update(matches)
        size = (frame.shape[1], frame.shape[0])
        found = {match.rule_id: match for match in matches if match.hit}
        # The capture just happened, so the display state is fresh: skip the extra
        # dumpsys round trips that would otherwise make every click ~1.5 s slower.
        tap_once = lambda x, y: device.tap(x, y, verify=False)
        order = walk_tree(project.tree, hits) if project.tree else flat_order(project.rules)
        judges = set(condition_ids(project.tree)) if project.tree else set()
        rule, next_cursor, event = advance(order, hits, project.rules, gate, cursor, judges=judges)
        result = {'frame': frame, 'matches': matches, 'gate': gate, 'rule': rule, 'event': event,
                  'cursor': next_cursor, 'click': None, 'actions': [], 'stopped': False, 'rearmed': rearmed}
        if rule is None or event not in ('run', 'delay', 'branch'):
            return result
        if event == 'branch':
            # 条件未命中：不执行动作，游标已指向「否则」分支。
            result['actions'] = ['条件不成立，进入「否则」分支']
            return result
        if event == 'delay':
            # 在工作线程里等待，不阻塞界面；等待期间定时器会因 worker 存在而跳过。
            time.sleep(rule.delay)
            result['actions'] = [f'延迟 {rule.delay:.2f} 秒']
            return result
        if execute and rule.action in ('click', 'points'):
            actions, stopped, clicks = rule_clicks(rule, found, size, tap=tap_once)
            result['actions'], result['stopped'], result['click'] = actions, stopped, (clicks[-1] if clicks else None)
            gate.mark(rule)
        elif execute and rule.action == 'pause':
            gate.mark(rule)
            result['actions'], result['stopped'] = ['暂停监测'], True
        else:
            result['actions'] = ['计划：' + line for line in describe_rule(rule, found, size)]
        return result

    def monitor_tick(self):
        if self.worker or not self.monitoring:
            return
        device = self.device if getattr(self.device, 'last_display', None) else self.sync_device()
        project = copy.deepcopy(self.project)
        gate = copy.deepcopy(self.gate)
        cursor = self.cursor
        execute = self.auto_click.isChecked()
        def done(result):
            self.config = device.config
            self.device = device
            self.set_frame(result['frame'], f'监测画面{" · 自动执行" if execute else ""} · {device.source_description}')
            self.gate = result['gate']
            self.present_matches(result['matches'], executed=bool(result['click']) or result['stopped'],
                                 rule=result['rule'], event=result['event'], cursor=result['cursor'])
            self.report_actions(result, auto=True)
        self.start_work('监测画面…', lambda: self.decide_and_act(device, project, gate, execute, cursor), done)

    def report_actions(self, result, auto=False):
        """Write the executed (or previewed) steps of one cycle to the log and the decision bar."""
        actions, rule, click, stopped = result['actions'], result['rule'], result['click'], result['stopped']
        names = {item.id: item.name for item in self.project.rules}
        event = result.get('event')
        for rule_id in result.get('rearmed') or []:
            self.log(f'「{names.get(rule_id, rule_id)}」已从画面消失，重新允许触发')
        if event == 'stop':
            self.log('执行到终止规则，回到列表第一条')
        elif event == 'delay' and rule is not None:
            self.log(f'已延迟 {rule.delay:.2f} 秒：{rule.name} → 继续下一条')
        elif event == 'branch' and rule is not None:
            self.log(f'条件不成立：{rule.name} → 进入「否则」分支')
        elif event == 'wait' and rule is not None:
            found = any(m.rule_id == rule.id and m.hit for m in self.matches)
            reason = '画面中没有找到' if not found else gate_reason(rule, self.gate)
            self.log(f'等待：{rule.name} — {reason}')
        if click and rule:
            self.log(f'已自动点击：{rule.name} → ({click[0]}, {click[1]})')
            self.decision.setText(f'已自动点击：{rule.name} · 坐标 {click[0]}, {click[1]}')
        elif rule and rule.action == 'pause':
            self.log('命中暂停规则，本次未点击')
        elif auto and rule:
            self.log(f'本轮已执行：{rule.name} → ' + '（'.join(actions or ['无动作']))
        elif rule:
            self.log('没有满足条件的动作，本次未点击')
        if actions and (rule is None or len(actions) > 1):
            for line in actions:
                self.log(f'  · {line}')
        if stopped:
            self.stop_monitor()
            self.log('规则要求暂停监测，已停止监测循环')

    def single_step(self):
        if self.worker:
            return
        self.stop_monitor()
        answer = QMessageBox.question(self, '执行一次', '将重新获取当前 Android 设备画面，按「规则与条件」里的顺序推进一条。\n如果动作包含点击，将向设备发送真实点击。是否继续？')
        if answer != QMessageBox.StandardButton.Yes:
            return
        device = self.device if getattr(self.device, 'last_display', None) else self.sync_device()
        project = copy.deepcopy(self.project)
        gate = copy.deepcopy(self.gate)
        cursor = self.cursor
        def done(result):
            self.config = device.config
            self.device = device
            self.set_frame(result['frame'], f'执行前截图 · {device.source_description}')
            self.gate = result['gate']
            self.present_matches(result['matches'], executed=bool(result['click']) or result['stopped'],
                                 rule=result['rule'], event=result['event'], cursor=result['cursor'])
            self.report_actions(result)
            self.pause_live_preview()
        self.start_work('执行单步动作…', lambda: self.decide_and_act(device, project, gate, True, cursor), done)

    def refresh_rule_points(self, rules):
        points, mixed = self.common_value(rules, lambda rule: rule.points)
        self.rule_points.clear()
        if mixed:
            self.rule_points.addItem('—（各规则位置不同）')
        else:
            for point in points:
                self.rule_points.addItem(f'X {point[0]}   Y {point[1]}')
        enabled = self.action.currentData() in (None, 'points')
        for control in (self.rule_points, self.rule_point_x, self.rule_point_y, self.rule_point_add, self.rule_point_pick, self.rule_point_delete, self.rule_points_interval, self.rule_points_repeat):
            control.setEnabled(enabled)

    def add_rule_point(self, point=None):
        rules = self.selected_rules()
        if not rules:
            return
        point = list(point or [self.rule_point_x.value(), self.rule_point_y.value()])
        for rule in rules:
            if len(rule.points) >= MAX_POINTS:
                continue
            rule.points.append(list(point))
            if rule.action in ('click', 'observe'):
                rule.action = 'points'
        self.changed()
        self.refresh_rules()
        self.log(f'点击位置已添加：({point[0]}, {point[1]})' + (f'（{len(rules)} 条规则）' if len(rules) > 1 else ''))

    def delete_rule_point(self):
        rules = self.selected_rules()
        row = self.rule_points.currentRow()
        if not rules or row < 0:
            return
        for rule in rules:
            if 0 <= row < len(rule.points):
                rule.points.pop(row)
        self.changed()
        self.refresh_rules()
        self.log('已删除选中的点击位置')

    def capture_point(self, target):
        if self.frame is None:
            self.error('请先获取截图或导入画面')
            return
        if target == 'rule' and not self.selected_rules():
            self.error('请先选择一条规则')
            return
        self.capture_target = target
        self.canvas.set_mode('point')
        self.canvas_hint.setText('在画面上点击要执行的位置，坐标会加入点击列表。')

    def device_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('设备设置')
        dialog.setMinimumWidth(680)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('选择设备对应的 ADB，保持模拟器或 Android 设备处于运行状态。', 'muted'))
        form = QFormLayout()
        adb = QLineEdit(self.config.adb_executable)
        row = QHBoxLayout()
        row.addWidget(adb)
        def browse():
            path, _ = QFileDialog.getOpenFileName(dialog, '选择 adb.exe', adb.text(), 'ADB (adb.exe)')
            if path:
                adb.setText(path)
        row.addWidget(self.button('浏览', browse))
        form.addRow('ADB 路径', row)
        mode = QComboBox()
        mode.addItem('自动跟随前台', 'auto')
        mode.addItem('手动固定显示器', 'manual')
        mode.setCurrentIndex(1 if self.config.capture_mode == 'manual' else 0)
        form.addRow('截图来源', mode)
        picker = QComboBox()
        picker.setMinimumWidth(380)
        if self.config.adb_display_id:
            picker.addItem(f'已保存的显示器 ID {self.config.adb_display_id}', self.config.adb_display_id)
        else:
            picker.addItem('尚未读取显示器', '')
        refresh = self.button('刷新显示器', lambda: load_displays())
        selector = QHBoxLayout()
        selector.addWidget(picker)
        selector.addWidget(refresh)
        form.addRow('固定显示器', selector)
        layout.addLayout(form)
        hint = label('自动模式优先截取模拟器前台显示器；前台仍是桌面时改用正在运行应用的显示器，因此打开游戏后无需手动切换。\n'
                     '手动模式固定使用所选显示器，模拟器重建显示器后需要重新选择。\n'
                     '设置将保存在程序所在目录的 environment.local.json。', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def fill(state):
            picker.clear()
            for item in state.displays:
                if not item.active:
                    continue
                app = f' / {item.app}' if item.app else ' / 无前台应用'
                picker.addItem(f'{item.title}{app}', item.physical_id)
            if picker.count() == 0:
                picker.addItem('未找到活动显示器', '')
            index = picker.findData(self.config.adb_display_id)
            if index < 0:
                try:
                    preferred = select_display(DeviceConfig(capture_mode='auto'), state).physical_id
                except RuntimeError:
                    preferred = ''
                index = picker.findData(preferred)
            picker.setCurrentIndex(max(index, 0))

        def load_displays():
            refresh.setEnabled(False)
            picker.clear()
            picker.addItem('正在读取显示器…', '')
            probe = Device(DeviceConfig(adb_executable=adb.text().strip(), adb_serial=self.serial.text().strip()))
            worker = Worker(probe.display_state, dialog)
            def done(state):
                fill(state)
                refresh.setEnabled(mode.currentData() == 'manual')
            def failed(message):
                refresh.setEnabled(mode.currentData() == 'manual')
                picker.clear()
                picker.addItem('读取显示器失败', '')
                QMessageBox.warning(dialog, '读取显示器失败', message)
            worker.result.connect(done)
            worker.failed.connect(failed)
            worker.finished.connect(worker.deleteLater)
            self.settings_worker = worker
            worker.start()

        def sync_mode():
            manual = mode.currentData() == 'manual'
            picker.setEnabled(manual)
            refresh.setEnabled(manual)
            if manual and not picker.currentData():
                load_displays()

        mode.currentIndexChanged.connect(lambda index: sync_mode())
        sync_mode()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not Path(adb.text()).is_file():
                self.error('ADB 路径不存在')
                return
            capture_mode = mode.currentData()
            physical = picker.currentData() if capture_mode == 'manual' else ''
            if capture_mode == 'manual' and not physical:
                self.error('请先点击「刷新显示器」并选择一个固定显示器')
                return
            self.config.adb_executable = adb.text().strip()
            self.config.adb_serial = self.serial.text().strip()
            self.config.capture_mode = capture_mode
            self.config.adb_display_id = physical
            self.device = Device(self.config)
            try:
                (app_directory() / 'environment.local.json').write_text(json.dumps(asdict(self.config), ensure_ascii=False, indent=2), encoding='utf-8')
                self.log('设备设置已保存')
            except OSError as exc:
                self.error(f'设置已应用，但无法保存：{exc}')

    def closeEvent(self, event):
        self.preview_timer.stop()
        if getattr(self.device, 'stream', None):
            self.device.stream.stop()
        self.stop_monitor()
        if self.worker:
            self.statusBar().showMessage('请等待当前设备操作结束后再关闭')
            event.ignore()
            return
        if self.check_unsaved():
            event.accept()
        else:
            event.ignore()

