"""8 位灰度 PNG，不依赖 Pillow。"""
import struct
import zlib


def encode_gray8(width: int, height: int, pixels: bytes) -> bytes:
    if width <= 0 or height <= 0 or len(pixels) != width * height:
        raise ValueError("PNG 尺寸与像素数不一致")

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    raw = bytearray()
    for row in range(height):
        raw.append(0)
        raw.extend(pixels[row * width:(row + 1) * width])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def decode_gray8(blob: bytes) -> tuple[int, int, bytes]:
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG")
    pos = 8
    width = height = None
    idat = bytearray()
    while pos < len(blob):
        length = struct.unpack(">I", blob[pos:pos + 4])[0]
        tag = blob[pos + 4:pos + 8]
        payload = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or color != 0:
                raise ValueError("只接受 8 位灰度 PNG")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None:
        raise ValueError("PNG 缺少 IHDR")
    raw = zlib.decompress(bytes(idat))
    pixels = bytearray()
    stride = width + 1
    for row in range(height):
        if raw[row * stride] != 0:
            raise ValueError("PNG 使用了过滤")
        pixels.extend(raw[row * stride + 1:(row + 1) * stride])
    return width, height, bytes(pixels)
