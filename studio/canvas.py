from __future__ import annotations

import math
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene


def image_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(image[:, :, ::-1])
    return QPixmap.fromImage(QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy())


class ScreenshotCanvas(QGraphicsView):
    selection = Signal(str, object)
    cursor_position = Signal(str)
    pressed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('screenshotCanvas')
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(QColor('#101925'))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMouseTracking(True)
        self.image: np.ndarray | None = None
        self.mode = 'pan'
        self.origin: QPointF | None = None
        self.rubber = None
        self.overlays = []
        self.fit_enabled = True
        self.set_mode('pan')

    def set_image(self, image: np.ndarray):
        self.scene().clear()
        self.overlays.clear()
        self.rubber = None
        self.image = image.copy()
        self.scene().addPixmap(image_to_pixmap(image))
        self.scene().setSceneRect(0, 0, image.shape[1], image.shape[0])
        if self.fit_enabled:
            self.fit_image()

    def clear_image(self):
        self.scene().clear()
        self.image = None
        self.overlays.clear()
        self.rubber = None

    def fit_image(self):
        self.fit_enabled = True
        if self.image is not None:
            self.fitInView(self.sceneRect().adjusted(-24, -24, 24, 24), Qt.AspectRatioMode.KeepAspectRatio)

    def set_mode(self, mode: str):
        self.mode = mode
        self.origin = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if mode == 'pan' else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if mode == 'pan' else Qt.CursorShape.CrossCursor)

    def point(self, event) -> QPointF:
        point = self.mapToScene(event.position().toPoint())
        bounds = self.sceneRect()
        return QPointF(max(0, min(point.x(), bounds.width())), max(0, min(point.y(), bounds.height())))

    def mousePressEvent(self, event):
        # 暂停实时画面时，在画面上点一下（浏览状态）即恢复实时更新；
        # 绘制模板/区域时不清扰，避免画面在拖动过程中被刷新。
        if self.image is not None and self.mode == 'pan' and event.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit()
        if self.image is not None and self.mode != 'pan' and event.button() == Qt.MouseButton.LeftButton:
            point = self.point(event)
            if self.mode == 'point':
                x = min(int(point.x()), self.image.shape[1] - 1)
                y = min(int(point.y()), self.image.shape[0] - 1)
                self.selection.emit('point', [x, y])
                self.set_mode('pan')
                return
            self.origin = point
            pen = QPen(QColor('#52e0bc'), 2)
            pen.setCosmetic(True)
            self.rubber = self.scene().addRect(QRectF(point, point), pen, QColor(82, 224, 188, 35))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.image is not None:
            point = self.point(event)
            self.cursor_position.emit(f'X {int(point.x())}   Y {int(point.y())}')
            if self.origin is not None and self.rubber:
                self.rubber.setRect(QRectF(self.origin, point).normalized())
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.origin is not None and self.rubber:
            rect = self.rubber.rect().intersected(self.sceneRect())
            self.scene().removeItem(self.rubber)
            self.rubber = None
            self.origin = None
            x, y = math.floor(rect.x()), math.floor(rect.y())
            w, h = math.ceil(rect.right()) - x, math.ceil(rect.bottom()) - y
            mode = self.mode
            self.set_mode('pan')
            if w >= 3 and h >= 3:
                self.selection.emit(mode, [x, y, w, h])
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.image is not None:
            self.fit_enabled = False
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            scale = self.transform().m11() * factor
            if 0.04 < scale < 10:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_enabled:
            self.fit_image()

    def clear_overlays(self):
        for item in self.overlays:
            self.scene().removeItem(item)
        self.overlays.clear()

    def draw_box(self, rect, color: str, text: str = ''):
        pen = QPen(QColor(color), 3)
        pen.setCosmetic(True)
        self.overlays.append(self.scene().addRect(QRectF(*rect), pen))
        if text:
            label = self.scene().addSimpleText(text)
            label.setBrush(QColor(color))
            label.setPos(rect[0], max(0, rect[1] - 24))
            self.overlays.append(label)

    def draw_point(self, x: int, y: int):
        pen = QPen(QColor('#ffcc7a'), 3)
        pen.setCosmetic(True)
        self.overlays.append(self.scene().addEllipse(x-9, y-9, 18, 18, pen))
        self.overlays.append(self.scene().addLine(x-16, y, x+16, y, pen))
        self.overlays.append(self.scene().addLine(x, y-16, x, y+16, pen))
