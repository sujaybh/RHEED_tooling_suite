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
import logging
import time
import numpy as np
from PIL import Image
from typing import Optional

log = logging.getLogger("rheed.parser")

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
    memmap_path: Optional[str] = None,
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
    t_total = time.perf_counter()

    file_mb = len(raw_bytes) / 1_048_576
    log.info("[parse_imm] input size=%.1f MB  force=%sx%s  mode=%s",
             file_mb, force_width, force_height, force_mode)

    t0 = time.perf_counter()
    fmt = auto_detect_format(
        len(raw_bytes),
        force_width=force_width,
        force_height=force_height,
        force_mode=force_mode,
    )
    log.info("[parse_imm] auto_detect done in %.3fs → mode=%s  %dx%d  nframes=%d  stride=%d  header=%d",
             time.perf_counter() - t0,
             fmt["mode"], fmt["width"], fmt["height"], fmt["nframes"],
             fmt["stride"], fmt["frame_header_size"])

    nframes = fmt["nframes"]
    stride = fmt["stride"]
    header = fmt["frame_header_size"]
    w, h = fmt["width"], fmt["height"]
    pixels = w * h

    frame_data_mb = (pixels * fmt["bytes_per_pixel"] * nframes) / 1_048_576
    log.info("[parse_imm] allocating array: %dx%dx%d  dtype=%s  data=%.1f MB",
             nframes, h, w, "uint16" if fmt["mode"] == "gray16" else "uint32x3",
             frame_data_mb)

    t0 = time.perf_counter()
    if fmt["mode"] == "gray16":
        if memmap_path is not None:
            frames = np.memmap(memmap_path, dtype=np.uint16, mode="w+", shape=(nframes, h, w))
            log.info("[parse_imm] memmap created at %s in %.3fs, starting frame loop …",
                     memmap_path, time.perf_counter() - t0)
        else:
            frames = np.empty((nframes, h, w), dtype=np.uint16)
            log.info("[parse_imm] array allocated in %.3fs, starting frame loop …", time.perf_counter() - t0)

        t_loop = time.perf_counter()
        log_interval = max(1, nframes // 10)
        for i in range(nframes):
            start = i * stride + header
            chunk = np.frombuffer(raw_bytes, dtype="<u2", count=pixels, offset=start)
            frames[i] = chunk.reshape((h, w))
            if (i + 1) % log_interval == 0 or i == nframes - 1:
                elapsed = time.perf_counter() - t_loop
                fps_rate = (i + 1) / elapsed if elapsed > 0 else 0
                log.info("[parse_imm] gray16 frame loop  %d/%d (%.0f%%)  elapsed=%.2fs  rate=%.0f frames/s",
                         i + 1, nframes, 100 * (i + 1) / nframes, elapsed, fps_rate)

    else:  # rgb96
        if memmap_path is not None:
            frames = np.memmap(memmap_path, dtype=np.uint32, mode="w+", shape=(nframes, h, w, 3))
            log.info("[parse_imm] memmap created at %s in %.3fs, starting frame loop …",
                     memmap_path, time.perf_counter() - t0)
        else:
            frames = np.empty((nframes, h, w, 3), dtype=np.uint32)
            log.info("[parse_imm] array allocated in %.3fs, starting frame loop …", time.perf_counter() - t0)

        t_loop = time.perf_counter()
        log_interval = max(1, nframes // 10)
        for i in range(nframes):
            start = i * stride + header
            chunk = np.frombuffer(raw_bytes, dtype="<u4", count=pixels * 3, offset=start)
            bgr = chunk.reshape((h, w, 3))
            frames[i] = bgr[:, :, ::-1]  # BGR → RGB
            if (i + 1) % log_interval == 0 or i == nframes - 1:
                elapsed = time.perf_counter() - t_loop
                fps_rate = (i + 1) / elapsed if elapsed > 0 else 0
                log.info("[parse_imm] rgb96 frame loop  %d/%d (%.0f%%)  elapsed=%.2fs  rate=%.0f frames/s",
                         i + 1, nframes, 100 * (i + 1) / nframes, elapsed, fps_rate)

    log.info("[parse_imm] DONE  total=%.3fs  file=%.1f MB  frames=%d  resolution=%dx%d  mode=%s",
             time.perf_counter() - t_total, file_mb, nframes, w, h, fmt["mode"])

    return frames, fmt


def frame_to_png_bytes(frame: np.ndarray, mode: str, contrast: float = 1.0) -> bytes:
    """
    Convert a single frame array to PNG bytes (uint8 preview).

    Uses gamma correction: output = (input / ref_max) ^ (1/contrast) * 255
    contrast=1.0 → linear (gamma=1); contrast>1 → lifts shadows (gamma<1, brighter);
    contrast<1 → crushes shadows (gamma>1, darker).
    """
    gamma = 1.0 / contrast
    if mode == "gray16":
        normalized = frame.astype(np.float32) / 4096.0
        preview = np.clip(np.power(normalized, gamma) * 255.0, 0, 255).astype(np.uint8)
        img = Image.fromarray(preview, mode="L")
    else:
        normalized = frame.astype(np.float32) / 20000.0
        preview = np.clip(np.power(normalized, gamma) * 255.0, 0, 255).astype(np.uint8)
        img = Image.fromarray(preview, mode="RGB")

    buf = io.BytesIO()
    # compress_level=1: ~5 ms encode vs ~40 ms at default 6 — size irrelevant for local tool
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()
