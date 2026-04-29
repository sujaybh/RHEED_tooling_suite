"""
Parse kSA proprietary .imm files into numpy arrays.

Observed format variants:
  gray16: N × (640-byte header  + W×H×2  bytes, little-endian uint16)
  rgb96:  N × (655-byte header  + W×H×12 bytes, little-endian uint32, BGR channel order)

Auto-detection tries a set of common resolutions; if none divides evenly the
caller can supply explicit width/height.

parse_imm_from_file() is preferred over parse_imm(): it memory-maps the source
file so raw bytes never enter the Python heap, keeping peak RAM near zero.
"""
from __future__ import annotations

import io
import logging
import os
import time
from typing import Optional

import numpy as np
import psutil
from PIL import Image

log = logging.getLogger("rheed.parser")
_proc = psutil.Process()


def _mem(label: str) -> None:
    rss = _proc.memory_info().rss / 1_048_576
    vm  = psutil.virtual_memory()
    log.info("[MEM/%s]  RSS=%.0f MB  avail=%.0f MB  used=%.0f%%",
             label, rss, vm.available / 1_048_576, vm.percent)

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


def parse_imm_from_file(
    file_path: str,
    force_width: Optional[int] = None,
    force_height: Optional[int] = None,
    force_mode: Optional[str] = None,
    memmap_path: Optional[str] = None,
) -> tuple[np.ndarray, dict]:
    """
    Parse an IMM file directly from disk — zero Python-heap allocation for pixel data.

    The source file is memory-mapped (OS page cache), so raw bytes never live
    in Python's heap.  Peak RAM during parse is one frame worth of numpy
    temporaries (~2 MB for 1024×1024 gray16) rather than the full file size.

    Returns
    -------
    frames : np.ndarray
        shape (nframes, H, W)      dtype uint16  for gray16
        shape (nframes, H, W, 3)  dtype uint32  for rgb96  (RGB order)
    fmt : dict
        Format dict from auto_detect_format.
    """
    t_total = time.perf_counter()
    file_size = os.path.getsize(file_path)
    file_mb = file_size / 1_048_576

    log.info("[parse_imm_from_file] path=%s  size=%.1f MB  force=%sx%s  mode=%s",
             os.path.basename(file_path), file_mb, force_width, force_height, force_mode)

    t0 = time.perf_counter()
    fmt = auto_detect_format(file_size, force_width, force_height, force_mode)
    log.info("[parse_imm_from_file] detected in %.3fs: mode=%s  %dx%d  nframes=%d  stride=%d",
             time.perf_counter() - t0,
             fmt["mode"], fmt["width"], fmt["height"], fmt["nframes"], fmt["stride"])

    nframes = fmt["nframes"]
    stride  = fmt["stride"]
    header  = fmt["frame_header_size"]
    w, h    = fmt["width"], fmt["height"]
    pixels  = w * h

    # Memory-map the source file as raw bytes — no Python heap allocation
    _mem("before-src-mmap")
    src = np.memmap(file_path, dtype=np.uint8, mode='r')
    _mem("after-src-mmap")

    t0 = time.perf_counter()
    _LOG_EVERY  = max(1, nframes // 10)   # progress every ~10%
    _MEM_EVERY  = max(1, nframes // 5)    # memory snapshot every ~20%
    _WARN_AVAIL = 400                      # MB — warn if below this

    try:
        if fmt["mode"] == "gray16":
            shape = (nframes, h, w)
            if memmap_path is not None:
                frames = np.memmap(memmap_path, dtype=np.uint16, mode='w+', shape=shape)
                log.info("[parse_imm_from_file] dest memmap created at %s", os.path.basename(memmap_path))
            else:
                frames = np.empty(shape, dtype=np.uint16)
            _mem("after-dest-alloc-gray16")

            pixel_bytes = pixels * 2
            for i in range(nframes):
                start = i * stride + header
                # view() is zero-copy: reinterprets uint8 slice as uint16 in-place
                frames[i] = src[start:start + pixel_bytes].view('<u2').reshape(h, w)
                if (i + 1) % _LOG_EVERY == 0 or i == nframes - 1:
                    elapsed = time.perf_counter() - t0
                    log.info("[parse_imm_from_file] gray16  %d/%d (%.0f%%)  %.2fs  %.0f fr/s",
                             i + 1, nframes, 100 * (i + 1) / nframes,
                             elapsed, (i + 1) / elapsed if elapsed > 0 else 0)
                if (i + 1) % _MEM_EVERY == 0:
                    vm = psutil.virtual_memory()
                    avail = vm.available / 1_048_576
                    log.info("[MEM/parse] frame=%d/%d  avail=%.0f MB  RSS=%.0f MB",
                             i + 1, nframes, avail, _proc.memory_info().rss / 1_048_576)
                    if avail < _WARN_AVAIL:
                        log.warning("[MEM/parse] *** LOW MEMORY %.0f MB < %d MB threshold *** "
                                    "OOM risk at frame %d/%d", avail, _WARN_AVAIL, i + 1, nframes)

        else:  # rgb96
            shape = (nframes, h, w, 3)
            if memmap_path is not None:
                frames = np.memmap(memmap_path, dtype=np.uint32, mode='w+', shape=shape)
                log.info("[parse_imm_from_file] dest memmap created at %s", os.path.basename(memmap_path))
            else:
                frames = np.empty(shape, dtype=np.uint32)
            _mem("after-dest-alloc-rgb96")

            pixel_bytes = pixels * 12
            for i in range(nframes):
                start = i * stride + header
                bgr = src[start:start + pixel_bytes].view('<u4').reshape(h, w, 3)
                frames[i] = bgr[:, :, ::-1]  # BGR → RGB
                if (i + 1) % _LOG_EVERY == 0 or i == nframes - 1:
                    elapsed = time.perf_counter() - t0
                    log.info("[parse_imm_from_file] rgb96  %d/%d (%.0f%%)  %.2fs  %.0f fr/s",
                             i + 1, nframes, 100 * (i + 1) / nframes,
                             elapsed, (i + 1) / elapsed if elapsed > 0 else 0)
                if (i + 1) % _MEM_EVERY == 0:
                    vm = psutil.virtual_memory()
                    avail = vm.available / 1_048_576
                    log.info("[MEM/parse] frame=%d/%d  avail=%.0f MB  RSS=%.0f MB",
                             i + 1, nframes, avail, _proc.memory_info().rss / 1_048_576)
                    if avail < _WARN_AVAIL:
                        log.warning("[MEM/parse] *** LOW MEMORY %.0f MB < %d MB threshold *** "
                                    "OOM risk at frame %d/%d", avail, _WARN_AVAIL, i + 1, nframes)

    finally:
        del src  # release the source memmap immediately
        _mem("after-src-del")

    log.info("[parse_imm_from_file] DONE  total=%.3fs  file=%.1f MB  frames=%d  %dx%d  mode=%s",
             time.perf_counter() - t_total, file_mb, nframes, w, h, fmt["mode"])
    _mem("parse-done")

    return frames, fmt


def get_raw_headers(file_path: str, fmt: dict) -> bytes:
    """
    Extract concatenated per-frame headers from an IMM file on disk.

    For gray16 with 172 frames: 172 × 640 B = ~110 KB — trivially small.
    """
    fhs    = fmt["frame_header_size"]
    stride = fmt["stride"]
    nframes = fmt["nframes"]
    src = np.memmap(file_path, dtype=np.uint8, mode='r')
    try:
        return b"".join(bytes(src[i * stride : i * stride + fhs]) for i in range(nframes))
    finally:
        del src


def parse_imm(
    raw_bytes: bytes,
    force_width: Optional[int] = None,
    force_height: Optional[int] = None,
    force_mode: Optional[str] = None,
    memmap_path: Optional[str] = None,
) -> tuple[np.ndarray, dict]:
    """
    Parse raw IMM bytes (legacy interface — holds the full file in RAM).

    Prefer parse_imm_from_file() for large files.

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

    t0 = time.perf_counter()
    if fmt["mode"] == "gray16":
        if memmap_path is not None:
            frames = np.memmap(memmap_path, dtype=np.uint16, mode="w+", shape=(nframes, h, w))
        else:
            frames = np.empty((nframes, h, w), dtype=np.uint16)

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
        else:
            frames = np.empty((nframes, h, w, 3), dtype=np.uint32)

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
