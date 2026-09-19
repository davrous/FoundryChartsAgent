"""Render, check and package the original sideload artwork using existing dependencies."""

import json
import struct
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import vl_convert as vlc

SIGNATURE = b"\x89PNG\r\n\x1a\n"
BACKGROUND = bytes.fromhex("2735A6")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def paeth(left: int, above: int, upper_left: int) -> int:
    prediction = left + above - upper_left
    distances = [abs(prediction - value) for value in (left, above, upper_left)]
    return (left, above, upper_left)[distances.index(min(distances))]


def decode_rgba(data: bytes) -> tuple[int, int, bytes]:
    require(data.startswith(SIGNATURE), "Expected a PNG image")
    offset, header, compressed = 8, b"", bytearray()
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        checksum = struct.unpack_from(">I", data, offset + 8 + length)[0]
        require(zlib.crc32(kind + body) == checksum, "Invalid PNG chunk checksum")
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            compressed.extend(body)
        offset += length + 12
    require(len(header) == 13, "Missing PNG header")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    require((depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0),
            "Expected non-interlaced, 8-bit RGBA from vl-convert")
    stride = width * 4
    raw = zlib.decompress(compressed)
    require(len(raw) == (stride + 1) * height, "Unexpected PNG data length")
    pixels = bytearray(stride * height)
    for y in range(height):
        filter_type = raw[y * (stride + 1)]
        require(filter_type <= 4, "Unsupported PNG row filter")
        for x in range(stride):
            index = y * stride + x
            left = pixels[index - 4] if x >= 4 else 0
            above = pixels[index - stride] if y else 0
            upper_left = pixels[index - stride - 4] if y and x >= 4 else 0
            predictors = (0, left, above, (left + above) // 2, paeth(left, above, upper_left))
            pixels[index] = (raw[y * (stride + 1) + x + 1] + predictors[filter_type]) & 255
    return width, height, bytes(pixels)


def encode_rgba(width: int, height: int, pixels: bytes) -> bytes:
    require(len(pixels) == width * height * 4, "Unexpected RGBA pixel count")

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    stride = width * 4
    rows = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))
    return (
        SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def white_silhouette(data: bytes) -> bytes:
    width, height, pixels = decode_rgba(data)
    white = bytearray(pixels)
    # Preserve coverage, avoiding premultiplied-alpha rounding in edge RGB values.
    for index in range(0, len(white), 4):
        white[index:index + 3] = b"\xff\xff\xff"
    return encode_rgba(width, height, bytes(white))


def validate_icons(color: bytes, outline: bytes) -> None:
    width, height, pixels = decode_rgba(color)
    require((width, height) == (192, 192), "Color icon must be 192 x 192")
    require(all(alpha == 255 for alpha in pixels[3::4]), "Color icon must be fully opaque")
    foreground = []
    for index in range(0, len(pixels), 4):
        if pixels[index:index + 3] != BACKGROUND:
            foreground.append(((index // 4) % width, (index // 4) // width))
    require(bool(foreground), "Color icon must contain a logo")
    require(all(48 <= x < 144 and 48 <= y < 144 for x, y in foreground),
            "Logo must fit inside the centered 96 x 96 safe area on the flat indigo background")

    width, height, pixels = decode_rgba(outline)
    require((width, height) == (32, 32), "Outline icon must be 32 x 32")
    visible = [pixels[index:index + 4] for index in range(0, len(pixels), 4) if pixels[index + 3]]
    require(0 < len(visible) < width * height, "Outline must have a visible glyph and transparency")
    require(all(pixel[:3] == b"\xff\xff\xff" for pixel in visible), "Outline must be pure white")
    require(any(pixel[3] == 255 for pixel in visible), "Outline must not be entirely faded")
    coordinates = [(index % width, index // width) for index, alpha in enumerate(pixels[3::4]) if alpha]
    bounds = (min(x for x, _ in coordinates), min(y for _, y in coordinates),
              max(x for x, _ in coordinates), max(y for _, y in coordinates))
    require(bounds == (0, 0, 31, 31), "Outline glyph must not have additional outer padding")


def build_package(root: Path) -> Path:
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest["icons"] == {
        "color": "default-color-icon.png", "outline": "default-outline-icon.png",
    }, "Manifest must reference the generated icon filenames")
    require(manifest["accentColor"].upper() == "#2735A6", "Accent must match the icon background")
    sources = root / "icon-sources"
    color = vlc.svg_to_png((sources / "color.svg").read_text())
    outline = white_silhouette(vlc.svg_to_png((sources / "outline.svg").read_text()))
    validate_icons(color, outline)

    files = {"manifest.json": manifest_bytes, "default-color-icon.png": color,
             "default-outline-icon.png": outline}
    for name, image in files.items():
        if name != "manifest.json":
            (root / name).write_bytes(image)
    build = root / "build"
    build.mkdir(exist_ok=True)
    package_path = build / "foundry-charts.zip"
    with ZipFile(package_path, "w", ZIP_DEFLATED) as package:
        for name, data in files.items():
            package.writestr(name, data)
    with ZipFile(package_path) as package:
        require(package.testzip() is None, "ZIP integrity check failed")
        require(set(package.namelist()) == set(files), "Unexpected package entries")
    return package_path


if __name__ == "__main__":
    package_path = build_package(Path(__file__).resolve().parent)
    print(f"Icon pixel checks and three-file ZIP integrity passed: {package_path}")
