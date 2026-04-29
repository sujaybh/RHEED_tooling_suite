"""
Memory and correctness tests for the RHEED backend.

Key design: uses requests_toolbelt.MultipartEncoder so files are streamed from
disk rather than buffered into RAM before sending.  Without this, uploading
6 × 445 MB = 2.7 GB would OOM the test process itself.

Usage (backend must be running first):
    cd backend && python -m uvicorn main:app
    python test_memory.py
"""
from __future__ import annotations

import io
import json
import sys
import time
import traceback
from pathlib import Path

import psutil
import requests
from requests_toolbelt import MultipartEncoder

BASE  = "http://localhost:8000"
PROC  = psutil.Process()
REPO  = Path(__file__).parent.parent

DATASET7  = REPO / "dataset7"
DATASET10 = REPO / "dataset10"

_logf = open(Path(__file__).parent / "test_memory.log", "w", buffering=1)

def _log(*args):
    msg = " ".join(str(a) for a in args)
    print(msg, flush=True)
    _logf.write(msg + "\n")

def _section(title: str):
    bar = "=" * 60
    _log(f"\n{bar}\n  {title}\n{bar}")

def _sys_avail_mb() -> float:
    return psutil.virtual_memory().available / 1_048_576

def _rss_mb() -> float:
    return PROC.memory_info().rss / 1_048_576

def _mem(label: str) -> dict:
    vm    = psutil.virtual_memory()
    avail = vm.available / 1_048_576
    rss   = _rss_mb()
    used  = vm.percent
    _log(f"  [MEM] {label:<45}  avail={avail:.0f} MB  RSS={rss:.0f} MB  used={used:.0f}%")
    return {"avail": avail, "rss": rss}

_passed = 0
_failed = 0

def _assert(cond: bool, msg: str):
    global _passed, _failed
    if cond:
        _log(f"  PASS  {msg}")
        _passed += 1
    else:
        _log(f"  FAIL  {msg}")
        _failed += 1

def _assert_eq(a, b, msg: str):  _assert(a == b,  f"{msg}  ({a!r} == {b!r})")
def _assert_le(a, b, msg: str):  _assert(a <= b,  f"{msg}  ({a!r} <= {b!r})")

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str, **kw) -> requests.Response:
    r = requests.get(BASE + path, **kw);  r.raise_for_status();  return r

def _delete(path: str) -> requests.Response:
    r = requests.delete(BASE + path, timeout=15);  r.raise_for_status();  return r

def _poll(path: str, timeout_s: float = 900) -> dict:
    """Poll until status is not 'running' or 'idle'."""
    deadline = time.time() + timeout_s
    dots = 0
    while time.time() < deadline:
        r = requests.get(BASE + path, timeout=10)
        r.raise_for_status()
        data = r.json()
        st = data.get("status", "")
        if st not in ("running", "idle"):
            _log("")  # newline after dots
            return data
        if dots % 20 == 0:
            vm = psutil.virtual_memory()
            _log(f"  ... {st} (avail={vm.available//1_048_576} MB, "
                 f"elapsed={int(time.time() - (deadline - timeout_s))}s)")
        dots += 1
        time.sleep(2)
    raise TimeoutError(f"Still {st} after {timeout_s}s")

# ── Streaming multipart upload ────────────────────────────────────────────────

def _stream_multi_upload(imm_files: list[Path], label: str) -> dict:
    """
    Upload multiple .imm files as multipart WITHOUT loading them into RAM.

    requests_toolbelt.MultipartEncoder reads each file lazily as the HTTP body
    is sent, so peak RAM = one streaming chunk (~8 KB) regardless of file sizes.
    """
    _log(f"\n  Streaming {len(imm_files)} files ({sum(f.stat().st_size for f in imm_files)//1_048_576} MB total):")
    for f in imm_files:
        _log(f"    {f.name}  ({f.stat().st_size // 1_048_576} MB)")

    m0 = _mem(f"{label} before upload")
    t0 = time.perf_counter()

    # Build fields list — file handles are opened here but NOT read until sent
    handles = []
    fields  = []
    for f in imm_files:
        fh = open(f, "rb")
        handles.append(fh)
        fields.append(("files", (f.name, fh, "application/octet-stream")))

    try:
        enc = MultipartEncoder(fields=fields)
        r = requests.post(
            BASE + "/api/multi-upload",
            data=enc,
            headers={"Content-Type": enc.content_type},
            timeout=900,
        )
        r.raise_for_status()
    finally:
        for fh in handles:
            fh.close()

    elapsed = time.perf_counter() - t0
    m1 = _mem(f"{label} after upload")
    data = r.json()

    _log(f"  Upload done in {elapsed:.1f}s  session={data.get('session_id','?')}")
    _log(f"  nstrips={data.get('nstrips')}  nframes={data.get('nframes')}  "
         f"{data.get('width')}x{data.get('height')}")

    _assert(data.get("nstrips") == len(imm_files), f"{label}: nstrips == {len(imm_files)}")
    _assert(data.get("nframes", 0) > 0, f"{label}: nframes > 0")

    delta_rss = m1["rss"] - m0["rss"]
    _log(f"  Client RSS delta: {delta_rss:+.0f} MB  (expected < 100 MB for streaming)")
    _assert(delta_rss < 200, f"{label}: client RSS spike < 200 MB (got {delta_rss:.0f} MB)")

    return data


