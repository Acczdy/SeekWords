# 构建与发布

建议使用 Python 3.12。Windows、Linux、macOS 分别在本机或对应的 GitHub Actions 运行器上构建；PyInstaller 不能跨系统生成独立程序。

```bash
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -p "test_*.py"
python scripts/build.py
```

Windows 产物为 `dist/SeekWords.exe`。Linux 产物为 `dist/SeekWords`。macOS 产物为 `dist/SeekWords.app`。`scripts/build.py` 使用同一套入口、图标与内置解析组件。构建所需的 PyInstaller、Windows 的 pywin32、macOS 的 PyObjC 由 `requirements-build.txt` 安装；源码运行时无需这些构建依赖。

在对应系统构建后运行 `python scripts/package.py windows-x64`、`linux-x64`、`macos-x64` 或 `macos-arm64`。发布包输出到 `release/`。macOS 使用 `ditto` 打包 `.app`，保留可执行位和资源属性。

推送 `v1.1` 等 `v*` 标签会触发四个平台的构建与测试；四个任务都成功后自动创建 GitHub Release 并上传文件。发布源码由 GitHub 根据该标签自动提供。Linux 图形界面截图使用 `xvfb-run` 和仅含虚构资料的临时目录生成。

Windows 本地构建建议使用独立虚拟环境，避免全局 Python 包冲突。任何系统的构建产物都需在该系统上完成运行验证；CI 编译成功不等于所有发行版或 macOS 版本均已手工验证。
