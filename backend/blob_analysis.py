"""
Blob detection and intensity tracking for RHEED diffraction patterns.

Physical layout:
  - Top half   (y < H//2): incoming electron beam — 1-2 very bright circular spots
  - Bottom half (y >= H//2): diffraction pattern   — multiple dimmer spots

Strategy:
  1. Average the first N frames to improve SNR.
  2. Normalize each half independently to [0,1] (critical: prevents the bright
     beam spot from suppressing the LoG response at diffraction-spot scale).
  3. Run blob_log on each half with tuned parameters.
  4. For every detected blob, extract a circular ROI and compute mean uint16
     intensity across ALL frames → mean_intensities time series.
"""
from __future__ import annotations

import logging
import time

import numpy as np
import psutil
from skimage.feature import blob_log
from typing import Optional

log = logging.getLogger("rheed.blob")
_proc = psutil.Process()


def _mem(label: str) -> None:
    rss = _proc.memory_info().rss / 1_048_576
    vm  = psutil.virtual_memory()
    log.info("[MEM/%s]  RSS=%.0f MB  avail=%.0f MB  used=%.0f%%",
             label, rss, vm.available / 1_048_576, vm.percent)

# Pre-assigned display colors
_BEAM_COLORS = ["#ff4444", "#ff8822"]
_DIFF_COLORS = [
    "#44aaff", "#44ffbb", "#ffdd44", "#ee44ff",
    "#44ffff", "#ff8844", "#88ff44", "#ff4488",
    "#8844ff", "#44ff88", "#ffaa44", "#44bbff",
    "#ff44aa", "#aaff44", "#4488ff",
]


def _peak_intensity(image: np.ndarray, y: float, x: float) -> float:
    """Return the value at the nearest integer pixel, clamped to image bounds."""
    yi = int(np.clip(round(y), 0, image.shape[0] - 1))
    xi = int(np.clip(round(x), 0, image.shape[1] - 1))
    return float(image[yi, xi])


def _circular_mask(height: int, width: int, cy: float, cx: float, r: float) -> np.ndarray:
    """Boolean mask of a filled circle, clipped to image bounds."""
    r = max(r, 1.0)
    y_idx, x_idx = np.ogrid[:height, :width]
    return (x_idx - cx) ** 2 + (y_idx - cy) ** 2 <= r ** 2


def find_beam_center(
    frame: np.ndarray,
    beam_roi_fraction: float = 0.5,
    local_radius: int = 20,
) -> tuple[float, float]:
    """
    Locate the electron beam spot center in the top half of a frame.

    Algorithm
    ---------
    1. Crop to the top half (beam region).
    2. Find the single brightest pixel — the electron beam is always the
       dominant intensity peak, so the global maximum is a reliable anchor
       regardless of background variation or secondary features.
    3. Compute an intensity-weighted centroid within `local_radius` pixels
       of that peak, keeping only pixels above the mean intensity inside
       that local disc.  This sub-pixel refines the center while staying
       immune to bright regions elsewhere in the frame.

    Why not a global percentile threshold?
    The 95th-percentile approach computes one threshold over the entire top
    half, so diffuse background, hot pixels, or secondary spots that happen
    to be bright in a given frame shift the centroid away from the beam —
    causing the "jumping" symptom.  Anchoring to the global max first and
    then computing a *local* centroid is both faster and far more stable.
    """
    H = frame.shape[0]
    W = frame.shape[1]
    split_row = int(H * beam_roi_fraction)
    top = frame[:split_row].astype(np.float64)
    if len(top.shape) == 3:          # rgb96: collapse channels
        top = top.mean(axis=2)

    # Step 1: global maximum — beam is the brightest thing in the top half
    flat_idx = np.argmax(top)
    peak_y, peak_x = np.unravel_index(flat_idx, top.shape)

    # Step 2: local disc around the peak
    y_grid, x_grid = np.ogrid[:top.shape[0], :top.shape[1]]
    local_mask = (x_grid - peak_x) ** 2 + (y_grid - peak_y) ** 2 <= local_radius ** 2

    y_idx, x_idx = np.where(local_mask)
    weights = top[y_idx, x_idx]

    # Step 3: keep only pixels above the mean within the disc (bright core)
    bright = weights > weights.mean()
    if bright.sum() >= 3:
        cx = float(np.average(x_idx[bright], weights=weights[bright]))
        cy = float(np.average(y_idx[bright], weights=weights[bright]))
    else:
        cx = float(peak_x)
        cy = float(peak_y)

    return cx, cy