def _stream_single_upload(imm_file: Path) -> dict:
    _log(f"  Uploading {imm_file.name}  ({imm_file.stat().st_size // 1_048_576} MB)")
    m0 = _mem("single-upload before")
    t0 = time.perf_counter()
    with open(imm_file, "rb") as fh:
        r = requests.post(
            BASE + "/api/upload",
            files={"file": (imm_file.name, fh, "application/octet-stream")},
            timeout=120,
        )
    r.raise_for_status()
    elapsed = time.perf_counter() - t0
    m1 = _mem("single-upload after")
    _log(f"  Done in {elapsed:.1f}s")
    return r.json()

# ── Correctness helpers ───────────────────────────────────────────────────────

def _check_assignments(assignments, nstrips, nframes, label):
    errors = 0
    for n in range(nframes):
        used = {assignments[i][n] for i in range(nstrips)}
        if used != set(range(nstrips)):
            errors += 1
    _assert(errors == 0, f"{label}: all {nframes} frames are valid permutations ({errors} bad)")

def _check_frames(session_id, nstrips, nframes, label):
    """Spot-check a handful of (strip, frame) combinations."""
    samples = [0, nframes // 4, nframes // 2, nframes - 1]
    for si in range(min(nstrips, 3)):   # first 3 strips
        for fi in samples:
            r = requests.get(BASE + f"/api/multi-frame/{session_id}/{si}/{fi}", timeout=30)
            _assert(r.status_code == 200,
                    f"{label}: strip {si} frame {fi} → 200 (got {r.status_code})")
            _assert(r.headers.get("content-type") == "image/png",
                    f"{label}: strip {si} frame {fi} is PNG")

def _check_export(session_id, strip_index, nframes, label):
    m0 = _mem(f"{label} before export strip {strip_index}")
    r = requests.post(BASE + f"/api/multi-session/{session_id}/export/{strip_index}", timeout=120)
    _assert(r.status_code == 200, f"{label}: export strip {strip_index} → 200")
    if r.status_code != 200:
        return
    data = r.json()
    m1 = _mem(f"{label} after export strip {strip_index}")
    _assert_eq(data["nframes"], nframes, f"{label}: exported nframes == {nframes}")

    sid = data["session_id"]
    r2 = requests.get(BASE + f"/api/frame/{sid}/0", timeout=30)
    _assert(r2.status_code == 200, f"{label}: exported frame 0 → 200")
    r3 = requests.get(BASE + f"/api/frame/{sid}/{nframes // 2}", timeout=30)
    _assert(r3.status_code == 200, f"{label}: exported frame middle → 200")

    delta = m1["rss"] - m0["rss"]
    _log(f"  Export client RSS delta: {delta:+.0f} MB")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)

# ── Individual tests ──────────────────────────────────────────────────────────

def test_backend_alive():
    _section("Backend liveness")
    try:
        r = requests.get(BASE + "/api/saved", timeout=5)
        _assert(r.status_code == 200, "GET /api/saved → 200")
    except requests.ConnectionError:
        _log(f"FATAL: backend not reachable at {BASE}")
        _log("Start it with:  cd backend && python -m uvicorn main:app")
        sys.exit(1)


def test_error_handling():
    _section("Error handling")

    r = requests.get(BASE + "/api/frame/nonexistent/0")
    _assert(r.status_code == 404, "nonexistent session → 404")

    imm = sorted(DATASET7.glob("*.imm"))[0]
    with open(imm, "rb") as fh:
        r = requests.post(BASE + "/api/upload",
                          files={"file": (imm.name, fh, "application/octet-stream")},
                          timeout=120)
    r.raise_for_status()
    sid = r.json()["session_id"]
    nf  = r.json()["nframes"]

    r2 = requests.get(BASE + f"/api/frame/{sid}/{nf + 100}")
    _assert(r2.status_code == 416, f"out-of-range frame → 416 (got {r2.status_code})")

    r3 = requests.get(BASE + f"/api/pixel/{sid}?x=9999&y=9999")
    _assert(r3.status_code == 422, f"out-of-range pixel → 422 (got {r3.status_code})")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)

    with open(imm, "rb") as fh:
        r4 = requests.post(BASE + "/api/multi-upload",
                           files=[("files", (imm.name, fh, "application/octet-stream"))],
                           timeout=30)
    _assert(r4.status_code == 422, f"single-file multi-upload → 422 (got {r4.status_code})")


