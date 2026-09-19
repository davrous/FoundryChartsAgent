import json
import struct
import zlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from m365sideloadmanifest.build_package import (
    SIGNATURE,
    build_package,
    decode_rgba,
    encode_rgba,
    paeth,
    validate_icons,
    white_silhouette,
)

ROOT = Path(__file__).resolve().parents[1] / "m365sideloadmanifest"


def test_manifest_allows_only_the_production_chart_artifact_host():
    manifest = json.loads((ROOT / "manifest.json").read_bytes())
    assert manifest["validDomains"] == ["msdavrous.blob.core.windows.net"]


@pytest.mark.parametrize("filter_type", range(5))
def test_decode_all_png_filters(filter_type):
    width, height = 3, 2
    pixels = bytes((index * 37) % 256 for index in range(width * height * 4))
    stride = width * 4
    rows = bytearray()
    for y in range(height):
        rows.append(filter_type)
        for x in range(stride):
            index = y * stride + x
            left = pixels[index - 4] if x >= 4 else 0
            above = pixels[index - stride] if y else 0
            corner = pixels[index - stride - 4] if y and x >= 4 else 0
            predictors = (0, left, above, (left + above) // 2, paeth(left, above, corner))
            rows.append((pixels[index] - predictors[filter_type]) & 255)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    png = (SIGNATURE
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    assert decode_rgba(png) == (width, height, pixels)


def test_white_silhouette_preserves_every_alpha_value():
    pixels = bytes(channel for alpha in range(256) for channel in (254, 254, 254, alpha))
    normalized = white_silhouette(encode_rgba(16, 16, pixels))
    width, height, result = decode_rgba(normalized)
    assert (width, height) == (16, 16)
    assert result[3::4] == pixels[3::4]
    assert all(result[index:index + 3] == b"\xff\xff\xff" for index in range(0, len(result), 4))


def test_checked_in_icons_meet_pixel_requirements():
    validate_icons((ROOT / "default-color-icon.png").read_bytes(),
                   (ROOT / "default-outline-icon.png").read_bytes())


def test_reject_nonwhite_outline_pixel():
    color = (ROOT / "default-color-icon.png").read_bytes()
    width, height, pixels = decode_rgba((ROOT / "default-outline-icon.png").read_bytes())
    modified = bytearray(pixels)
    first_visible = next(index for index in range(0, len(pixels), 4) if pixels[index + 3])
    modified[first_visible] = 254
    with pytest.raises(ValueError, match="pure white"):
        validate_icons(color, encode_rgba(width, height, bytes(modified)))


def test_reject_outline_padding():
    color = (ROOT / "default-color-icon.png").read_bytes()
    width, height, pixels = decode_rgba((ROOT / "default-outline-icon.png").read_bytes())
    modified = bytearray(pixels)
    for y in range(height):
        for x in range(width):
            if x in (0, 31) or y in (0, 31):
                modified[(y * width + x) * 4 + 3] = 0
    with pytest.raises(ValueError, match="padding"):
        validate_icons(color, encode_rgba(width, height, bytes(modified)))


@pytest.mark.parametrize(("index", "value", "message"), [(3, 0, "opaque"), (0, 255, "safe area")])
def test_reject_color_transparency_or_unsafe_logo(index, value, message):
    width, height, pixels = decode_rgba((ROOT / "default-color-icon.png").read_bytes())
    modified = bytearray(pixels)
    modified[index] = value
    with pytest.raises(ValueError, match=message):
        validate_icons(encode_rgba(width, height, bytes(modified)),
                       (ROOT / "default-outline-icon.png").read_bytes())


def test_package_contains_only_manifest_and_matching_icons(tmp_path):
    (tmp_path / "manifest.json").write_bytes((ROOT / "manifest.json").read_bytes())
    sources = tmp_path / "icon-sources"
    sources.mkdir()
    for name in ("color.svg", "outline.svg"):
        (sources / name).write_bytes((ROOT / "icon-sources" / name).read_bytes())
    package_path = build_package(tmp_path)
    with ZipFile(package_path) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {
            "manifest.json", "default-color-icon.png", "default-outline-icon.png",
        }
        assert package.read("manifest.json") == (ROOT / "manifest.json").read_bytes()
        manifest = json.loads(package.read("manifest.json"))
        for filename in manifest["icons"].values():
            assert package.read(filename) == (ROOT / filename).read_bytes()
        assert manifest["version"] == json.loads((ROOT / "manifest.json").read_bytes())["version"]