def _greedy_assign(centers: list, ref_centers: list) -> list:
    """
    One-to-one assignment of `centers[j]` to `ref_centers[i]` by minimum
    Euclidean distance (greedy, guaranteed permutation).
    Returns assignment[j] = i.
    """
    K = len(centers)
    pairs = []
    for j in range(K):
        for i in range(K):
            dx = centers[j][0] - ref_centers[i][0]
            dy = centers[j][1] - ref_centers[i][1]
            pairs.append((dx * dx + dy * dy, j, i))
    pairs.sort()

    assignment: dict[int, int] = {}
    used_j: set[int] = set()
    used_i: set[int] = set()
    for _, j, i in pairs:
        if j not in used_j and i not in used_i:
            assignment[j] = i
            used_j.add(j)
            used_i.add(i)
    return [assignment[j] for j in range(K)]


def _median_smooth(seq: list, window: int = 5) -> list:
    """
    Median filter on an integer assignment sequence.

    Strip assignments are step functions — long stable runs with rare transitions.
    A median window votes out isolated wrong frames (stray assignments) and fixes
    the off-by-one at transition boundaries where the beam centroid lands in an
    ambiguous intermediate position for exactly one frame.
    """
    arr = np.array(seq)
    half = window // 2
    out = arr.copy()
    for i in range(len(arr)):
        lo = max(0, i - half)
        hi = min(len(arr), i + half + 1)
        out[i] = int(np.median(arr[lo:hi]))
    return out.tolist()


