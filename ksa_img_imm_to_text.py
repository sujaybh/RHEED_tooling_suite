#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


def load_bmp_dimensions(bmp_path: Path) -> tuple[int, int, str]:
    bmp = Image.open(bmp_path)
    return bmp.size[0], bmp.size[1], bmp.mode


def detect_img_variant(raw_size: int, width: int, height: int, force_mode: Optional[str] = None):
    pixels = width * height
    candidates = []

    gray16_header = raw_size - pixels * 2
    if gray16_header >= 0:
        candidates.append({
            "mode": "gray16",
            "header_size": gray16_header,
            "bytes_per_pixel": 2,
            "description": "16-bit grayscale"
        })

    rgb96_header = raw_size - pixels * 12
    if rgb96_header >= 0:
        candidates.append({
            "mode": "rgb96",
            "header_size": rgb96_header,
            "bytes_per_pixel": 12,
            "description": "3x32-bit color (B,G,R order)"
        })

    if force_mode is not None:
        matches = [c for c in candidates if c["mode"] == force_mode]
        if not matches:
            raise SystemExit(
                f"Forced mode {force_mode!r} is incompatible with file size and dimensions."
            )
        return matches[0]

    if not candidates:
        raise SystemExit("Could not match file size to a supported IMG format.")
    candidates.sort(key=lambda c: c["header_size"])
    return candidates[0]


def detect_imm_variant(raw_size: int, width: int, height: int, force_mode: Optional[str] = None):
    """
    Detect a movie made of repeated IMG-like frame blocks.

    Observed kSA pattern:
      grayscale movie: N * (640-byte header + width*height*2)
      color movie:     N * (655-byte header + width*height*12)

    Returns the candidate that yields the largest integer number of frames.
    """
    pixels = width * height
    candidates = []

    gray16_stride = 640 + pixels * 2
    if gray16_stride > 0 and raw_size % gray16_stride == 0:
        candidates.append({
            "mode": "gray16",
            "frame_header_size": 640,
            "bytes_per_pixel": 2,
            "stride": gray16_stride,
            "nframes": raw_size // gray16_stride,
            "description": "IMM made of repeated gray16 IMG-like frames"
        })

    rgb96_stride = 655 + pixels * 12
    if rgb96_stride > 0 and raw_size % rgb96_stride == 0:
        candidates.append({
            "mode": "rgb96",
            "frame_header_size": 655,
            "bytes_per_pixel": 12,
            "stride": rgb96_stride,
            "nframes": raw_size // rgb96_stride,
            "description": "IMM made of repeated rgb96 IMG-like frames"
        })

    if force_mode is not None:
        matches = [c for c in candidates if c["mode"] == force_mode]
        if not matches:
            raise SystemExit(
                f"Forced mode {force_mode!r} is incompatible with file size and dimensions."
            )
        return matches[0]

    if not candidates:
        raise SystemExit(
            "Could not match file size to a supported IMM format. "
            "This script currently supports the two kSA variants we have observed."
        )

    candidates.sort(key=lambda c: (-c["nframes"], c["stride"]))
    return candidates[0]


def save_gray16(prefix: Path, raw_bytes: bytes, width: int, height: int, header_size: int):
    payload = np.frombuffer(raw_bytes[header_size:], dtype="<u2")
    expected = width * height
    if payload.size != expected:
        raise SystemExit(f"Expected {expected} uint16 values, got {payload.size}.")

    img16 = payload.reshape((height, width)).copy()

    np.savetxt(prefix.with_name(prefix.name + "_gray16_matrix.txt"),
               img16, fmt="%u", delimiter="\t")

    preview8 = np.clip(np.rint(img16 / 16.0), 0, 255).astype(np.uint8)
    Image.fromarray(preview8, mode="L").save(prefix.with_name(prefix.name + "_reconstructed.png"))

    notes = [
        f"Detected mode: gray16",
        f"Width: {width}",
        f"Height: {height}",
        f"Header size: {header_size} bytes",
        "Payload layout: uint16 grayscale pixels, little-endian",
        "Preview conversion used: gray8 = round(gray16 / 16)",
        "Interpretation: effectively 12-bit data stored in a 16-bit container",
    ]
    prefix.with_name(prefix.name + "_notes.txt").write_text("\n".join(notes), encoding="utf-8")


