"""图片读取、压缩、感知哈希与“同图”比对。"""

from __future__ import annotations

import io
import re
import base64
import asyncio
import binascii
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from PIL import Image, ImageOps, ImageStat, ImageChops, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 30_000_000
MAX_UPLOAD_BYTES = 500_000


class ImageError(ValueError):
    """可以直接回给用户的图片错误。"""


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageError("这张图片无法读取，请换 JPG / PNG / WebP 再试。") from exc


def validate(raw: bytes) -> None:
    if not raw:
        raise ImageError("图片内容为空。")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageError("图片需小于 10 MB。")
    image = _open(raw)
    if image.width * image.height > MAX_PIXELS:
        raise ImageError("图片像素过大，请缩小后再发。")
    if min(image.size) < 8:
        raise ImageError("图片尺寸过小，无法比对。")


def prepare_upload(raw: bytes) -> bytes:
    """把图片压到 500 KB 内，同时尽量保留原图质量。"""
    if len(raw) <= MAX_UPLOAD_BYTES:
        return raw
    image = _open(raw)
    frame = ImageOps.exif_transpose(image).convert("RGBA")
    frame.thumbnail((1600, 1600))
    rgb = Image.new("RGB", frame.size, "white")
    rgb.paste(frame, mask=frame.getchannel("A"))
    while True:
        for quality in (88, 72, 56):
            output = io.BytesIO()
            rgb.save(output, format="JPEG", quality=quality, optimize=True)
            if len(output.getvalue()) <= MAX_UPLOAD_BYTES:
                return output.getvalue()
        rgb = rgb.resize(
            (max(1, int(rgb.width * 0.82)), max(1, int(rgb.height * 0.82))),
            Image.Resampling.LANCZOS,
        )


def normalize_for_detection(raw: bytes) -> bytes:
    """统一成小尺寸 JPEG 供感知哈希使用（不改变原图）。"""
    image = _open(raw)
    frame = ImageOps.exif_transpose(image).convert("RGBA")
    frame.thumbnail((1024, 1024))
    rgb = Image.new("RGB", frame.size, "white")
    rgb.paste(frame, mask=frame.getchannel("A"))
    output = io.BytesIO()
    rgb.save(output, format="JPEG", quality=88)
    return output.getvalue()


def dhash(raw: bytes) -> str:
    """64 位差值哈希，用于本地缓存命中判断。"""
    image = _open(raw).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = image.tobytes()
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return f"{bits:016x}"


def hamming(left: str, right: str, limit: int = 8) -> int:
    try:
        value = int(left, 16) ^ int(right, 16)
    except (TypeError, ValueError):
        return 64
    if value == 0:
        return 0
    count = value.bit_count()
    return count if count <= limit else 64


def same_image(original: bytes, candidate: bytes) -> bool:
    """判断两张图是否基本为同一张。

    先看字节、长宽比，再比较缩略图的颜色差和感知哈希；三者都通过才认定同图。
    对裁剪、缩放、轻微压缩有容忍度，但对不同图片保持保守。
    """
    if not original or not candidate:
        return False
    if original == candidate:
        return True
    try:
        with Image.open(io.BytesIO(original)) as first, Image.open(io.BytesIO(candidate)) as second:
            if any(
                img.width * img.height > MAX_PIXELS or min(img.size) < 8
                for img in (first, second)
            ):
                return False
            ratio_a = first.width / first.height
            ratio_b = second.width / second.height
            if abs(ratio_a / ratio_b - 1) > 0.03:
                return False
            a = first.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            b = second.convert("RGB").resize((32, 32), Image.Resampling.LANCZOS)
            diff = ImageStat.Stat(ImageChops.difference(a, b)).mean
            if max(diff) > 8.5:
                return False
            hashes = []
            for image in (a, b):
                gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                pixels = gray.tobytes()
                hashes.append(
                    [
                        pixels[y * 9 + x] > pixels[y * 9 + x + 1]
                        for y in range(8)
                        for x in range(8)
                    ]
                )
            return sum(x != y for x, y in zip(hashes[0], hashes[1])) <= 6
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return False


def _decode_base64(value: str) -> bytes:
    payload = value[len("base64://") :]
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageError("图片数据无效。") from exc


async def download_image(url: str, proxy: str | None, timeout: float = 12) -> bytes:
    safe = "".join(url.split())
    if not re.match(r"https?://", safe, re.I):
        raise ImageError("图片地址无效。")
    try:
        async with httpx.AsyncClient(
            proxy=proxy, timeout=timeout, follow_redirects=True, trust_env=False
        ) as client:
            async with client.stream("GET", safe) as response:
                response.raise_for_status()
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_IMAGE_BYTES:
                    raise ImageError("图片需小于 10 MB。")
                buf = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(buf) + len(chunk) > MAX_IMAGE_BYTES:
                        raise ImageError("图片需小于 10 MB。")
                    buf.extend(chunk)
                return bytes(buf)
    except ImageError:
        raise
    except httpx.HTTPError as exc:
        raise ImageError("图片下载失败，可能已过期，请重新发送。") from exc


async def load_source(source: str, proxy: str | None, timeout: float = 12) -> bytes:
    """读取 Event.image / image_list 中的值：URL、base64、file:// 或本地路径。"""
    value = source.strip()
    if not value:
        raise ImageError("图片内容为空。")
    if value.startswith("base64://"):
        raw = _decode_base64(value)
    elif value.startswith(("http://", "https://")):
        raw = await download_image(value, proxy, timeout)
    elif value.startswith("file://"):
        path = Path(unquote(urlsplit(value).path))
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise ImageError("图片文件读取失败。") from exc
    else:
        path = Path(value)
        if path.is_file():
            try:
                raw = await asyncio.to_thread(path.read_bytes)
            except OSError as exc:
                raise ImageError("图片文件读取失败。") from exc
        else:
            raise ImageError("图片内容无法读取。")
    validate(raw)
    return raw
