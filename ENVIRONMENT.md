# Android visual automation development environment

Prepared on 2026-11-17 in `E:\0\Tools\r3`.

## Installed environment

- Python 3.12.10, Windows x64, in `.venv`.
- PySide6 6.12.0.
- OpenCV headless 4.14.0.94.
- NumPy 2.5.5.
- Exact resolved packages are pinned in `requirements.txt`.
- `requirements.in` lists direct dependency version ranges for future upgrades.
- VS Code default interpreter points to `.venv/Scripts/python.exe`.

Use the project interpreter directly; shell activation is optional. From this project directory:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip list
```

To recreate the environment with Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --only-binary=:all: -r requirements.txt
```

Install only one OpenCV package in this environment. This project uses `opencv-python-headless`; PySide6 provides the GUI.

## MuMu connection

- Manager: `E:\App\MuMuPlayer\nx_main\MuMuManager.exe`
- ADB: `E:\App\MuMuPlayer\nx_main\adb.exe`
- Currently running instance: index 0, Android 15.
- Observed endpoint: `127.0.0.1:16384`. Query the manager again if instances change.
- ADB connection and two consecutive in-memory PNG screenshot captures passed.
- Screenshot display ID: `4619827820427265280`; specify it explicitly with `screencap -p -d <display-id>`.
- Actual captured orientation: 1280 x 900; wm reports physical size 900 x 1280 and density 225.
- Android reports multiple displays. Omitting the display ID adds a warning to exec-out output before the PNG bytes.
- MuMu 12 renders the desktop and each running app on separate virtual displays. With the desktop focused: `local:4619827820427265280` (display 0, `mumuscreen000`) plus unused `mumuscreen006`/`mumuscreen007`. After launching a game, `mTopFocusedDisplayId` moves to the game display and that display shows the game, while display 0 still shows the launcher.
- The app therefore defaults to `capture_mode: auto` in `environment.local.json`. Auto mode picks the focused display, and falls back to the sole display running a non-launcher foreground app when MuMu keeps the desktop focused. `capture_mode: manual` pins `adb_display_id` to one physical display, which must be re-selected after the emulator rebuilds its displays.
- `screencap -p -d <physical-id>` takes the SurfaceFlinger display id, while `input -d <logical-id> tap` takes the logical (DisplayInfo) id. The app maps the two through `dumpsys display` viewports.

Inspect displays manually:

```powershell
& 'E:\App\MuMuPlayer\nx_main\adb.exe' -s 127.0.0.1:16384 shell "dumpsys SurfaceFlinger --display-id"
& 'E:\App\MuMuPlayer\nx_main\adb.exe' -s 127.0.0.1:16384 shell "dumpsys window | grep -E 'mTopFocusedDisplayId|mCurrentFocus|mFocusedApp'"
```

- Machine-specific paths are in `environment.local.json`.

```powershell
& 'E:\App\MuMuPlayer\nx_main\MuMuManager.exe' info --vmindex all
& 'E:\App\MuMuPlayer\nx_main\adb.exe' connect 127.0.0.1:16384
& 'E:\App\MuMuPlayer\nx_main\adb.exe' -s 127.0.0.1:16384 get-state
```

## Verification

Dependency consistency, Qt Windows widget rendering, template matching, and PNG encoding/decoding passed. Full diagnostics are in `environment-check.local.json`.

ADB screenshot verification, when successful, captures and decodes the image in memory without storing a game screenshot. Touch actions and the actual game workflow still need to be verified during application development. Verified on 2026-11-17: with 忍者必须死3 running, auto mode captured display 7 (`mumuscreen006`, `com.pandadastudio.ninjamustdie3`) and a single-step tap to that display's logical id was accepted by the device. VisionFlow GUI is available in dist/VisionFlow/VisionFlow.exe. See README.md for features and limits; build dependencies are pinned in requirements-build.txt.