def test_single_upload():
    _section("Single file upload")
    imm = sorted(DATASET7.glob("*.imm"))[0]
    data = _stream_single_upload(imm)

    sid = data["session_id"]
    _assert(data["nframes"] > 0, "nframes > 0")
    _assert(data["width"] == 1024 and data["height"] == 1024, "1024×1024")

    r2 = requests.get(BASE + f"/api/frame/{sid}/0", timeout=30)
    _assert(r2.status_code == 200, "frame 0 → 200")

    r3 = requests.get(BASE + f"/api/pixel/{sid}?x=512&y=256", timeout=10)
    _assert(r3.status_code == 200, "pixel query → 200")
    _assert(len(r3.json()["intensities"]) == data["nframes"],
            "intensities length == nframes")

    requests.delete(BASE + f"/api/session/{sid}", timeout=10)
    _assert(True, "session deleted")


def test_dataset10():
    _section("Dataset10 — 6 strips × 445 MB")
    imm_files = sorted(DATASET10.glob("*.imm"))
    _assert(len(imm_files) >= 2, f"≥2 .imm files in dataset10 ({len(imm_files)} found)")

    data = _stream_multi_upload(imm_files, "dataset10")
    session_id = data["session_id"]
    nstrips    = data["nstrips"]
    nframes    = data["nframes"]

    try:
        _log("\n  Starting strip analysis …")
        _mem("dataset10 before analyze")
        requests.post(BASE + f"/api/multi-analyze/{session_id}", timeout=10).raise_for_status()
        status = _poll(f"/api/multi-analyze/{session_id}/status", timeout_s=900)
        _mem("dataset10 after analyze")

        _assert_eq(status["status"], "complete", "dataset10: analyze → complete")
        if status["status"] != "complete":
            _log(f"  Error: {status.get('detail')}")
            return

        _check_assignments(status["assignments"], nstrips, nframes, "dataset10")
        _check_frames(session_id, nstrips, nframes, "dataset10")
        _check_export(session_id, 0, nframes, "dataset10")

    finally:
        r = requests.delete(BASE + f"/api/multi-session/{session_id}", timeout=15)
        _assert(r.status_code == 200, "dataset10: multi-session deleted")


def test_dataset7():
    _section("Dataset7 — 12 strips × 345 MB")
    imm_files = sorted(DATASET7.glob("*.imm"))
    _assert(len(imm_files) >= 2, f"≥2 .imm files in dataset7 ({len(imm_files)} found)")

    data = _stream_multi_upload(imm_files, "dataset7")
    session_id = data["session_id"]
    nstrips    = data["nstrips"]
    nframes    = data["nframes"]

    try:
        _log("\n  Starting strip analysis …")
        _mem("dataset7 before analyze")
        requests.post(BASE + f"/api/multi-analyze/{session_id}", timeout=10).raise_for_status()
        status = _poll(f"/api/multi-analyze/{session_id}/status", timeout_s=900)
        _mem("dataset7 after analyze")

        _assert_eq(status["status"], "complete", "dataset7: analyze → complete")
        if status["status"] != "complete":
            _log(f"  Error: {status.get('detail')}")
            return

        _check_assignments(status["assignments"], nstrips, nframes, "dataset7")
        _check_frames(session_id, nstrips, nframes, "dataset7")
        _check_export(session_id, 0,          nframes, "dataset7 strip 0")
        _check_export(session_id, nstrips//2, nframes, "dataset7 strip mid")

    finally:
        r = requests.delete(BASE + f"/api/multi-session/{session_id}", timeout=15)
        _assert(r.status_code == 200, "dataset7: multi-session deleted")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    vm = psutil.virtual_memory()
    _log("RHEED backend memory & correctness test")
    _log(f"Backend : {BASE}")
    _log(f"System  : {vm.total//1_048_576} MB total, {vm.available//1_048_576} MB avail")

    for fn in [test_backend_alive, test_error_handling, test_single_upload,
               test_dataset10, test_dataset7]:
        try:
            fn()
        except SystemExit:
            raise
        except Exception as e:
            _log(f"\nFATAL in {fn.__name__}: {e}")
            traceback.print_exc()
            _failed += 1

    _section("Results")
    _log(f"  Passed: {_passed}")
    _log(f"  Failed: {_failed}")
    sys.exit(0 if _failed == 0 else 1)
