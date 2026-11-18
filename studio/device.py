from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, fields
from pathlib import Path
import cv2
import numpy as np
from .stream import AdbScreenStream


def app_directory() -> Path:
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


@dataclass
class DeviceConfig:
    adb_executable: str = r'E:\App\MuMuPlayer\nx_main\adb.exe'
    adb_serial: str = '127.0.0.1:16384'
    adb_display_id: str = ''
    mumu_manager_executable: str = r'E:\App\MuMuPlayer\nx_main\MuMuManager.exe'
    capture_mode: str = 'auto'
    capture_backend: str = 'stream'

    @classmethod
    def load(cls) -> 'DeviceConfig':
        path = app_directory() / 'environment.local.json'
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                result = cls(**{field.name: str(data[field.name]) for field in fields(cls) if field.name in data})
                # v0.1 cached the desktop ID without expressing a user selection.
                # Old files default to auto; only explicit manual mode pins an ID.
                if result.capture_mode not in ('auto', 'manual'):
                    result.capture_mode = 'auto'
                if result.capture_backend not in ('stream', 'adb'):
                    result.capture_backend = 'stream'
                return result
            except (ValueError, TypeError, OSError):
                pass
        return cls()


@dataclass(frozen=True)
class Display:
    physical_id: str
    logical_id: int | None
    name: str
    active: bool = True
    app: str = ''

    @property
    def title(self) -> str:
        logical = str(self.logical_id) if self.logical_id is not None else '?'
        return f'显示器 {logical} · {self.name}'


@dataclass
class DisplayState:
    displays: list[Display]
    focused_id: int | None


def parse_display_state(physical_text: str, display_text: str, window_text: str) -> DisplayState:
    mapping: dict[str, tuple[int, bool]] = {}
    # Viewports explicitly map screencap physical IDs to input logical IDs.
    for block in re.findall(r'DisplayViewport\{([^}]+)\}', display_text):
        logical = re.search(r'\bdisplayId\s*=\s*(\d+)', block)
        physical = re.search(r"uniqueId\s*=\s*['\"]local:(\d+)['\"]", block)
        if logical and physical:
            active = not re.search(r'\b(?:valid|isActive)\s*=\s*false\b', block)
            mapping[physical.group(1)] = (int(logical.group(1)), active)
    # Fallback for Android builds that omit viewport diagnostics.
    for line in display_text.splitlines():
        if 'DisplayInfo{' not in line:
            continue
        logical = re.search(r'\bdisplayId\s+(\d+)', line)
        physical = re.search(r'\buniqueId\s+["\']local:(\d+)["\']', line)
        if logical and physical:
            mapping.setdefault(physical.group(1), (int(logical.group(1)), not bool(re.search(r'\bstate OFF\b', line))))
    focus = re.search(r'\bmTopFocusedDisplayId\s*=\s*(\d+)', window_text)
    focused_id = int(focus.group(1)) if focus else None
    apps = {}
    section_id = None
    for line in window_text.splitlines():
        header = re.search(r'\bDisplay: mDisplayId=(\d+)', line)
        if header:
            section_id = int(header.group(1))
        if section_id is not None and ('mCurrentFocus=' in line or 'mFocusedApp=' in line):
            app = re.search(r'\bu\d+\s+([A-Za-z0-9_.]+)/(\S+)', line)
            if app:
                apps[section_id] = app.group(1)
    if focused_id is None and len(apps) == 1:
        focused_id = next(iter(apps))
    displays = []
    for line in physical_text.splitlines():
        match = re.match(r'Display\s+(\d+)\b', line.strip())
        if not match:
            continue
        physical_id = match.group(1)
        name = re.search(r'displayName="([^"]+)"', line)
        logical, active = mapping.get(physical_id, (None, True))
        displays.append(Display(physical_id, logical, name.group(1) if name else 'Android', active, apps.get(logical, '')))
    return DisplayState(displays, focused_id)


LAUNCHER_HINTS = ('launcher', 'lawnchair', 'quickstep', '.home', 'homescreen')


def is_launcher_app(app: str) -> bool:
    app = app.strip().lower()
    return bool(app) and any(hint in app for hint in LAUNCHER_HINTS)


def select_display(config: DeviceConfig, state: DisplayState) -> Display:
    if not state.displays:
        raise RuntimeError('未找到显示器，请检查模拟器状态')
    if config.capture_mode == 'manual':
        selected = next((d for d in state.displays if d.physical_id == config.adb_display_id and d.active), None)
        if not selected:
            raise RuntimeError('手动固定的显示器已失效，请在设备设置中刷新显示器后重新选择，或改为自动跟随前台')
        return selected
    active = [d for d in state.displays if d.active]
    if not active:
        raise RuntimeError('模拟器没有可用显示器，请检查模拟器状态')
    focused = next((d for d in active if state.focused_id is not None and d.logical_id == state.focused_id), None)
    # Some Android emulators render each app on its own virtual display; while an app is loading the
    # focused display can still be the launcher desktop, so prefer the display that
    # owns a real foreground app instead of blindly trusting the focus id.
    apps = [d for d in active if d.app and not is_launcher_app(d.app)]
    if focused is not None and focused.app and not is_launcher_app(focused.app):
        return focused
    if len(apps) == 1:
        return apps[0]
    if focused is not None:
        return focused
    if len(active) == 1:
        return active[0]
    raise RuntimeError('无法确定前台显示器，请点击「刷新显示器」后手动选择截图来源')