def save_rgb96(prefix: Path, raw_bytes: bytes, width: int, height: int, header_size: int):
    payload = np.frombuffer(raw_bytes[header_size:], dtype="<u4")
    expected = width * height * 3
    if payload.size != expected:
        raise SystemExit(f"Expected {expected} uint32 values, got {payload.size}.")

    bgr32 = payload.reshape((height, width, 3))
    rgb32 = bgr32[:, :, ::-1].copy()

    np.savetxt(prefix.with_name(prefix.name + "_R32_matrix.txt"),
               rgb32[:, :, 0], fmt="%u", delimiter="\t")
    np.savetxt(prefix.with_name(prefix.name + "_G32_matrix.txt"),
               rgb32[:, :, 1], fmt="%u", delimiter="\t")
    np.savetxt(prefix.with_name(prefix.name + "_B32_matrix.txt"),
               rgb32[:, :, 2], fmt="%u", delimiter="\t")

    preview8 = np.clip(np.rint(rgb32 / 20000.0), 0, 255).astype(np.uint8)
    Image.fromarray(preview8, mode="RGB").save(prefix.with_name(prefix.name + "_reconstructed.png"))

    notes = [
        f"Detected mode: rgb96",
        f"Width: {width}",
        f"Height: {height}",
        f"Header size: {header_size} bytes",
        "Payload layout: interleaved uint32 channels in B,G,R order, little-endian",
        "Preview conversion used: rgb8 = round(rgb32 / 20000)",
    ]
    prefix.with_name(prefix.name + "_notes.txt").write_text("\n".join(notes), encoding="utf-8")


def save_gray16_movie(prefix: Path, raw_bytes: bytes, width: int, height: int, frame_header_size: int, nframes: int):
    pixels = width * height
    stride = frame_header_size + pixels * 2
    out_dir = prefix.with_name(prefix.name + "_frames")
    out_dir.mkdir(parents=True, exist_ok=True)

    header_dump_dir = out_dir / "headers"
    header_dump_dir.mkdir(parents=True, exist_ok=True)

    for i in range(nframes):
        start = i * stride
        hdr = raw_bytes[start:start + frame_header_size]
        payload = np.frombuffer(raw_bytes[start + frame_header_size:start + stride], dtype="<u2")
        if payload.size != pixels:
            raise SystemExit(f"Frame {i}: expected {pixels} uint16 values, got {payload.size}.")
        img16 = payload.reshape((height, width)).copy()

        np.savetxt(out_dir / f"frame_{i+1:04d}_gray16_matrix.txt",
                   img16, fmt="%u", delimiter="\t")

        preview8 = np.clip(np.rint(img16 / 16.0), 0, 255).astype(np.uint8)
        Image.fromarray(preview8, mode="L").save(out_dir / f"frame_{i+1:04d}_preview.png")

        # Save the per-frame header as raw bytes plus hex text for reverse engineering.
        (header_dump_dir / f"frame_{i+1:04d}_header.bin").write_bytes(hdr)
        (header_dump_dir / f"frame_{i+1:04d}_header_hex.txt").write_text(
            hdr.hex(" ", 1), encoding="utf-8"
        )

    notes = [
        f"Detected IMM mode: gray16",
        f"Width: {width}",
        f"Height: {height}",
        f"Frames: {nframes}",
        f"Per-frame header size: {frame_header_size} bytes",
        f"Per-frame payload size: {pixels * 2} bytes",
        f"Per-frame stride: {stride} bytes",
        "Interpretation: this IMM is a direct concatenation of IMG-like grayscale frames",
        "Each frame = 640-byte header + width*height uint16 payload",
        "Preview conversion used: gray8 = round(gray16 / 16)",
    ]
    (prefix.with_name(prefix.name + "_imm_notes.txt")).write_text("\n".join(notes), encoding="utf-8")


