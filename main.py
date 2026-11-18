from __future__ import annotations
import argparse
import json
import sys
import traceback
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from studio.window import MainWindow

def main():
    parser = argparse.ArgumentParser(description='VisionFlow · Android 视觉自动化工作台')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-test', type=Path)
    parser.add_argument('--capture-ui', type=Path)
    parser.add_argument('--ocr-smoke-test', type=Path)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('VisionFlow')
    app.setOrganizationName('VisionFlow')
    app.setFont(QFont('Microsoft YaHei UI', 9))
    def unhandled(exc_type, value, tb):
        detail = ''.join(traceback.format_exception(exc_type, value, tb))
        if sys.stderr:
            sys.stderr.write(detail)
        QMessageBox.critical(None, '程序错误', f'{value}\n\n请保存操作信息并反馈。')
    sys.excepthook = unhandled
    window = MainWindow(demo=args.demo or bool(args.smoke_test) or bool(args.ocr_smoke_test))
    window.show()
    if args.ocr_smoke_test:
        def ocr_smoke():
            try:
                import time
                from studio.models import decode_image
                frame = decode_image(window.project.reference)
                started = time.perf_counter()
                result = window.ocr.recognize(frame, [400, 300, 300, 100], frame_key=1)
                args.ocr_smoke_test.write_text(json.dumps({'status': 'passed', 'elapsed_seconds': round(time.perf_counter() - started, 3), 'items': [item.__dict__ for item in result]}, ensure_ascii=False, indent=2), encoding='utf-8')
                app.exit(0)
            except Exception:
                args.ocr_smoke_test.write_text(json.dumps({'status': 'failed', 'error': traceback.format_exc()}, ensure_ascii=False, indent=2), encoding='utf-8')
                app.exit(1)
        QTimer.singleShot(800, ocr_smoke)
    if args.smoke_test:
        def smoke():
            try:
                from studio.selftest import run_smoke
                report = run_smoke(window, args.smoke_test.parent)
                if args.capture_ui:
                    window.grab().save(str(args.capture_ui))
                args.smoke_test.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                window.dirty = False
                app.exit(0)
            except Exception:
                args.smoke_test.write_text(json.dumps({'status':'failed', 'error':traceback.format_exc()}, ensure_ascii=False, indent=2), encoding='utf-8')
                window.dirty = False
                app.exit(1)
        QTimer.singleShot(800, smoke)
    return app.exec()

if __name__ == '__main__':
    raise SystemExit(main())