class Device:
    def __init__(self, config: DeviceConfig):
        self.config = config
        self.last_display: Display | None = None
        self.stream: AdbScreenStream | None = None

    @property
    def source_description(self) -> str:
        if not self.last_display:
            return '尚未选择截图来源'
        display = self.last_display
        mode = '自动跟随前台' if self.config.capture_mode == 'auto' else '手动固定'
        app = f' / {display.app}' if display.app else ''
        return f'{mode} / {display.title} / ID {display.physical_id}{app}'

    def run(self, *arguments: str) -> bytes:
        if not Path(self.config.adb_executable).is_file():
            raise ValueError('找不到 ADB，请在设备设置中选择对应 Android 设备的 adb.exe')
        try:
            result = subprocess.run([self.config.adb_executable, *arguments], capture_output=True, timeout=18,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('设备响应超时，请检查模拟器是否正常运行') from exc
        if result.returncode:
            error = result.stderr.decode('utf-8', errors='replace').strip()
            raise RuntimeError(error or 'ADB 命令执行失败')
        return result.stdout

    def command(self, *arguments: str) -> bytes:
        return self.run('-s', self.config.adb_serial, *arguments)

    def display_state(self) -> DisplayState:
        def query(*args):
            return self.command('shell', 'dumpsys', *args).decode('utf-8', errors='replace')
        return parse_display_state(query('SurfaceFlinger', '--display-id'), query('display'), query('window'))

    def connect(self) -> str:
        serial = self.config.adb_serial.strip()
        if not serial:
            raise ValueError('请填写设备地址')
        if ':' in serial:
            self.run('connect', serial)
        if self.command('get-state').decode().strip() != 'device':
            raise RuntimeError('设备尚未就绪')
        self.last_display = select_display(self.config, self.display_state())
        if self.config.capture_backend == 'stream':
            self.stream = AdbScreenStream(self.config.adb_executable, self.config.adb_serial,
                                          self.last_display.physical_id)
            self.stream.start()
        return f'已连接 {serial}；{self.source_description}'

    def ensure_stream(self) -> bool:
        """Keep one persistent screenrecord alive; returns whether a stream is usable."""
        if self.config.capture_backend != 'stream':
            return False
        if self.stream and not self.stream.alive:
            self.stream.stop()
            self.stream = None
        if self.stream:
            return True
        try:
            selected = select_display(self.config, self.display_state())
            self.last_display = selected
            self.stream = AdbScreenStream(self.config.adb_executable, self.config.adb_serial,
                                          selected.physical_id)
            self.stream.start()
            return True
        except Exception:
            self.stream = None
            return False

    def screenshot(self) -> np.ndarray:
        if self.stream and not self.stream.alive:
            # screenrecord 有默认时长上限，结束后自动重启，避免画面静止。
            self.stream.stop()
            self.stream = None
        if self.stream is None:
            self.ensure_stream()
        if self.stream:
            try:
                image = self.stream.read()
                self.last_display = self.last_display or select_display(self.config, self.display_state())
                return image
            except RuntimeError:
                self.stream.stop()
                self.stream = None
        self.last_display = None
        for attempt in range(2):
            selected = select_display(self.config, self.display_state())
            try:
                raw = self.command('exec-out', 'screencap', '-p', '-d', selected.physical_id)
                marker = raw.find(b'\x89PNG\r\n\x1a\n')
                image = cv2.imdecode(np.frombuffer(raw[marker:], dtype=np.uint8), cv2.IMREAD_COLOR) if marker >= 0 else None
                if image is None:
                    raise RuntimeError('截图失败，显示器可能已重建；请重新获取截图或刷新截图来源')
                self.last_display = selected
                return image
            except RuntimeError:
                if attempt or self.config.capture_mode == 'manual':
                    raise
        raise RuntimeError('截图失败')

    def __del__(self):
        try:
            if self.stream:
                self.stream.stop()
        except Exception:
            pass

    def tap(self, x: int, y: int, verify: bool = True):
        """Tap on the display the last screenshot came from.

        ``verify`` re-reads the display state to detect a focus switch since the
        screenshot. Callers that tap right after a fresh capture can skip it: the
        coordinates belong to that capture's display, so ``input -d`` on it is still
        the correct target, and skipping saves three dumpsys round trips per click.
        """
        if not self.last_display or self.last_display.logical_id is None:
            raise RuntimeError('无法确定截图对应的触控显示器，请重新截图后再试')
        selected = self.last_display
        if verify:
            selected = select_display(self.config, self.display_state())
            if (selected.physical_id, selected.logical_id) != (self.last_display.physical_id, self.last_display.logical_id):
                raise RuntimeError('前台显示器已变化，本次点击已取消，请重新截图识别')
        self.command('shell', 'input', '-d', str(selected.logical_id), 'tap', str(x), str(y))
