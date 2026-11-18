from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from .models import Project, Rule, encode_image, tree_from_rules


def demo_project() -> Project:
    image = QImage(1280, 900, QImage.Format.Format_RGB32)
    image.fill(QColor('#152136'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    def label(x, y, w, h, text, size, color='#f1f5fc', weight=False):
        font = QFont('Microsoft YaHei UI', size)
        font.setBold(weight)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRect(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)
    label(80, 45, 1120, 60, '选择你的出战卡牌', 32, weight=True)
    label(80, 110, 1120, 40, '演示画面 · 用于体验图片识别与规则优先级', 15, '#9faec8')
    cards = [(110, '#47d7b1', 'A', '治疗支援', '优先选择'), (490, '#70a7ff', 'B', '远程输出', '第二优先'), (870, '#c99aff', 'C', '防御守护', '第三优先')]
    templates = []
    for x, color, letter, name, subtitle in cards:
        painter.setPen(QPen(QColor(color), 3))
        painter.setBrush(QColor('#23324c'))
        painter.drawRoundedRect(x, 235, 300, 410, 22, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(x+95, 295, 110, 110)
        label(x+95, 300, 110, 100, letter, 48, '#172137', True)
        label(x+20, 445, 260, 65, name, 24, weight=True)
        label(x+20, 515, 260, 50, subtitle, 16, color)
        templates.append([x+10, 245, 280, 390])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#47d7b1'))
    painter.drawRoundedRect(460, 735, 360, 85, 16, 16)
    label(460, 735, 360, 85, '准备', 25, '#122331', True)
    painter.end()
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    array = np.frombuffer(rgb.bits(), np.uint8).reshape(rgb.height(), rgb.bytesPerLine())[:, :rgb.width()*3].reshape(rgb.height(), rgb.width(), 3)
    frame = np.ascontiguousarray(array[:, :, ::-1])
    rules = []
    for name, rectangle in zip(['治疗支援 · 首选', '远程输出 · 次选', '防御守护 · 备选'], templates):
        x, y, w, h = rectangle
        rules.append(Rule(name=name, template=encode_image(frame[y:y+h, x:x+w]), region=[70, 190, 1140, 500]))
    rules.append(Rule(name='准备按钮', template=encode_image(frame[740:815, 465:815]), region=[350, 680, 600, 180]))
    return Project('卡牌选择 · 示例方案', rules, encode_image(frame), [1280, 900], tree_from_rules(rules))
