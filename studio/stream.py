from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import av
import numpy as np


class AdbScreenStream:
    """Read the latest Android frame from one persistent screenrecord process."""

    def __init__(self, adb_path: str, serial: str, display_id: str):
        self.adb_path = str(adb_path)
        self.serial = str(serial)
        self.display_id = str(display_id)
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.condition = threading.Condition()
        self.latest: np.ndarray | None = None
        self.sequence = 0
        self.error: Exception | None = None
        self.finished = False
        self.target_fps = 30.0
        self.stopping = False

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        args = [self.adb_path, '-s', self.serial, 'exec-out', 'screenrecord',
                '--output-format=h264', '--bit-rate', '8000000']
        if self.display_id:
            args.extend(['--display-id', self.display_id])
        args.append('-')
        self.stopping = False
        self.error = None
        self.latest = None
        self.sequence = 0
        self.error = None
        self.finished = False
        self._next_convert = 0.0
        self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         bufsize=0,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.thread = threading.Thread(target=self._decode, name='visionflow-screen-stream', daemon=True)
        self.thread.start()

    def _decode(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        try:
            decoder = av.CodecContext.create('h264', 'r')
            while not self.stopping:
                data = process.stdout.read(65536)
                if not data:
                    break
                for packet in decoder.parse(data):
                    for frame in decoder.decode(packet):
                        now = time.monotonic()
                        if now < self._next_convert:
                            continue
                        self._next_convert = now + (1.0 / self.target_fps)
                        image = frame.to_ndarray(format='bgr24')
                        with self.condition:
                            self.latest = image
                            self.sequence += 1
                            self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()
        finally:
            with self.condition:
                self.finished = True
                self.condition.notify_all()

    @property
    def alive(self) -> bool:
        return not self.finished and not self.stopping

    def read(self, timeout: float = 3.0) -> np.ndarray:
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.latest is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = f'持续截图无帧：{self.error}' if self.error else '持续截图超时'
                    raise RuntimeError(detail)
                self.condition.wait(remaining)
            return self.latest

    def snapshot(self) -> tuple[np.ndarray | None, int]:
        with self.condition:
            return self.latest, self.sequence

    def stop(self) -> None:
        self.stopping = True
        process = self.process
        self.process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        with self.condition:
            self.condition.notify_all()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