def assign_strips(
    frames_list: list,            # K arrays, each (N, H, W) uint16
    beam_roi_fraction: float = 0.5,
    smooth_window: int = 5,
) -> tuple:
    """
    Given K .imm files as frame arrays, determine which original strip each
    frame belongs to for each fixed strip.

    Returns
    -------
    assignments : list[list[int]]
        assignments[fixed_strip_i][frame_n] = original_strip_j
    ref_centers : list[tuple[float, float]]
        Beam-spot centroid in frame 0 of each original strip.
    """
    K = len(frames_list)
    N = min(f.shape[0] for f in frames_list)

    _WARN_AVAIL = 400   # MB — warn if below this during analysis
    _MEM_EVERY  = max(1, N // 10)   # memory log every ~10% of frames

    log.info("[assign_strips] START  K=%d strips  N=%d frames", K, N)
    _mem("assign-start")

    # Reference position = beam center in frame 0 of each original strip
    ref_centers = [find_beam_center(frames_list[k][0], beam_roi_fraction) for k in range(K)]
    _mem("assign-ref-centers-done")
    log.info("[assign_strips] ref_centers=%s", [(f"{cx:.1f}", f"{cy:.1f}") for cx, cy in ref_centers])

    # Frame 0 is always correct (identity assignment)
    assignments: list[list[int]] = [[k] for k in range(K)]  # assignments[i] starts with [i]

    t0 = time.perf_counter()
    for n in range(1, N):
        current_centers = [find_beam_center(frames_list[j][n], beam_roi_fraction) for j in range(K)]
        # assignment_n[j] = i: original strip j at frame n → fixed strip i
        assignment_n = _greedy_assign(current_centers, ref_centers)
        for j, i in enumerate(assignment_n):
            assignments[i].append(j)

        if n % _MEM_EVERY == 0:
            elapsed = time.perf_counter() - t0
            vm = psutil.virtual_memory()
            avail = vm.available / 1_048_576
            rss   = _proc.memory_info().rss / 1_048_576
            rate  = n / elapsed if elapsed > 0 else 0
            log.info("[assign_strips] frame %d/%d (%.0f%%)  %.1fs  %.0f fr/s  "
                     "avail=%.0f MB  RSS=%.0f MB  used=%.0f%%",
                     n, N, 100 * n / N, elapsed, rate, avail, rss, vm.percent)
            if avail < _WARN_AVAIL:
                log.warning("[assign_strips] *** LOW MEMORY %.0f MB at frame %d/%d ***",
                            avail, n, N)

    elapsed = time.perf_counter() - t0
    log.info("[assign_strips] frame loop done  %d frames  %.2fs  %.0f fr/s",
             N, elapsed, N / elapsed if elapsed > 0 else 0)
    _mem("assign-frame-loop-done")

    # Smooth out isolated wrong frames caused by ambiguous transition-point centroids.
    # Median filter is applied per-strip independently, which can introduce conflicts
    # (two fixed strips claiming the same original strip at the same frame).  For any
    # such frame, fall back to the original greedy assignment which is a valid permutation.
    if smooth_window > 1:
        raw = [list(assignments[i]) for i in range(K)]   # unsmoothed copy
        smoothed = [_median_smooth(assignments[i], smooth_window) for i in range(K)]
        for n in range(N):
            proposed = [smoothed[i][n] for i in range(K)]
            if len(set(proposed)) == K:          # still a valid permutation
                for i in range(K):
                    assignments[i][n] = smoothed[i][n]
            # else: leave assignments[i][n] as the raw greedy value

    _mem("assign-done")
    log.info("[assign_strips] DONE  K=%d  N=%d", K, N)
    return assignments, ref_centers


def detect_and_track(
    frames: np.ndarray,       # (nframes, H, W) uint16
    n_analysis_frames: int = 5,
    beam_roi_fraction: float = 0.5,
    # blob_log params — beam (top half)
    beam_min_sigma: float = 5.0,
    beam_max_sigma: float = 40.0,
    beam_num_sigma: int = 8,
    beam_threshold: float = 0.15,
    beam_max_blobs: int = 3,
    # blob_log params — diffraction (bottom half)
    diff_min_sigma: float = 2.0,
    diff_max_sigma: float = 20.0,
    diff_num_sigma: int = 10,
    diff_threshold: float = 0.04,
    diff_max_blobs: int = 15,
) -> dict:
    """
    Detect blobs and return per-blob intensity traces across all frames.

    Returns
    -------
    dict with key "blobs", each blob containing:
        blob_id, region, center_x, center_y, radius_px, color, mean_intensities
    """
    nframes, H, W = frames.shape
    split_row = int(H * beam_roi_fraction)
    n = min(n_analysis_frames, nframes)

    # Average of first N frames as float64
    avg = frames[:n].mean(axis=0).astype(np.float64)

    blobs_out = []
    blob_id = 0

    # ── Beam spots (top half) ──────────────────────────────────────────────
    top = avg[:split_row, :]
    top_max = top.max()
    if top_max > 0:
        top_norm = top / top_max
        raw = blob_log(
            top_norm,
            min_sigma=beam_min_sigma,
            max_sigma=beam_max_sigma,
            num_sigma=beam_num_sigma,
            threshold=beam_threshold,
        )
        # Sort by peak intensity descending, keep top N
        raw_sorted = sorted(
            raw,
            key=lambda b: _peak_intensity(top_norm, b[0], b[1]),
            reverse=True,
        )[:beam_max_blobs]

        for idx, b in enumerate(raw_sorted):
            y, x, sigma = b
            r = sigma * np.sqrt(2)
            blobs_out.append(dict(
                blob_id=blob_id,
                region="beam",
                center_x=float(x),
                center_y=float(y),          # in full-image coords (top half starts at 0)
                radius_px=float(r),
                color=_BEAM_COLORS[idx % len(_BEAM_COLORS)],
            ))
            blob_id += 1

    # ── Diffraction spots (bottom half) ───────────────────────────────────
    bottom = avg[split_row:, :]
    bottom_max = bottom.max()
    if bottom_max > 0:
        bottom_norm = bottom / bottom_max
        raw = blob_log(
            bottom_norm,
            min_sigma=diff_min_sigma,
            max_sigma=diff_max_sigma,
            num_sigma=diff_num_sigma,
            threshold=diff_threshold,
        )
        raw_sorted = sorted(
            raw,
            key=lambda b: _peak_intensity(bottom_norm, b[0], b[1]),
            reverse=True,
        )[:diff_max_blobs]

        for idx, b in enumerate(raw_sorted):
            y, x, sigma = b
            r = sigma * np.sqrt(2)
            blobs_out.append(dict(
                blob_id=blob_id,
                region="diffraction",
                center_x=float(x),
                center_y=float(y + split_row),  # offset to full-image y
                radius_px=float(r),
                color=_DIFF_COLORS[idx % len(_DIFF_COLORS)],
            ))
            blob_id += 1

    # ── Intensity tracing across ALL frames ───────────────────────────────
    for blob in blobs_out:
        cx, cy, r = blob["center_x"], blob["center_y"], blob["radius_px"]
        mask = _circular_mask(H, W, cy, cx, r)
        # frames[:, mask] → shape (nframes, n_pixels); mean over pixels
        intensities = frames[:, mask].astype(np.float64).mean(axis=1)
        blob["mean_intensities"] = intensities.tolist()

    return {
        "blobs": blobs_out,
        "n_blobs_beam": sum(1 for b in blobs_out if b["region"] == "beam"),
        "n_blobs_diffraction": sum(1 for b in blobs_out if b["region"] == "diffraction"),
        "n_analysis_frames_used": n,
        "split_row": split_row,
    }
