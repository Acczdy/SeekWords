"""Bundled, CPU-only Chinese/English OCR using local RapidOCR ONNX models."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MODEL_HASHES = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}
_ENGINE = None


def _model_dir():
    package = importlib.util.find_spec("rapidocr")
    return Path(package.origin).parent / "models" if package and package.origin else None


def _verify_models():
    model_dir = _model_dir()
    if not model_dir or importlib.util.find_spec("onnxruntime") is None:
        raise RuntimeError("缺少内置 OCR 组件；源码运行请安装 requirements-ocr.txt")
    for name, expected in MODEL_HASHES.items():
        path = model_dir / name
        if not path.is_file():
            raise RuntimeError(f"内置 OCR 模型缺失：{name}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"内置 OCR 模型校验失败：{name}")
    return model_dir


def available_engine():
    try:
        _verify_models()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, "", str(exc)
    return "rapidocr", "中英混合", ""


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        model_dir = _verify_models()
        from rapidocr import RapidOCR

        # Explicit local paths bypass RapidOCR's default model downloader.
        _ENGINE = RapidOCR(params={
            "Global.log_level": "error",
            "Det.model_path": str(model_dir / "PP-OCRv6_det_small.onnx"),
            "Cls.model_path": str(model_dir / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
            "Rec.model_path": str(model_dir / "PP-OCRv6_rec_small.onnx"),
        })
    return _ENGINE


def ocr_lines(path, command="rapidocr", language=""):
    from .content import ContentReadError
    try:
        result = _get_engine()(Path(path))
        texts = result.txts if result.txts is not None else ()
        boxes = result.boxes if result.boxes is not None else ()
        for number, (text, box) in enumerate(zip(texts, boxes), 1):
            text = str(text).strip()
            if not text:
                continue
            left = round(min(point[0] for point in box))
            top = round(min(point[1] for point in box))
            right = round(max(point[0] for point in box))
            bottom = round(max(point[1] for point in box))
            yield f"图片第 {number} 处 · 像素 ({left},{top})–({right},{bottom})", text
    except ContentReadError:
        raise
    except Exception as exc:
        raise ContentReadError("图片 OCR 无法完成：" + str(exc)[:220]) from exc
