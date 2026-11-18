<!-- markdownlint-disable MD033 MD041 -->

<div align="center">

# VisionFlow

Android 视觉自动化工作台。图像识别 + OCR + 规则流程，减少重复操作。

Powered by [MaaFramework](https://github.com/MaaXYZ/MaaFramework)

</div>

<p align="center">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg"></a>
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-0078D6.svg">
  <img alt="python" src="https://img.shields.io/badge/Python-3.12-3776AB.svg">
</p>

## 使用须知

项目处于持续迭代阶段，当前已在 Windows + MuMu 模拟器环境中完成验证。

- 其他 Android 模拟器和真实 Android 设备：理论上支持标准 ADB 接口，尚未逐一实机验证。
- 开启 [自动执行] 才会向设备发送真实点击或输入。
- 请固定设备分辨率和方向，避免识别区域与点击坐标失效。

## 功能列表

### 识别能力

- 图像模板识别：MaaFramework `TemplateMatch`
- 文字识别：MaaFramework OCR + 中文 OCR 模型
- 指定搜索区域和单矩形文字识别区域
- 匹配阈值、OCR 置信度、包含文字和完全匹配
- 识别框、匹配分数和当前规则高亮

### 自动操作

- 模板中心点击
- 文字框中心点击
- 指定坐标点击
- 多点点击、点击间隔和重复次数
- 持续监测和单步执行
- 自动执行与只识别预览
- ADB `tap`、`swipe`、按键和文字输入

## 新手上路

### Windows

从 Releases 下载完整的 `VisionFlow` 文件夹，解压后运行：

```text
VisionFlow.exe
```

以下目录必须保留：

```text
_internal/
maa-assets/
```

### 源码运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

### 连接设备

1. 启动 Android 模拟器，或通过 USB / 网络连接 Android 设备。
2. 在「设备设置」中选择对应的 `adb.exe`。
3. 填写设备地址，例如 `127.0.0.1:5555`。
4. 点击「连接设备」。
5. 点击「获取截图」确认画面和分辨率。
6. 绘制模板、搜索区域或文字识别区域。
7. 点击「测试当前画面」，确认识别框、分数和规则顺序。
8. 确认无误后，再勾选「自动执行」并开始监测。

### 保存方案

方案默认保存为：

```text
*.vision.json
```

## 开发相关

运行单元测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

运行源码自检：

```powershell
.\.venv\Scripts\python.exe main.py --smoke-test artifacts/source-smoke.json --capture-ui artifacts/gui-source.png
```

构建 Windows 发布版：

```powershell
.\.venv\Scripts\python.exe build.py
```

构建结果：

```text
dist/VisionFlow/
```

自检不会向真实设备发送点击。

## 目录说明

```text
studio/                 主程序源码
maa-assets/             MaaFramework Pipeline 和 OCR 模型
tests/                  单元测试
main.py                 程序入口
build.py                PyInstaller 构建脚本
VisionFlow.spec         PyInstaller 配置
requirements.txt        运行依赖
requirements-build.txt  构建依赖
```

## 致谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) - 图像识别、OCR 和任务执行框架
- [MaaCommonAssets](https://github.com/MaaXYZ/MaaCommonAssets) - OCR 相关资源来源
- [Qt for Python](https://doc.qt.io/qtforpython/) - GUI 框架
- [PyAV](https://github.com/PyAV-Org/PyAV) - H.264 视频帧解码
- [OpenCV](https://opencv.org/) - 图像处理

## 许可证

VisionFlow 自有源码采用 [AGPL-3.0](LICENSE) 许可证。

第三方组件和 OCR 模型继续适用各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

<div align="center">

如果这个项目对你有帮助，欢迎 Star。

</div>
