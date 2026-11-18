"""Build with a clean DLL search path, then reject unrelated runtime binaries."""
from pathlib import Path
import ast
import json
import os
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent
base_python = Path(sys.base_prefix)
windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
environment = os.environ.copy()
environment['PATH'] = os.pathsep.join(str(p) for p in [Path(sys.executable).parent, base_python, windows/'System32', windows, windows/'System32'/'Wbem'])
for key in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
    environment.pop(key, None)
command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--windowed', '--onedir', '--name', 'VisionFlow']
for module in ['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets']:
    command += ['--exclude-module', module]
command += ['--collect-all', 'av', '--collect-all', 'maa', '--add-data', 'maa-assets;maa-assets']
command += ['main.py']
subprocess.run(command, cwd=root, env=environment, check=True)
# Check that no DLL was collected from the invoking host's unrelated tools.
toc = ast.literal_eval((root/'build/VisionFlow/Analysis-00.toc').read_text(encoding='utf-8'))
allowed_roots = [root.resolve(), base_python.resolve(), windows.resolve()]
unexpected = []
def inspect(value):
    if isinstance(value, (list, tuple)):
        if len(value) == 3 and value[2] == 'BINARY' and isinstance(value[1], str):
            source = Path(value[1]).resolve()
            if not any(source.is_relative_to(allowed) for allowed in allowed_roots):
                unexpected.append(str(source))
        for item in value:
            inspect(item)
inspect(toc)
if unexpected:
    raise RuntimeError('Unexpected external DLL sources: '+repr(unexpected))
output = root/'dist/VisionFlow'
shutil.copy2(root/'README.md', output/'README.md')
if (root/'environment.local.json').exists():
    shutil.copy2(root/'environment.local.json', output/'environment.local.json')
(root/'artifacts/build-audit.json').write_text(json.dumps({'clean_path':environment['PATH'], 'unexpected_binary_sources':unexpected, 'status':'passed'}, indent=2),encoding='utf-8')
print('Build completed:', output/'VisionFlow.exe')
