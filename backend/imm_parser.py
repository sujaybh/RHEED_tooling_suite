"""
Parse kSA proprietary .imm files into numpy arrays.

Observed format variants:
  gray16: N × (640-byte header  + W×H×2  bytes, little-endian uint16)
  rgb96:  N × (655-byte header  + W×H×12 bytes, little-endian uint32, BGR channel order)

Auto-detection tries a set of common resolutions; if none divides evenly the
caller can supply explicit width/height.
"""
from __future__ import annotations

import io
import numpy as np
from PIL import Image
from typing import Optional

# (width, height) pairs to try during auto-detection, most common first
_COMMON_RESOLUTIONS = [
    (1024, 1024),
    (640, 480),
    (800, 600),
    (1024, 768),
    (1280, 1024),
    (1600, 1200),
    (512, 512),
    (640, 512),
    (320, 240),
    (2048, 2048),
]

_VARIANTS = [
    # (mode_name, frame_header_bytes, bytes_per_pixel)
    ("gray16", 640, 2),
    ("rgb96",  655, 12),
]


def auto_detect_format(
    file_size: int,
    force_width: Optional[int] = None,
    force_height: Optional[int] = None,
    force_mode: Optional[str] = None,
) -> dict:
    """
    Return a format dict: {mode, width, height, nframes, frame_header_size,
    bytes_per_pixel, stride}.

    If force_width/force_height are given, only that resolution is tried.
    Raises ValueError if no match is found.
    """
    resolutions = (
        [(force_width, force_height)]
        if force_width and force_height
        else _COMMON_RESOLUTIONS
    )
    variants = (
        [v for v in _VARIANTS if v[0] == force_mode]
        if force_mode
        else _VARIANTS
    )

    candidates = []
    for w, h in resolutions:
        pixels = w * h
        for mode, header, bpp in variants:
            stride = header + pixels * bpp
            if stride > 0 and file_size % stride == 0:
                nframes = file_size // stride
                if nframes > 0:
                    candidates.append(
                        dict(
                            mode=mode,
                            width=w,
                            height=h,
                            nframes=nframes,
                            frame_header_size=header,
                            bytes_per_pixel=bpp,
                            stride=stride,
                        )
                    )

    if not candidates:
        raise ValueError(
            "Could not auto-detect IMM dimensions from file size. "
            "Provide explicit width and height."
        )

    # Prefer gray16 over rgb96; among ties prefer more frames (smaller stride)
    candidates.sort(key=lambda c: (c["mode"] != "gray16", -c["nframes"]))
    return candidates[0]


def parse_imm(
    raw_bytes: bytes,
    force_width: Optional[int] = None,
    force_height: Optional[int] = None,
    force_mode: Optional[str] = None,
) -> tuple[np.ndarray, dict]:
    """
    Parse raw IMM bytes.

    Returns
    -------
    frames : np.ndarray
        shape (nframes, height, width)        dtype uint16  for gray16
        shape (nframes, height, width, 3)     dtype uint32  for rgb96  (RGB order)
    fmt : dict
        The format dict returned by auto_detect_format.
    """
    fmt = auto_detect_format(
        len(raw_bytes),
        force_width=force_width,
        force_height=force_height,
        force_mode=force_mode,
    )

    nframes = fmt["nframes"]
    stride = fmt["stride"]
    header = fmt["frame_header_size"]
    w, h = fmt["width"], fmt["height"]
    pixels = w * h

    if fmt["mode"] == "gray16":
        frames = np.empty((nframes, h, w), dtype=np.uint16)
        for i in range(nframes):
            start = i * stride + header
            chunk = np.frombuffer(raw_bytes, dtype="<u2", count=pixels, offset=start)
            frames[i] = chunk.reshape((h, w))

    else:  # rgb96
        frames = np.empty((nframes, h, w, 3), dtype=np.uint32)
        for i in range(nframes):
            start = i * stride + header
            chunk = np.frombuffer(raw_bytes, dtype="<u4", count=pixels * 3, offset=start)
            bgr = chunk.reshape((h, w, 3))
            frames[i] = bgr[:, :, ::-1]  # BGR → RGB

    return frames, fmt


def frame_to_png_bytes(frame: np.ndarray, mode: str, contrast: float = 1.0) -> bytes:
    """
    Convert a single frame array to PNG bytes (uint8 preview).

    gray16 → L mode:   uint8 = clip(round(uint16 / 16 * contrast), 0, 255)
    rgb96  → RGB mode:  uint8 = clip(round(uint32 / 20000 * contrast), 0, 255)
    """
    if mode == "gray16":
        preview = np.clip(np.rint(frame.astype(np.float32) / 16.0 * contrast), 0, 255).astype(np.uint8)
        img = Image.fromarray(preview, mode="L")
    else:
        preview = np.clip(np.rint(frame.astype(np.float32) / 20000.0 * contrast), 0, 255).astype(np.uint8)
        img = Image.fromarray(preview, mode="RGB")

    buf = io.BytesIO()
    # compress_level=1: ~5 ms encode vs ~40 ms at default 6 — size irrelevant for local tool
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()
