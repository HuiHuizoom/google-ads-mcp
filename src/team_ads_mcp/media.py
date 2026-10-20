"""Local image inspection and bounded upload payloads; never automatic cropping."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import AdsError

MAX_IMAGE_BYTES = 5_120_000
IMAGE_RULES = {
    "MARKETING_IMAGE": (600, 314, 1.91),
    "SQUARE_MARKETING_IMAGE": (300, 300, 1.0),
    "PORTRAIT_MARKETING_IMAGE": (480, 600, 0.8),
    "LOGO": (128, 128, 1.0),
    "LANDSCAPE_LOGO": (512, 128, 4.0),
}


def compatible_image_fields(width, height):
    # Google's linking validation remains authoritative for rounding/tolerance.
    return [
        field
        for field, (min_w, min_h, ratio) in IMAGE_RULES.items()
        if width >= min_w and height >= min_h and abs(width / height / ratio - 1) <= 0.01
    ]


def image_payload(path: str, expected_hash: str | None = None):
    file = Path(path)
    if not file.is_absolute():
        raise AdsError("IMAGE_PATH_REQUIRED", "请提供本机图片的绝对路径")
    try:
        if not stat.S_ISREG(file.stat().st_mode):
            raise AdsError("IMAGE_FILE_REQUIRED", "路径必须指向普通图片文件")
        with file.open("rb") as fp:
            data = fp.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise AdsError("IMAGE_UNAVAILABLE", "无法读取指定的本机图片") from None
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise AdsError("IMAGE_SIZE_LIMIT", "图片必须非空且不超过 5,120,000 字节")
    sha = hashlib.sha256(data).hexdigest()
    if expected_hash is not None and sha != expected_hash:
        raise AdsError("IMAGE_CONTENT_CHANGED", "图片与确认的 sha256 不一致，请重新检查并预览")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                fmt, (width, height) = image.format, image.size
                if fmt not in {"PNG", "JPEG", "GIF"} or getattr(image, "is_animated", False):
                    raise AdsError("IMAGE_FORMAT_UNSUPPORTED", "仅支持静态 PNG、JPEG、GIF")
                if width * height > 40_000_000:
                    raise AdsError("IMAGE_DIMENSION_LIMIT", "图片超过 4000 万像素")
                image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise AdsError("INVALID_IMAGE", "图片损坏或尺寸无法安全解析") from None
    return {
        "path": str(file),
        "sha256": sha,
        "bytes": len(data),
        "format": fmt,
        "width": width,
        "height": height,
        "compatible_field_types": compatible_image_fields(width, height),
        "note": "仅本机格式、尺寸检查；不证明 Google 政策审核或关联校验通过，不裁剪原文件",
    }, data


def public_payload(value):
    """Keep complete operations private; show a digest instead of image base64."""
    if isinstance(value, list):
        return [public_payload(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key == "image_asset" and isinstance(child, dict) and "data" in child:
            raw = base64.b64decode(child["data"], validate=True)
            result[key] = {
                **{k: public_payload(v) for k, v in child.items() if k != "data"},
                "upload": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
            }
        elif key == "data" and isinstance(child, str) and child.startswith("{"):
            # Ledger events retain their JSON-string contract.
            try:
                result[key] = json.dumps(public_payload(json.loads(child)), ensure_ascii=False)
            except (ValueError, TypeError):
                result[key] = child
        else:
            result[key] = public_payload(child)
    return result
