<!-- markdownlint-disable MD033 MD041 -->

<div align="center">

# VisionFlow

Android visual automation workspace. Image recognition + OCR + rule workflows for reducing repetitive operations.

Powered by [MaaFramework](https://github.com/MaaXYZ/MaaFramework)

</div>

<p align="center">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg"></a>
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-0078D6.svg">
  <img alt="python" src="https://img.shields.io/badge/Python-3.12-3776AB.svg">
</p>

## Notice

The project is under active development and has currently been verified on Windows with the MuMu Android emulator.

- Other Android emulators and physical Android devices should work through standard ADB interfaces, but have not been individually tested.
- Automatic execution sends real taps or input events to the connected device. Keep automatic execution disabled until recognition results have been verified.
- Keep the device resolution and orientation fixed while a workflow is running.
- VisionFlow is an independent third-party project and is not affiliated with, authorized by, or commercially associated with any emulator vendor, game publisher, or the MaaFramework team.

## Features

### Recognition

- Image template matching through MaaFramework `TemplateMatch`
- OCR through MaaFramework and the bundled Chinese OCR models
- Configurable search regions and single rectangular OCR regions
- Match thresholds, OCR confidence thresholds, contains-text matching, and exact matching
- Recognition boxes, scores, and active-rule highlighting

### Rule workflows

- Image recognition
- Text recognition
- Always-run actions
- Delayed actions
- Termination nodes
- Rule ordering and batch editing
- `Then / Otherwise` branches
- Tree drag-and-drop and nested conditions
- Once-per-cycle, reappear, and interval triggers

### Actions

- Click the center of a template
- Click the center of a recognized text box
- Click absolute coordinates
- Multi-point clicks with intervals and repetitions
- Continuous monitoring and single-step execution
- Automatic execution or recognition-only preview
- ADB taps, swipes, key events, and text input

### Live preview

- Persistent H.264 screen streaming
- Latest-frame-only buffering to avoid processing stale frames
- Target frame-rate limiting to reduce CPU usage
- Automatic fallback to single-frame ADB screenshots
- Preview freeze during frame testing, with canvas click-to-resume

## Getting started

### Windows release

Download the complete `VisionFlow` folder from Releases and run:

```text
VisionFlow.exe
```

Do not copy only the executable. The following directories must remain beside it:

```text
_internal/
maa-assets/
```

### Run from source

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

The virtual environment does not need to be activated.

### Connect a device

1. Start an Android emulator, or connect an Android device through USB or the network.
2. Select the device's `adb.exe` under **Device Settings**.
3. Enter the device address, for example `127.0.0.1:5555`.
4. Click **Connect Device**.
5. Click **Capture Screenshot** to confirm the frame and resolution.
6. Draw a template region, search region, or OCR region.
7. Click **Test Current Frame** and verify the boxes, scores, and rule order.
8. Enable **Automatic Execution** only after the workflow has been verified.

### Save workflows

New workflows use the following extension by default:

```text
*.vision.json
```

Legacy `.mumu.json` files and the following legacy format identifiers remain readable:

```text
mumu-rule-studio
mumu-auto
```

Use `environment.example.json` as a template for the untracked local file `environment.local.json`.

## Rule model

VisionFlow supports these rule modes:

- **Image recognition**: run an action when a template is found; otherwise wait.
- **Text recognition**: recognize target text inside one configured rectangle.
- **Always run**: execute a fixed-position action without recognition.
- **Delay**: wait for a configured duration before continuing.
- **Stop**: return to the beginning of the workflow.

Rules can be arranged into condition trees:

```text
Login button found
├── Then: click Login
└── Otherwise: wait 1 second
```

Each rule can also configure:

- Contains-text or exact-text matching
- Image match threshold
- OCR confidence threshold
- Search and OCR regions
- Click offsets
- Multiple points, click intervals, and repetitions
- Once-per-cycle, reappear, or interval triggers

## Development

Run unit tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run the source smoke test:

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test artifacts/source-smoke.json --capture-ui artifacts/gui-source.png
```

Build the Windows release:

```powershell
.\.venv\Scripts\python.exe build.py
```

Build output:

```text
dist/VisionFlow/
```

The smoke test covers recognition, condition branches, save/load round trips, single-step execution, monitoring cycles, GUI layout, and read-only device capture. It does not send clicks to a real device.

## Repository layout

```text
studio/                 Application source
maa-assets/             MaaFramework pipeline and OCR assets
tests/                  Unit tests
main.py                 Application entry point
build.py                PyInstaller build script
VisionFlow.spec         PyInstaller specification
requirements.txt        Runtime dependencies
requirements-build.txt  Build dependencies
```

## License

VisionFlow's own source code is licensed under [AGPL-3.0](LICENSE).

Third-party components and OCR models remain subject to their respective licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Android, MuMu, MaaFramework, and other product names are trademarks of their respective owners.

## Acknowledgements

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) - image recognition, OCR, and task execution
- [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) - OCR-related assets
- [Qt for Python](https://doc.qt.io/qtforpython/) - desktop GUI
- [PyAV](https://github.com/PyAV-Org/PyAV) - H.264 frame decoding
- [OpenCV](https://opencv.org/) - image processing
