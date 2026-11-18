from __future__ import annotations

import base64
import json
import math
import os
import re
import tempfile
import uuid
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

ACTIONS = {'observe': '仅识别', 'click': '点击目标', 'points': '点击指定位置', 'pause': '暂停监测'}
MODES = {'match': '图像识别', 'text': '文字识别', 'always': '始终执行', 'delay': '延迟执行', 'stop': '终止'}
SHORT_MODES = {'match': '识别', 'text': '文字', 'always': '常执行', 'delay': '延迟', 'stop': '终止'}
TEXT_MATCHES = {'contains': '包含文字', 'exact': '完全匹配'}
TRIGGERS = {'once': '每轮一次', 'reappear': '消失后再次出现', 'interval': '按间隔触发'}
TREE_KINDS = {'rule': '规则', 'if': '条件分支'}
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
MAX_RULES = 200
MAX_TREE_DEPTH = 6
MAX_TREE_NODES = 400
MAX_POINTS = 20


def encode_image(image: np.ndarray) -> str:
    ok, data = cv2.imencode('.png', image)
    if not ok:
        raise ValueError('图片编码失败')
    return base64.b64encode(data).decode('ascii')


@lru_cache(maxsize=256)
def decode_image(value: str) -> np.ndarray:
    try:
        raw = base64.b64decode(value, validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        raise ValueError('图片数据损坏，请重新导入') from exc
    if image is None or image.size == 0:
        raise ValueError('图片数据损坏，请重新导入')
    if image.shape[0] > 12000 or image.shape[1] > 12000:
        raise ValueError('图片尺寸过大，最大支持 12000 × 12000')
    return image


def read_image(path: str) -> np.ndarray:
    return decode_image(base64.b64encode(Path(path).read_bytes()).decode('ascii'))


def validate_points(points, limit: int = MAX_POINTS) -> list[list[int]]:
    """Click points are absolute screen coordinates."""
    if not isinstance(points, list) or len(points) > limit:
        raise ValueError(f'点击位置无效，最多支持 {limit} 个')
    result = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2 or not all(type(value) is int and abs(value) <= 12000 for value in point):
            raise ValueError('点击位置必须是 [X, Y] 整数对')
        result.append([point[0], point[1]])
    return result


def validate_pacing(interval, repeat) -> tuple[float, int]:
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or not 0 <= interval <= 60:
        raise ValueError('点击间隔必须在 0 和 60 秒之间')
    if type(repeat) is not int or not 1 <= repeat <= 100:
        raise ValueError('重复次数必须在 1 和 100 之间')
    return float(interval), repeat


def natural_key(text: str) -> list:
    """Sort keys so 2.png comes before 10.png, without mixing int and str comparisons."""
    return [(0, int(chunk)) if chunk.isdigit() else (1, chunk.lower()) for chunk in re.split(r'(\d+)', text) if chunk]


def collect_images(folder: Path, limit: int = 0) -> list[Path]:
    """Every supported image inside the folder (including subfolders), name sorted."""
    if not folder.is_dir():
        raise ValueError('文件夹不存在')
    try:
        files = [path for path in folder.rglob('*') if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
    except OSError as exc:
        raise ValueError(f'无法读取文件夹：{exc}') from exc
    files.sort(key=lambda path: natural_key(str(path.relative_to(folder).as_posix())))
    return files[:limit] if limit else files


def unique_names(names: list[str], parents: list[str]) -> list[str]:
    """Keep file stems readable, appending the folder only when stems repeat."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    seen: dict[str, int] = {}
    result = []
    for name, parent in zip(names, parents):
        if counts[name] > 1 and parent:
            result.append(f'{name} · {parent}')
            continue
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f'{name} ({seen[name]})')
    return result


@dataclass
class Rule:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = '新规则'
    mode: str = 'match'
    enabled: bool = True
    delay: float = 1.0
    text: str = ''
    text_match: str = 'contains'
    text_confidence: float = 0.60
    text_region: list[int] | None = None
    threshold: float = 0.90
    template: str = ''
    region: list[int] | None = None
    action: str = 'click'
    offset: list[int] = field(default_factory=lambda: [0, 0])
    trigger: str = 'once'
    interval: float = 2.0
    points: list[list[int]] = field(default_factory=list)
    points_interval: float = 0.5
    points_repeat: int = 1

    @classmethod
    def from_dict(cls, raw: dict) -> 'Rule':
        if not isinstance(raw, dict):
            raise ValueError('规则格式错误')
        fields = set(cls.__dataclass_fields__)
        # 旧版文件带有「所属阶段」，读取时忽略，不再作为规则属性。
        extra = set(raw) - fields
        if extra - {'stage'}:
            raise ValueError('规则包含不支持的字段')
        rule = cls(**{key: value for key, value in raw.items() if key in fields})
        if not isinstance(rule.id, str) or not rule.id:
            raise ValueError('规则 ID 无效')
        if not isinstance(rule.name, str) or not rule.name.strip():
            raise ValueError('规则名称不能为空')
        if type(rule.enabled) is not bool:
            raise ValueError('规则启用状态无效')
        if not isinstance(rule.threshold, (int, float)) or not math.isfinite(rule.threshold) or not 0.5 <= rule.threshold <= 1:
            raise ValueError('匹配阈值必须在 0.50 和 1.00 之间')
        if rule.action not in ACTIONS or rule.trigger not in TRIGGERS:
            raise ValueError('不支持的动作或触发方式')
        if rule.mode not in MODES:
            raise ValueError('不支持的规则模式')
        if not isinstance(rule.delay, (int, float)) or not math.isfinite(rule.delay) or not 0.05 <= rule.delay <= 600:
            raise ValueError('延迟时间必须在 0.05 和 600 秒之间')
        if rule.mode == 'text':
            if not isinstance(rule.text, str) or not rule.text.strip() or len(rule.text) > 200:
                raise ValueError('文字识别目标不能为空且最多 200 个字符')
            if rule.text_match not in TEXT_MATCHES:
                raise ValueError('不支持的文字匹配方式')
            if not isinstance(rule.text_confidence, (int, float)) or not math.isfinite(rule.text_confidence) or not 0.05 <= rule.text_confidence <= 1:
                raise ValueError('文字识别置信度必须在 0.05 和 1.00 之间')
            if not isinstance(rule.text_region, list) or len(rule.text_region) != 4 or not all(type(x) is int for x in rule.text_region):
                raise ValueError('文字识别必须设置一个矩形识别区域')
            x, y, w, h = rule.text_region
            if min(x, y) < 0 or min(w, h) <= 0:
                raise ValueError('文字识别区域必须是正数矩形')
        if not isinstance(rule.interval, (int, float)) or not math.isfinite(rule.interval) or not 0.2 <= rule.interval <= 3600:
            raise ValueError('触发间隔必须在 0.2 和 3600 秒之间')
        if not isinstance(rule.offset, list) or len(rule.offset) != 2 or not all(type(x) is int and abs(x) <= 12000 for x in rule.offset):
            raise ValueError('点击偏移无效')
        if rule.region is not None:
            if not isinstance(rule.region, list) or len(rule.region) != 4 or not all(type(x) is int for x in rule.region):
                raise ValueError('搜索区域无效')
            x, y, w, h = rule.region
            if min(x, y) < 0 or min(w, h) <= 0:
                raise ValueError('搜索区域必须在画面内')
        if not isinstance(rule.template, str):
            raise ValueError('模板格式无效')
        if rule.template:
            decode_image(rule.template)
        rule.points = validate_points(rule.points)
        rule.points_interval, rule.points_repeat = validate_pacing(rule.points_interval, rule.points_repeat)
        if rule.mode == 'stop':
            return rule
        if rule.action == 'points' and not rule.points:
            raise ValueError('「点击指定位置」至少需要一个点击位置')
        if rule.mode == 'always' and rule.action == 'click':
            raise ValueError('「始终执行」没有匹配中心，请改用「点击指定位置」')
        return rule


@dataclass
class TreeNode:
    """One row of the rule list.

    ``rule`` rows execute that rule. ``if`` rows are condition branches: their
    ``rule_id`` rule is only a condition, and it decides which child list runs.
    """
    kind: str = 'rule'
    rule_id: str = ''
    then: list['TreeNode'] = field(default_factory=list)
    otherwise: list['TreeNode'] = field(default_factory=list)

    @classmethod
    def leaf(cls, rule_id: str) -> 'TreeNode':
        return cls(kind='rule', rule_id=rule_id)

    @classmethod
    def branch(cls, rule_id: str) -> 'TreeNode':
        return cls(kind='if', rule_id=rule_id)

    @classmethod
    def from_dict(cls, raw, depth: int = 0, counter: list | None = None) -> 'TreeNode':
        counter = [0] if counter is None else counter
        if not isinstance(raw, dict):
            raise ValueError('规则列表格式错误')
        if set(raw) - set(cls.__dataclass_fields__):
            raise ValueError('规则列表包含不支持的字段')
        node = cls(**raw)
        counter[0] += 1
        if counter[0] > MAX_TREE_NODES:
            raise ValueError(f'规则列表最多 {MAX_TREE_NODES} 行')
        if node.kind not in TREE_KINDS:
            raise ValueError('不支持的规则列表类型')
        if not isinstance(node.rule_id, str) or not node.rule_id:
            raise ValueError('规则列表每一行都必须引用一条规则')
        if not isinstance(node.then, list) or not isinstance(node.otherwise, list):
            raise ValueError('条件分支格式错误')
        if node.kind == 'rule':
            if node.then or node.otherwise:
                raise ValueError('规则行不能包含子行')
        else:
            if depth >= MAX_TREE_DEPTH:
                raise ValueError(f'条件最多嵌套 {MAX_TREE_DEPTH} 层')
            node.then = [cls.from_dict(item, depth + 1, counter) for item in node.then]
            node.otherwise = [cls.from_dict(item, depth + 1, counter) for item in node.otherwise]
        return node


def leaf_ids(nodes: list[TreeNode]) -> list[str]:
    """Rule ids that can execute, in list order."""
    result: list[str] = []
    for node in nodes:
        if node.kind == 'if':
            result.extend(leaf_ids(node.then))
            result.extend(leaf_ids(node.otherwise))
        else:
            result.append(node.rule_id)
    return result


def condition_ids(nodes: list[TreeNode]) -> list[str]:
    result: list[str] = []
    for node in nodes:
        if node.kind == 'if':
            result.append(node.rule_id)
            result.extend(condition_ids(node.then))
            result.extend(condition_ids(node.otherwise))
    return result


def eligible_ids(nodes: list[TreeNode], hits) -> list[str]:
    """Rule ids allowed this cycle: a condition opens only one of its branches."""
    result: list[str] = []

    def walk(items):
        for node in items:
            if node.kind == 'if':
                walk(node.then if hits.get(node.rule_id, False) else node.otherwise)
            else:
                result.append(node.rule_id)

    walk(nodes)
    return result


def prune_tree(nodes: list[TreeNode], removed: set[str]) -> tuple[list[TreeNode], int]:
    """Drop rows that reference deleted rules, including conditions using them."""
    count = [0]

    def walk(items):
        kept = []
        for node in items:
            if node.rule_id in removed:
                count[0] += 1
                continue
            if node.kind == 'if':
                node.then = walk(node.then)
                node.otherwise = walk(node.otherwise)
            kept.append(node)
        return kept

    return walk(nodes), count[0]


def normalize_tree(nodes: list[TreeNode], rule_ids: list[str]) -> tuple[list[TreeNode], list[str]]:
    """Validate references, keep one row per rule, and append rules missing from the list.

    同一规则既被当作条件行、又作为普通行存在时（旧版本「添加条件」会留下这样的重复），
    以条件行为准：否则保存时会丢掉整个分支。
    """
    notes: list[str] = []
    known = set(rule_ids)
    used: set[str] = set()
    judges = set(condition_ids(nodes))

    def walk(items):
        kept = []
        for node in items:
            if node.rule_id not in known:
                notes.append('已移除引用不存在规则的条目')
                continue
            if node.rule_id in used:
                if node.kind == 'if':
                    notes.append('同一规则在列表里出现了多次，已保留第一处')
                continue
            if node.kind == 'rule' and node.rule_id in judges:
                # 重复的普通行：条件分支优先，直接丢掉这一行。
                notes.append('同一规则既是条件行又是普通行，已按条件分支处理')
                continue
            used.add(node.rule_id)
            if node.kind == 'if':
                node.then = walk(node.then)
                node.otherwise = walk(node.otherwise)
            kept.append(node)
        return kept

    nodes = walk(nodes)
    for rule_id in rule_ids:
        if rule_id not in used:
            nodes.append(TreeNode.leaf(rule_id))
            used.add(rule_id)
    return nodes, notes


def tree_from_rules(rules: list[Rule]) -> list[TreeNode]:
    return [TreeNode.leaf(rule.id) for rule in rules]


@dataclass
class Project:
    name: str = '未命名方案'
    rules: list[Rule] = field(default_factory=list)
    reference: str = ''
    resolution: list[int] | None = None
    tree: list[TreeNode] = field(default_factory=list)
    load_notes: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        # 保存前复用同一套规则校验，避免把缺少文字识别区域的坏规则写入方案。
        for rule in self.rules:
            Rule.from_dict(asdict(rule))
        tree = [asdict(node) for node in (self.tree or tree_from_rules(self.rules))]
        return {'format': 'visionflow', 'version': 5,
                'name': self.name,
                'rules': [asdict(rule) for rule in self.rules],
                'reference': self.reference, 'resolution': self.resolution,
                'tree': tree}

    def save(self, path: Path) -> None:
        data = json.dumps(self.payload(), ensure_ascii=False, indent=2)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, suffix='.tmp', delete=False) as output:
                temp = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
        finally:
            if temp and temp.exists():
                temp.unlink()

    @classmethod
    def load(cls, path: Path) -> 'Project':
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('配置文件超过 64 MB')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        # 兼容旧版 mumu-rule-studio / mumu-auto 方案文件，新方案统一写入 visionflow。
        if not isinstance(data, dict) or data.get('format') not in ('visionflow', 'mumu-auto', 'mumu-rule-studio') or data.get('version') not in (1, 2, 3, 4, 5):
            raise ValueError('不是受支持的 VisionFlow 配置文件')
        rules_raw = data.get('rules')
        if not isinstance(rules_raw, list) or len(rules_raw) > MAX_RULES:
            raise ValueError(f'规则列表无效，最多支持 {MAX_RULES} 条')
        rules = [Rule.from_dict(item) for item in rules_raw]
        if len({rule.id for rule in rules}) != len(rules):
            raise ValueError('规则 ID 重复')
        name = data.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('方案名称无效')
        reference, resolution = data.get('reference', ''), data.get('resolution')
        if resolution is not None and (not isinstance(resolution, list) or len(resolution) != 2 or not all(type(n) is int and 0 < n <= 12000 for n in resolution)):
            raise ValueError('基准分辨率无效')
        if not isinstance(reference, str):
            raise ValueError('参考画面无效')
        if reference:
            image = decode_image(reference)
            if resolution != [image.shape[1], image.shape[0]]:
                raise ValueError('参考画面与基准分辨率不一致')
        notes: list[str] = []
        raw_tree = data.get('tree')
        if isinstance(raw_tree, list):
            tree = [TreeNode.from_dict(item) for item in raw_tree]
        else:
            tree = tree_from_rules(rules)
            if data.get('version') in (1, 2, 3):
                notes.append('旧版文件没有条件分支信息，规则按原顺序排列；需要条件分支请重新添加')
        tree, cleaned = normalize_tree(tree, [rule.id for rule in rules])
        notes.extend(cleaned)
        mapping = {rule.id: rule for rule in rules}
        ordered = [mapping[rule_id] for rule_id in leaf_ids(tree)]
        ordered.extend(rule for rule in rules if rule not in ordered)
        return cls(name, ordered, reference, resolution, tree, notes)
