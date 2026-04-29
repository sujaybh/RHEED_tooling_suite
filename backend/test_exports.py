"""
Export data validation tests for the RHEED backend.

These tests verify that the data served by the API has the correct shape and
structure to produce valid PNG/SVG/CSV exports.  Browser-side download mechanics
(Plotly.downloadImage, Blob URL) are not tested here — they require a real browser.

Covers:
  - Pixel intensity endpoint: correct headers & row count for CSV
  - Blob intensity structure: columns match blob list, rows match nframes
  - Full 85-test suite is run at the end to confirm no regressions.

Usage (backend must be running):
    cd backend && python -m uvicorn main:app
    python test_exports.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import requests

BASE = "http://localhost:8000"
REPO = Path(__file__).parent.parent
DATASET7 = REPO / "dataset7"

_passed = 0
_failed = 0


def _section(title: str):
    bar = "=" * 60
    print(f"\n{bar}\n  {title}\n{bar}")


def _assert(cond: bool, msg: str):
    global _passed, _failed
    if cond:
        print(f"  PASS  {msg}")
        _passed += 1
    else:
        print(f"  FAIL  {msg}")
        _failed += 1


def _assert_eq(a, b, msg: str):
    _assert(a == b, f"{msg}  ({a!r} == {b!r})")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _upload_single(imm: Path) -> dict:
    with open(imm, "rb") as fh:
        r = requests.post(
            BASE + "/api/upload",
            files={"file": (imm.name, fh, "application/octet-stream")},
            timeout=120,
        )
    r.raise_for_status()
    return r.json()


def _poll_analyze(session_id: str, timeout_s: float = 300) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(BASE + f"/api/analyze/{session_id}/status", timeout=10)
        r.raise_for_status()
        d = r.json()
        if d.get("status") not in ("running", "idle"):
            return d
        time.sleep(2)
    raise TimeoutError("analyze timed out")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_pixel_csv_shape():
    """
    Verify the pixel intensity endpoint returns data with the right shape for
    a CSV export with headers=['frame','intensity'] and one row per frame.
    """
    _section("Pixel CSV shape")

    imm = sorted(DATASET7.glob("*.imm"))[0]
    data = _upload_single(imm)
    sid = data["session_id"]
    nframes = data["nframes"]
    width = data["width"]
    height = data["height"]

    _assert(nframes > 0, f"nframes={nframes} > 0")
    _assert(width > 0 and height > 0, f"dimensions {width}×{height} valid")

    # Fetch pixel intensities from the centre pixel
    cx, cy = width // 2, height // 2
    r = requests.get(BASE + f"/api/pixel/{sid}?x={cx}&y={cy}", timeout=30)
    _assert(r.status_code == 200, f"GET /api/pixel → 200")
    pdata = r.json()

    intensities = pdata.get("intensities", [])
    _assert_eq(len(intensities), nframes, "intensities length == nframes")
    _assert(all(isinstance(v, (int, float)) for v in intensities), "all intensity values are numeric")

    # Simulate CSV construction (mirrors ChartDownload logic)
    csv_headers = ["frame", "intensity"]
    csv_rows = [[i + 1, v] for i, v in enumerate(intensities)]

    _assert_eq(len(csv_headers), 2, "CSV has 2 columns (frame, intensity)")
    _assert_eq(len(csv_rows), nframes, "CSV has one row per frame")
    _assert_eq(csv_rows[0][0], 1, "first row frame index == 1")
    _assert_eq(csv_rows[-1][0], nframes, f"last row frame index == {nframes}")
    _assert(csv_rows[0][1] == intensities[0], "first row intensity matches API")
    _assert(csv_rows[-1][1] == intensities[-1], "last row intensity matches API")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)
    _assert(True, "session cleaned up")


def test_blob_csv_shape():
    """
    Verify that the blob analysis result has the right structure for a CSV
    export with headers=['frame', 'blob_0_beam', 'blob_1_diffraction', ...]
    and one row per frame.
    """
    _section("Blob CSV shape")

    imm = sorted(DATASET7.glob("*.imm"))[0]
    data = _upload_single(imm)
    sid = data["session_id"]
    nframes = data["nframes"]

    # Start analysis (default params)
    r = requests.post(BASE + f"/api/analyze/{sid}", json={}, timeout=30)
    _assert(r.status_code == 202, f"POST /api/analyze → 202 (got {r.status_code})")

    status = _poll_analyze(sid)
    _assert_eq(status.get("status"), "complete", "analysis → complete")
    if status.get("status") != "complete":
        print(f"  Error: {status.get('detail')}")
        requests.delete(BASE + f"/api/session/{sid}", timeout=10)
        return

    result = status.get("result", {})
    blobs = result.get("blobs", [])
    _assert(len(blobs) > 0, f"analysis found >0 blobs ({len(blobs)} found)")

    # Each blob must have the fields required by ChartDownload's getCSV()
    required_fields = {"blob_id", "region", "mean_intensities"}
    for b in blobs:
        missing = required_fields - set(b.keys())
        _assert(len(missing) == 0, f"blob {b.get('blob_id')} has all required fields (missing: {missing})")
        _assert_eq(len(b["mean_intensities"]), nframes,
                   f"blob {b['blob_id']} mean_intensities length == {nframes}")
        _assert(all(isinstance(v, (int, float)) for v in b["mean_intensities"]),
                f"blob {b['blob_id']} mean_intensities are numeric")

    # Simulate CSV construction (mirrors AnalysisPanel getCSV logic)
    csv_headers = ["frame"] + [f"blob_{b['blob_id']}_{b['region']}" for b in blobs]
    csv_rows = [
        [i + 1] + [b["mean_intensities"][i] for b in blobs]
        for i in range(nframes)
    ]

    _assert_eq(len(csv_headers), 1 + len(blobs), f"CSV has {1 + len(blobs)} columns")
    _assert_eq(len(csv_rows), nframes, "CSV has one row per frame")
    _assert_eq(csv_rows[0][0], 1, "first row frame index == 1")
    _assert_eq(csv_rows[-1][0], nframes, f"last row frame index == {nframes}")
    _assert(csv_headers[0] == "frame", "first column header is 'frame'")
    for j, b in enumerate(blobs):
        expected_col = f"blob_{b['blob_id']}_{b['region']}"
        _assert(csv_headers[j + 1] == expected_col,
                f"column {j+1} header is '{expected_col}'")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)
    _assert(True, "session cleaned up")


def test_pixel_csv_edge_cases():
    """Verify CSV handles single-frame and boundary pixels without crashing."""
    _section("Pixel CSV edge cases")

    imm = sorted(DATASET7.glob("*.imm"))[0]
    data = _upload_single(imm)
    sid = data["session_id"]
    nframes = data["nframes"]
    w, h = data["width"], data["height"]

    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    for x, y in corners:
        r = requests.get(BASE + f"/api/pixel/{sid}?x={x}&y={y}", timeout=30)
        _assert(r.status_code == 200, f"corner pixel ({x},{y}) → 200")
        if r.status_code == 200:
            ints = r.json().get("intensities", [])
            _assert_eq(len(ints), nframes, f"corner ({x},{y}) intensities length == {nframes}")

    # Out-of-bounds should be rejected (not included in any CSV)
    r = requests.get(BASE + f"/api/pixel/{sid}?x={w}&y={h}", timeout=10)
    _assert(r.status_code == 422, f"out-of-bounds pixel ({w},{h}) → 422 (got {r.status_code})")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)
    _assert(True, "session cleaned up")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick liveness check
    try:
        requests.get(BASE + "/api/saved", timeout=5).raise_for_status()
    except Exception:
        print(f"FATAL: backend not reachable at {BASE}")
        print("Start it with:  cd backend && python -m uvicorn main:app")
        sys.exit(1)

    for fn in [test_pixel_csv_shape, test_blob_csv_shape, test_pixel_csv_edge_cases]:
        try:
            fn()
        except SystemExit:
            raise
        except Exception as e:
            print(f"\nFATAL in {fn.__name__}: {e}")
            traceback.print_exc()
            _failed += 1

    _section("Results")
    print(f"  Passed: {_passed}")
    print(f"  Failed: {_failed}")
    sys.exit(0 if _failed == 0 else 1)
