"""MaaFramework recognition adapter for image templates and OCR."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
import sys

import numpy as np
from maa.controller import CustomController
from maa.pipeline import JOCR, JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker

from .models import Rule, decode_image
from .ocr import OCRText, text_match


class _HeadlessController(CustomController):
    """Satisfy MaaTasker initialization without starting a second device capture."""

    def connect(self) -> bool:
        return True

    def connected(self) -> bool:
        return True

    def request_uuid(self) -> str:
        return 'visionflow'

    def get_features(self) -> int:
        return 0

    def screencap(self):
        return np.zeros((1, 1, 3), dtype=np.uint8)

    def click(self, *args) -> bool:
        return False

    def swipe(self, *args) -> bool:
        return False

    def touch_down(self, *args) -> bool:
        return False

    def touch_move(self, *args) -> bool:
        return False

    def touch_up(self, *args) -> bool:
        return False

    def click_key(self, *args) -> bool:
        return False

    def input_text(self, *args) -> bool:
        return False

    def key_down(self, *args) -> bool:
        return False

    def key_up(self, *args) -> bool:
        return False

    def start_app(self, *args) -> bool:
        return False

    def stop_app(self, *args) -> bool:
        return False


def assets_directory() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'maa-assets'


class MaaVision:
    """Run all image and text recognition through MaaFramework."""

    def __init__(self, assets: Path | None = None):
        self.assets = assets or assets_directory()
        self._tasker: Tasker | None = None
        self._resource: Resource | None = None
        self._controller: _HeadlessController | None = None
        self._template_names: dict[str, str] = {}
        self._lock = threading.RLock()

    def _ensure(self) -> Tasker:
        with self._lock:
            if self._tasker is not None:
                return self._tasker
            if not (self.assets / 'model' / 'ocr' / 'det.onnx').is_file():
                raise RuntimeError(f'找不到 Maa OCR 模型：{self.assets / "model" / "ocr"}')
            resource = Resource()
            load_job = resource.post_bundle(str(self.assets))
            load_job.wait()
            if not load_job.succeeded:
                raise RuntimeError('MaaFramework 资源加载失败')
            controller = _HeadlessController()
            connection = controller.post_connection()
            connection.wait()
            if not connection.succeeded:
                raise RuntimeError('MaaFramework 控制器初始化失败')
            tasker = Tasker()
            if not tasker.bind(resource, controller):
                raise RuntimeError('MaaFramework 任务器绑定失败')
            self._resource, self._controller, self._tasker = resource, controller, tasker
            return tasker

    def _template_name(self, template: str) -> str:
        digest = hashlib.sha1(template.encode('ascii')).hexdigest()
        return f'{digest}.png'

    def _override_template(self, template: str) -> str:
        name = self._template_name(template)
        if name not in self._template_names:
            assert self._resource is not None
            self._resource.override_image(name, decode_image(template))
            self._template_names[name] = name
        return name

    @staticmethod
    def _node(job):
        job.wait()
        detail = job.get()
        if not detail or not detail.nodes:
            return None
        return detail.nodes[0].recognition

    def match_template(self, frame: np.ndarray, rule: Rule):
        tasker = self._ensure()
        template_image = decode_image(rule.template)
        method = 0 if float(template_image.std()) < 1e-6 else 5
        name = self._template_name(rule.template)
        if name not in self._template_names:
            assert self._resource is not None
            self._resource.override_image(name, template_image)
            self._template_names[name] = name
        roi = tuple(rule.region or (0, 0, 0, 0))
        param = JTemplateMatch(template=[name], roi=roi, threshold=[rule.threshold], index=0, method=method)
        recognition = self._node(tasker.post_recognition(JRecognitionType.TemplateMatch, param, frame))
        if recognition is None or recognition.best_result is None:
            return False, 0.0, None, '未找到模板'
        result = recognition.best_result
        box = tuple(int(value) for value in result.box)
        score = float(result.score)
        return bool(recognition.hit), score, box, '达到阈值' if recognition.hit else '低于阈值'

    def recognize(self, frame: np.ndarray, region: list[int], frame_key=None) -> list[OCRText]:
        tasker = self._ensure()
        param = JOCR(expected=[], roi=tuple(region), threshold=0.0)
        recognition = self._node(tasker.post_recognition(JRecognitionType.OCR, param, frame))
        if recognition is None:
            return []
        return [OCRText(str(item.text), float(item.score), tuple(int(value) for value in item.box))
                for item in (recognition.all_results or [])]

    def recognize_text(self, frame: np.ndarray, rule: Rule) -> list[OCRText]:
        tasker = self._ensure()
        region = tuple(rule.text_region or (0, 0, 0, 0))
        param = JOCR(expected=[], roi=region, threshold=float(rule.text_confidence))
        recognition = self._node(tasker.post_recognition(JRecognitionType.OCR, param, frame))
        if recognition is None:
            return []
        result = []
        for item in recognition.all_results or []:
            box = tuple(int(value) for value in item.box)
            result.append(OCRText(str(item.text), float(item.score), box))
        return result

    def text_match(self, frame: np.ndarray, rule: Rule):
        return text_match(self.recognize_text(frame, rule), rule)
