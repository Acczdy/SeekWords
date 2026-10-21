# 内置 OCR 组件与模型来源

SeekWords 的图片 OCR 使用 RapidOCR 3.10.0、ONNX Runtime 1.23.2 的 CPU 推理。安装包包含 RapidOCR 官方 wheel 自带的 PP-OCRv6 检测/识别模型及方向分类模型，识别中英文。源代码模式需运行 `python -m pip install -r requirements-ocr.txt`；安装包用户无需单独安装 OCR 组件。运行时只读取本机图片和随包模型，显式传入本地模型路径，不下载模型。

| 内置模型文件 | SHA-256 |
| --- | --- |
| `PP-OCRv6_det_small.onnx` | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` |
| `PP-OCRv6_rec_small.onnx` | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` |

RapidOCR 代码与由 PaddleOCR 转换的模型按 Apache License 2.0 分发，见 [RapidOCR 仓库](https://github.com/RapidAI/RapidOCR)及[模型授权声明](https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/)。本仓库保留[完整 Apache 许可证](third_party/APACHE-2.0.txt)与[模型归属声明](third_party/RapidOCR-MODEL_LICENSES.md)。ONNX Runtime 按 MIT License 分发，见 [ONNX Runtime 仓库](https://github.com/microsoft/onnxruntime)。这些上游项目独立于 SeekWords；其他 OCR 依赖及其许可证以随包 Python 发行包的元数据为准。

模型在程序启动扫描时先检查 SHA-256；缺失或校验失败时 OCR 不会运行，也不会下载替换。图片识别发生在本机独立子进程中，超过配置的读取时间会被终止并记录为未完整读取。速度随硬件和图片内容变化，不承诺固定耗时。
