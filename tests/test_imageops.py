import io

import pytest
from PIL import Image
from shadow_lens.imageops import (
    ImageError,
    dhash,
    hamming,
    validate,
    same_image,
    prepare_upload,
)


def _png(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def test_same_image_tolerates_recompression() -> None:
    png = _png((120, 80, 40))
    jpeg = _jpeg((120, 80, 40))
    assert same_image(png, jpeg)


def test_different_colors_are_not_same_image() -> None:
    assert not same_image(_png((200, 10, 10)), _png((10, 10, 200)))


def test_dhash_is_stable_and_hex() -> None:
    raw = _png((30, 160, 90))
    first = dhash(raw)
    assert len(first) == 16
    assert hamming(first, dhash(raw)) == 0
    assert hamming(first, "0000000000000000", limit=64) <= 64


def test_validate_rejects_tiny_images() -> None:
    with pytest.raises(ImageError):
        validate(_png((0, 0, 0), (4, 4)))


def test_prepare_upload_keeps_small_file() -> None:
    raw = _png((10, 20, 30))
    assert prepare_upload(raw) == raw
