# Third-party notices

VisionFlow includes or depends on the following third-party projects. Their licenses remain applicable to their respective components and are not replaced by the VisionFlow license.

## MaaFramework

- Project: https://github.com/MaaXYZ/MaaFramework
- Version used by this project: `5.12.2`
- License: GNU Lesser General Public License v3.0 (LGPL-3.0)
- Usage: image template matching, OCR, recognition resources and task execution.

The MaaFramework Python package contains its own `LICENSE.md`. Distributed builds should keep the MaaFramework files and license notices in the application directory. The source code for the version used by VisionFlow is available from the upstream project.

## MaaAgentBinary

`MaaFramework` declares `MaaAgentBinary` as a package dependency. Its package metadata contains a separate GNU Affero General Public License v3.0 (AGPL-3.0) license notice.

VisionFlow does not intentionally use the Android `minicap` or `maatouch` assets from this package in its current custom-controller build. If those assets are included in a future release, the corresponding AGPL-3.0 notice and source obligations must be reviewed for that release.

## OCR model assets

The files under `maa-assets/model/ocr/` were obtained from the MaaCommonAssets / PP-OCR-related resources:

- Project: https://github.com/MaaXYZ/MaaCommonAssets
- Files: `det.onnx`, `rec.onnx`, `keys.txt`

The exact license and attribution for the selected model files must be retained from the upstream resource revision. Model files can have terms different from the MaaFramework source code; verify the upstream `LICENSE` or `NOTICE` files before publishing a binary release.

## PySide6

- Project: https://doc.qt.io/qtforpython/
- License options: LGPL-3.0, GPL-3.0, or commercial license, depending on the distribution.

VisionFlow uses the PySide6 Python bindings and distributes the application as an onedir PyInstaller build. PySide6 license notices must remain available to users.

## NumPy

- Project: https://numpy.org/
- License: BSD-style license.

## OpenCV

- Project: https://opencv.org/
- License: Apache License 2.0, with notices for included third-party components where applicable.

## PyAV

- Project: https://github.com/PyAV-Org/PyAV
- License: BSD-3-Clause.

PyAV also uses FFmpeg components. FFmpeg licensing and build configuration notices should be checked for the exact PyAV binary distribution used in a release.

## PyInstaller

- Project: https://github.com/pyinstaller/pyinstaller
- License: GPL with the PyInstaller Bootloader Exception.

## ONNX Runtime

MaaFramework distributes an ONNX Runtime-based native component for OCR and related inference features. The license and notices for the exact MaaFramework binary distribution remain applicable.

## Android Debug Bridge

VisionFlow does not redistribute Android SDK Platform-Tools or a vendor emulator. Users must provide a compatible `adb.exe` and comply with the license terms of the Android SDK Platform-Tools and their emulator vendor.

## Trademarks and third-party content

Android, MuMu, MaaFramework and other product names are trademarks of their respective owners. VisionFlow is an independent third-party project and is not endorsed by those owners.

The repository should not include screenshots, game assets, emulator binaries, personal device configuration, or other content that the project author does not have permission to redistribute.
