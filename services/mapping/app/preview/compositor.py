"""子图纹理拼成占据栅格。几何与混合公式移植自 robospace captureMapSnapshot。"""
import gzip
import math

import numpy as np

from ..geom import compose, js_round, yaw_of

MAX_LIVE_OCCUPANCY_GRID_CELLS = 4_194_304
PREVIEW_PADDING_CELLS = 2
MAX_TEXTURE_CACHE_BYTES = 256 * 1024 * 1024


def decode_texture(blob: bytes, expected_len: int) -> bytes:
    cells = gzip.decompress(blob)
    if len(cells) != expected_len:
        raise ValueError("子图纹理长度不符")
    return cells


def _texture_point(tf: dict, resolution: float, column: float, row: float) -> tuple[float, float]:
    yaw = yaw_of(tf["rotation"])
    cosine, sine = math.cos(yaw), math.sin(yaw)
    u = column * resolution
    v = row * resolution
    return (
        sine * u - cosine * v + tf["translation"]["x"],
        cosine * u + sine * v - tf["translation"]["y"],
    )


def _clamp(value: float) -> int:
    return max(0, min(255, js_round(value)))


def compose_occupancy(submaps: list[dict]) -> dict:
    """submaps: pose + texture{width,height,resolution,slicePose,cells(decoded)}。"""
    if not submaps:
        raise ValueError("没有可显示的子图")
    slices = []
    for submap in submaps:
        texture = submap["texture"]
        slices.append({**submap, "transform": compose(submap["pose"], texture["slicePose"])})
    bounds = {"minX": math.inf, "minY": math.inf, "maxX": -math.inf, "maxY": -math.inf}
    minimum = math.inf
    for sl in slices:
        tex = sl["texture"]
        minimum = min(minimum, tex["resolution"])
        for column, row in ((0, 0), (tex["width"], 0), (0, tex["height"]), (tex["width"], tex["height"])):
            x, y = _texture_point(sl["transform"], tex["resolution"], column, row)
            bounds["minX"] = min(bounds["minX"], x)
            bounds["minY"] = min(bounds["minY"], y)
            bounds["maxX"] = max(bounds["maxX"], x)
            bounds["maxY"] = max(bounds["maxY"], y)
    span_x = max(bounds["maxX"] - bounds["minX"], minimum)
    span_y = max(bounds["maxY"] - bounds["minY"], minimum)
    content_side = math.floor(math.sqrt(MAX_LIVE_OCCUPANCY_GRID_CELLS)) - PREVIEW_PADDING_CELLS * 2
    resolution = max(minimum, span_x / content_side, span_y / content_side)
    width = math.ceil(span_x / resolution) + PREVIEW_PADDING_CELLS * 2
    height = math.ceil(span_y / resolution) + PREVIEW_PADDING_CELLS * 2
    if width * height > MAX_LIVE_OCCUPANCY_GRID_CELLS:
        raise ValueError("预览栅格超过上限")
    origin_x = bounds["minX"] - PREVIEW_PADDING_CELLS * resolution
    origin_y = bounds["minY"] - PREVIEW_PADDING_CELLS * resolution
    red = np.full(width * height, 128, dtype=np.float64)
    observed = np.zeros(width * height, dtype=np.float64)
    for sl in slices:
        tex = sl["texture"]
        cells = tex["cells"]
        step = max(1, math.floor(resolution / tex["resolution"]))
        yaw = yaw_of(sl["transform"]["rotation"])
        cosine, sine = math.cos(yaw), math.sin(yaw)
        tx, ty = sl["transform"]["translation"]["x"], sl["transform"]["translation"]["y"]
        for row in range(0, tex["height"], step):
            v = (row + 0.5) * tex["resolution"]
            for column in range(0, tex["width"], step):
                source = (row * tex["width"] + column) * 2
                intensity = cells[source]
                alpha = cells[source + 1]
                if intensity == 0 and alpha == 0:
                    continue
                u = (column + 0.5) * tex["resolution"]
                px = sine * u - cosine * v + tx
                py = cosine * u + sine * v - ty
                x = math.floor((px - origin_x) / resolution)
                y = math.floor((py - origin_y) / resolution)
                if x < 0 or y < 0 or x >= width or y >= height:
                    continue
                index = y * width + x
                inverse = 255 - alpha
                red[index] = _clamp(intensity + (red[index] * inverse) / 255)
                observed[index] = _clamp(255 + (observed[index] * inverse) / 255)
    occupancy = [-1] * (width * height)
    for row in range(height):
        for column in range(width):
            index = row * width + column
            target = (height - 1 - row) * width + column
            if observed[index] == 0:
                occupancy[target] = -1
            else:
                occupancy[target] = max(0, min(100, js_round((1 - red[index] / 255) * 100)))
    return {
        "width": width,
        "height": height,
        "resolution": resolution,
        "origin": {"x": origin_x, "y": -(origin_y + (height - 1) * resolution), "yaw": 0},
        "cells": occupancy,
    }