def save_rgb96_movie(prefix: Path, raw_bytes: bytes, width: int, height: int, frame_header_size: int, nframes: int):
    pixels = width * height
    stride = frame_header_size + pixels * 12
    out_dir = prefix.with_name(prefix.name + "_frames")
    out_dir.mkdir(parents=True, exist_ok=True)

    header_dump_dir = out_dir / "headers"
    header_dump_dir.mkdir(parents=True, exist_ok=True)

    for i in range(nframes):
        start = i * stride
        hdr = raw_bytes[start:start + frame_header_size]
        payload = np.frombuffer(raw_bytes[start + frame_header_size:start + stride], dtype="<u4")
        if payload.size != pixels * 3:
            raise SystemExit(f"Frame {i}: expected {pixels*3} uint32 values, got {payload.size}.")
        bgr32 = payload.reshape((height, width, 3))
        rgb32 = bgr32[:, :, ::-1].copy()

        np.savetxt(out_dir / f"frame_{i+1:04d}_R32_matrix.txt",
                   rgb32[:, :, 0], fmt="%u", delimiter="\t")
        np.savetxt(out_dir / f"frame_{i+1:04d}_G32_matrix.txt",
                   rgb32[:, :, 1], fmt="%u", delimiter="\t")
        np.savetxt(out_dir / f"frame_{i+1:04d}_B32_matrix.txt",
                   rgb32[:, :, 2], fmt="%u", delimiter="\t")

        preview8 = np.clip(np.rint(rgb32 / 20000.0), 0, 255).astype(np.uint8)
        Image.fromarray(preview8, mode="RGB").save(out_dir / f"frame_{i+1:04d}_preview.png")

        (header_dump_dir / f"frame_{i+1:04d}_header.bin").write_bytes(hdr)
        (header_dump_dir / f"frame_{i+1:04d}_header_hex.txt").write_text(
            hdr.hex(" ", 1), encoding="utf-8"
        )

    notes = [
        f"Detected IMM mode: rgb96",
        f"Width: {width}",
        f"Height: {height}",
        f"Frames: {nframes}",
        f"Per-frame header size: {frame_header_size} bytes",
        f"Per-frame payload size: {pixels * 12} bytes",
        f"Per-frame stride: {stride} bytes",
        "Interpretation: this IMM is a direct concatenation of IMG-like color frames",
        "Each frame = 655-byte header + width*height*3 uint32 payload",
        "Preview conversion used: rgb8 = round(rgb32 / 20000)",
    ]
    (prefix.with_name(prefix.name + "_imm_notes.txt")).write_text("\n".join(notes), encoding="utf-8")


def infer_dimensions(args):
    if args.bmp is not None:
        w, h, bmp_mode = load_bmp_dimensions(args.bmp)
        return w, h, bmp_mode
    if args.width is None or args.height is None:
        raise SystemExit("Provide either --bmp or both --width and --height.")
    return args.width, args.height, None


def main():
    parser = argparse.ArgumentParser(
        description="Convert observed kSA IMG / IMM variants into text matrices and preview images."
    )
    parser.add_argument("input_file", type=Path, help="Input .img or .imm file")
    parser.add_argument("--bmp", type=Path, default=None,
                        help="Optional paired BMP export from kSA (for dimensions)")
    parser.add_argument("--width", type=int, default=1024, help="Image width if no BMP is supplied")
    parser.add_argument("--height", type=int, default=1024, help="Image height if no BMP is supplied")
    parser.add_argument("--mode", choices=["gray16", "rgb96"], default=None,
                        help="Force a specific parser mode instead of auto-detect")
    parser.add_argument("--out-prefix", type=Path, default=None, help="Output file prefix")
    args = parser.parse_args()

    input_path = args.input_file
    suffix = input_path.suffix.lower()
    width, height, bmp_mode = infer_dimensions(args)
    raw_bytes = input_path.read_bytes()
    prefix = args.out_prefix if args.out_prefix is not None else input_path.with_suffix("")
    prefix = Path(prefix)

    if suffix == ".img":
        fmt = detect_img_variant(len(raw_bytes), width, height, force_mode=args.mode)
        if fmt["mode"] == "gray16":
            save_gray16(prefix, raw_bytes, width, height, fmt["header_size"])
        elif fmt["mode"] == "rgb96":
            save_rgb96(prefix, raw_bytes, width, height, fmt["header_size"])
        else:
            raise SystemExit(f"Unsupported IMG mode: {fmt['mode']}")

        print("Done.")
        print(f"Detected IMG format: {fmt['description']}")
        print(f"Header size: {fmt['header_size']} bytes")
        print(f"Dimensions: {width} x {height}")

    elif suffix == ".imm":
        fmt = detect_imm_variant(len(raw_bytes), width, height, force_mode=args.mode)
        if fmt["mode"] == "gray16":
            save_gray16_movie(prefix, raw_bytes, width, height, fmt["frame_header_size"], fmt["nframes"])
        elif fmt["mode"] == "rgb96":
            save_rgb96_movie(prefix, raw_bytes, width, height, fmt["frame_header_size"], fmt["nframes"])
        else:
            raise SystemExit(f"Unsupported IMM mode: {fmt['mode']}")

        print("Done.")
        print(f"Detected IMM format: {fmt['description']}")
        print(f"Per-frame header size: {fmt['frame_header_size']} bytes")
        print(f"Frames: {fmt['nframes']}")
        print(f"Dimensions: {width} x {height}")

    else:
        raise SystemExit("Unsupported extension. Use .img or .imm")

    if bmp_mode is not None:
        print(f"Paired BMP mode: {bmp_mode}")


if __name__ == "__main__":
    main()
