"""
RHEED Analysis Suite — FastAPI backend
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rheed.api")

from imm_parser import auto_detect_format, parse_imm, frame_to_png_bytes
from blob_analysis import detect_and_track, _circular_mask, assign_strips
import database as db

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="RHEED Analysis Suite", version="0.1.0")
db.init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=2)

# ── Session store ──────────────────────────────────────────────────────────────

@dataclass
class RheedSession:
    session_id: str
    filename: str
    mode: str
    width: int
    height: int
    nframes: int
    frames: np.ndarray            # (nframes, H, W) uint16  or  (nframes, H, W, 3) uint32
    analysis_result: Optional[dict] = None
    analysis_status: str = "idle"  # idle | running | complete | error
    analysis_error: Optional[str] = None
    _analysis_task: Optional[asyncio.Task] = field(default=None, repr=False)


_sessions: dict[str, RheedSession] = {}


# ── Multi-strip session ────────────────────────────────────────────────────────

@dataclass
class MultiRheedSession:
    session_id: str
    filenames: list
    frames_list: list              # list of np.ndarray, each (N_k, H, W)
    width: int
    height: int
    nframes: int                   # min frames across all files
    mode: str
    analysis_status: str = "idle"
    analysis_error: Optional[str] = None
    # assignments[fixed_strip_i][frame_n] = original_strip_j
    assignments: Optional[list] = None
    # reference beam-center for each fixed strip (= frame-0 center of original strip)
    reference_centers: Optional[list] = None
    _analysis_task: Optional[asyncio.Task] = field(default=None, repr=False)


_multi_sessions: dict[str, MultiRheedSession] = {}


def _get_session(session_id: str) -> RheedSession:
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return _sessions[session_id]


# ── Pydantic models ────────────────────────────────────────────────────────────

class BlobIntensityRequest(BaseModel):
    center_x: float
    center_y: float
    radius_px: float


class AnalyzeParams(BaseModel):
    n_analysis_frames: int = 5
    beam_roi_fraction: float = 0.5
    beam_min_sigma: float = 5.0
    beam_max_sigma: float = 40.0
    beam_num_sigma: int = 8
    beam_threshold: float = 0.15
    beam_max_blobs: int = 3
    diff_min_sigma: float = 2.0
    diff_max_sigma: float = 20.0
    diff_num_sigma: int = 10
    diff_threshold: float = 0.04
    diff_max_blobs: int = 15


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    width: Optional[int] = Query(default=None),
    height: Optional[int] = Query(default=None),
    mode: Optional[str] = Query(default=None),
):
    raw = await file.read()

    # Auto-detect (or use forced dims). Run in executor so we don't block.
    loop = asyncio.get_running_loop()
    try:
        frames, fmt = await loop.run_in_executor(
            _executor,
            lambda: parse_imm(raw, force_width=width, force_height=height, force_mode=mode),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    session_id = uuid.uuid4().hex
    _sessions[session_id] = RheedSession(
        session_id=session_id,
        filename=file.filename or "unknown.imm",
        mode=fmt["mode"],
        width=fmt["width"],
        height=fmt["height"],
        nframes=fmt["nframes"],
        frames=frames,
    )

    return {
        "session_id": session_id,
        "filename": file.filename,
        "mode": fmt["mode"],
        "width": fmt["width"],
        "height": fmt["height"],
        "nframes": fmt["nframes"],
        "frame_header_size": fmt["frame_header_size"],
    }


@app.get("/api/frame/{session_id}/{frame_index}")
async def get_frame(
    session_id: str,
    frame_index: int,
    contrast: float = Query(default=1.0, ge=0.1, le=20.0),
):
    sess = _get_session(session_id)
    if frame_index < 0 or frame_index >= sess.nframes:
        raise HTTPException(status_code=416, detail=f"frame_index must be in [0, {sess.nframes - 1}]")

    loop = asyncio.get_running_loop()
    png_bytes = await loop.run_in_executor(
        _executor,
        lambda: frame_to_png_bytes(sess.frames[frame_index], sess.mode, contrast),
    )

    import io
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


@app.get("/api/pixel/{session_id}")
async def get_pixel(
    session_id: str,
    x: int = Query(...),
    y: int = Query(...),
):
    sess = _get_session(session_id)
    if not (0 <= x < sess.width and 0 <= y < sess.height):
        raise HTTPException(status_code=422, detail="Pixel coordinates out of range")

    if sess.mode == "gray16":
        intensities = sess.frames[:, y, x].tolist()
    else:
        # rgb96: mean of 3 channels as proxy for intensity
        intensities = sess.frames[:, y, x, :].mean(axis=-1).tolist()

    return {"x": x, "y": y, "intensities": intensities}


@app.post("/api/analyze/{session_id}", status_code=202)
async def start_analyze(session_id: str, params: AnalyzeParams):
    sess = _get_session(session_id)

    if sess.analysis_status == "running":
        raise HTTPException(status_code=409, detail="Analysis already running")

    sess.analysis_status = "running"
    sess.analysis_result = None
    sess.analysis_error = None

    loop = asyncio.get_running_loop()

    async def _run():
        try:
            result = await loop.run_in_executor(
                _executor,
                lambda: detect_and_track(
                    sess.frames,
                    n_analysis_frames=params.n_analysis_frames,
                    beam_roi_fraction=params.beam_roi_fraction,
                    beam_min_sigma=params.beam_min_sigma,
                    beam_max_sigma=params.beam_max_sigma,
                    beam_num_sigma=params.beam_num_sigma,
                    beam_threshold=params.beam_threshold,
                    beam_max_blobs=params.beam_max_blobs,
                    diff_min_sigma=params.diff_min_sigma,
                    diff_max_sigma=params.diff_max_sigma,
                    diff_num_sigma=params.diff_num_sigma,
                    diff_threshold=params.diff_threshold,
                    diff_max_blobs=params.diff_max_blobs,
                ),
            )
            sess.analysis_result = result
            sess.analysis_status = "complete"
        except Exception as e:
            sess.analysis_status = "error"
            sess.analysis_error = str(e)

    sess._analysis_task = asyncio.create_task(_run())

    return {"status": "running", "session_id": session_id}


@app.get("/api/analyze/{session_id}/status")
async def get_analyze_status(session_id: str):
    sess = _get_session(session_id)

    if sess.analysis_status == "idle":
        return {"status": "idle"}
    if sess.analysis_status == "running":
        return {"status": "running"}
    if sess.analysis_status == "error":
        return {"status": "error", "detail": sess.analysis_error}
    # complete
    return {"status": "complete", "result": sess.analysis_result}


@app.post("/api/blob-intensities/{session_id}")
async def get_blob_intensities(session_id: str, req: BlobIntensityRequest):
    """Compute mean intensity inside a circular ROI across all frames."""
    sess = _get_session(session_id)

    def compute():
        mask = _circular_mask(sess.height, sess.width, req.center_y, req.center_x, req.radius_px)
        if sess.mode == "gray16":
            intensities = sess.frames[:, mask].astype(np.float64).mean(axis=1)
        else:
            intensities = sess.frames[:, mask].astype(np.float64).mean(axis=(1, 2))
        return intensities.tolist()

    loop = asyncio.get_running_loop()
    mean_intensities = await loop.run_in_executor(_executor, compute)
    return {"mean_intensities": mean_intensities}


@app.post("/api/multi-upload")
async def multi_upload(files: list[UploadFile] = File(...)):
    """Accept K .imm files and create a multi-strip session."""
    if len(files) < 2:
        raise HTTPException(status_code=422, detail="At least 2 files required")

    loop = asyncio.get_running_loop()
    frames_list = []
    filenames = []
    fmt_ref = None

    for uf in files:
        raw = await uf.read()
        try:
            frames, fmt = await loop.run_in_executor(
                _executor, lambda r=raw: parse_imm(r),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{uf.filename}: {e}")
        finally:
            del raw  # free raw bytes before parsing the next file
        frames_list.append(frames)
        filenames.append(uf.filename or "unknown.imm")
        if fmt_ref is None:
            fmt_ref = fmt
        elif fmt["width"] != fmt_ref["width"] or fmt["height"] != fmt_ref["height"]:
            raise HTTPException(
                status_code=422,
                detail=f"{uf.filename}: dimensions {fmt['width']}×{fmt['height']} don't match "
                       f"first file {fmt_ref['width']}×{fmt_ref['height']}",
            )

    session_id = uuid.uuid4().hex
    _multi_sessions[session_id] = MultiRheedSession(
        session_id=session_id,
        filenames=filenames,
        frames_list=frames_list,
        width=fmt_ref["width"],
        height=fmt_ref["height"],
        nframes=min(f.shape[0] for f in frames_list),
        mode=fmt_ref["mode"],
    )

    return {
        "session_id": session_id,
        "filenames": filenames,
        "nstrips": len(files),
        "nframes": _multi_sessions[session_id].nframes,
        "width": fmt_ref["width"],
        "height": fmt_ref["height"],
        "mode": fmt_ref["mode"],
    }


@app.post("/api/multi-analyze/{session_id}", status_code=202)
async def start_multi_analyze(session_id: str):
    if session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    sess = _multi_sessions[session_id]

    if sess.analysis_status == "running":
        raise HTTPException(status_code=409, detail="Analysis already running")

    sess.analysis_status = "running"
    sess.assignments = None
    sess.reference_centers = None
    sess.analysis_error = None

    loop = asyncio.get_running_loop()

    async def _run():
        try:
            assignments, ref_centers = await loop.run_in_executor(
                _executor,
                lambda: assign_strips(sess.frames_list),
            )
            sess.assignments = assignments
            sess.reference_centers = [{"x": cx, "y": cy} for cx, cy in ref_centers]
            sess.analysis_status = "complete"
        except Exception as e:
            sess.analysis_status = "error"
            sess.analysis_error = str(e)

    sess._analysis_task = asyncio.create_task(_run())
    return {"status": "running", "session_id": session_id}


@app.get("/api/multi-analyze/{session_id}/status")
async def get_multi_analyze_status(session_id: str):
    if session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    sess = _multi_sessions[session_id]

    if sess.analysis_status in ("idle", "running"):
        return {"status": sess.analysis_status}
    if sess.analysis_status == "error":
        return {"status": "error", "detail": sess.analysis_error}
    return {
        "status": "complete",
        "assignments": sess.assignments,
        "reference_centers": sess.reference_centers,
    }


@app.get("/api/multi-frame/{session_id}/{strip_index}/{frame_index}")
async def get_multi_frame(
    session_id: str,
    strip_index: int,
    frame_index: int,
    contrast: float = Query(default=1.0, ge=0.1, le=20.0),
):
    if session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    sess = _multi_sessions[session_id]

    if sess.assignments is None:
        raise HTTPException(status_code=400, detail="Analysis not complete")
    if not (0 <= strip_index < len(sess.assignments)):
        raise HTTPException(status_code=416, detail="strip_index out of range")
    if not (0 <= frame_index < sess.nframes):
        raise HTTPException(status_code=416, detail="frame_index out of range")

    orig_strip = sess.assignments[strip_index][frame_index]
    frame = sess.frames_list[orig_strip][frame_index]

    loop = asyncio.get_running_loop()
    png_bytes = await loop.run_in_executor(
        _executor,
        lambda: frame_to_png_bytes(frame, sess.mode, contrast),
    )

    import io as _io
    return StreamingResponse(_io.BytesIO(png_bytes), media_type="image/png")


@app.post("/api/multi-session/{multi_session_id}/export/{strip_index}")
async def export_strip_as_session(multi_session_id: str, strip_index: int):
    """Materialise one fixed strip as a regular RheedSession for full analysis."""
    if multi_session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    msess = _multi_sessions[multi_session_id]

    if msess.assignments is None:
        raise HTTPException(status_code=400, detail="Analysis not complete")
    if not (0 <= strip_index < len(msess.assignments)):
        raise HTTPException(status_code=416, detail="strip_index out of range")

    def build_frames():
        return np.stack([
            msess.frames_list[msess.assignments[strip_index][n]][n]
            for n in range(msess.nframes)
        ])

    loop = asyncio.get_running_loop()
    frames = await loop.run_in_executor(_executor, build_frames)

    session_id = uuid.uuid4().hex
    filename = msess.filenames[strip_index] if strip_index < len(msess.filenames) else f"strip_{strip_index + 1}.imm"
    _sessions[session_id] = RheedSession(
        session_id=session_id,
        filename=f"[Fixed] {filename}",
        mode=msess.mode,
        width=msess.width,
        height=msess.height,
        nframes=msess.nframes,
        frames=frames,
    )
    return {
        "session_id": session_id,
        "filename": f"[Fixed] {filename}",
        "mode": msess.mode,
        "width": msess.width,
        "height": msess.height,
        "nframes": msess.nframes,
    }


@app.delete("/api/multi-session/{session_id}")
async def delete_multi_session(session_id: str):
    if session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    del _multi_sessions[session_id]
    return {"deleted": session_id}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    _get_session(session_id)
    del _sessions[session_id]
    return {"deleted": session_id}


# ── Library: save / load / list / delete ──────────────────────────────────────

class BlobPayload(BaseModel):
    blob_id: int
    region: str
    center_x: float
    center_y: float
    radius_px: float
    color: str
    mean_intensities: list[float]


class AnalysisMeta(BaseModel):
    split_row: Optional[int] = None
    n_analysis_frames_used: Optional[int] = None


class SaveSingleRequest(BaseModel):
    session_id: str
    name: Optional[str] = None
    blobs: Optional[list[BlobPayload]] = None
    analysis_meta: Optional[AnalysisMeta] = None
    replace_instance_id: Optional[str] = None   # if set, delete old instance atomically


class SaveMultiRequest(BaseModel):
    multi_session_id: str
    name: Optional[str] = None
    replace_instance_id: Optional[str] = None   # if set, delete old instance atomically


@app.post("/api/save/single", status_code=201)
async def save_single(req: SaveSingleRequest):
    t0 = time.perf_counter()
    sess = _get_session(req.session_id)
    name = req.name or sess.filename

    log.info("[POST /save/single] session=%s  name=%r  nframes=%d  shape=%s  blobs=%d  replace=%s",
             req.session_id, name, sess.nframes, sess.frames.shape,
             len(req.blobs) if req.blobs else 0, req.replace_instance_id)

    analysis_meta_dict = req.analysis_meta.model_dump() if req.analysis_meta else {}
    blobs_list = [b.model_dump() for b in req.blobs] if req.blobs else []

    log.info("[POST /save/single] serialised %d blobs, handing off to executor …", len(blobs_list))

    loop = asyncio.get_running_loop()
    replace_id = req.replace_instance_id

    def _save():
        log.info("[_save/single] executor thread started")
        if replace_id:
            log.info("[_save/single] deleting old instance %s …", replace_id)
            db.delete_instance(replace_id)
        result = db.save_single_instance(
            name, sess.filename, sess.mode,
            sess.width, sess.height, sess.nframes,
            sess.frames, analysis_meta_dict, blobs_list,
        )
        log.info("[_save/single] executor thread done")
        return result

    inst_id = await loop.run_in_executor(_executor, _save)
    log.info("[POST /save/single] returning 201  inst_id=%s  total=%.2fs", inst_id, time.perf_counter() - t0)
    return {"instance_id": inst_id, "name": name}


@app.post("/api/save/multi", status_code=201)
async def save_multi(req: SaveMultiRequest):
    t0 = time.perf_counter()
    if req.multi_session_id not in _multi_sessions:
        raise HTTPException(status_code=404, detail="Multi-session not found")
    msess = _multi_sessions[req.multi_session_id]

    if msess.analysis_status != "complete" or msess.assignments is None:
        raise HTTPException(status_code=400, detail="Analysis must be complete before saving")

    name = req.name or f"Multi-strip ({len(msess.filenames)} files)"
    log.info("[POST /save/multi] session=%s  name=%r  nstrips=%d  nframes=%d  replace=%s",
             req.multi_session_id, name, len(msess.filenames), msess.nframes, req.replace_instance_id)

    loop = asyncio.get_running_loop()
    frames_list = msess.frames_list
    filenames = msess.filenames
    replace_id = req.replace_instance_id

    def _save():
        log.info("[_save/multi] executor thread started")
        if replace_id:
            log.info("[_save/multi] deleting old instance %s …", replace_id)
            db.delete_instance(replace_id)
        result = db.save_multi_instance(
            name, filenames, frames_list, msess.mode,
            msess.width, msess.height, msess.nframes,
            msess.assignments, msess.reference_centers,
        )
        log.info("[_save/multi] executor thread done")
        return result

    inst_id = await loop.run_in_executor(_executor, _save)
    log.info("[POST /save/multi] returning 201  inst_id=%s  total=%.2fs", inst_id, time.perf_counter() - t0)
    return {"instance_id": inst_id, "name": name}


@app.get("/api/saved")
async def list_saved():
    instances = db.list_instances()
    return {"instances": instances}


@app.get("/api/saved/{instance_id}")
async def get_saved_instance(instance_id: str):
    inst = db.get_instance_with_videos(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Saved instance not found")
    return inst


@app.post("/api/saved/{instance_id}/load")
async def load_saved(instance_id: str):
    inst = db.get_instance_with_videos(instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="Saved instance not found")

    loop = asyncio.get_running_loop()

    if inst["type"] == "single":
        if not inst["videos"]:
            raise HTTPException(status_code=500, detail="Saved instance has no video data — record may be corrupted")
        vid = inst["videos"][0]

        def _load():
            return db.load_frames(vid["frames_path"])

        frames = await loop.run_in_executor(_executor, _load)
        session_id = uuid.uuid4().hex

        blobs = vid["blobs"]
        has_analysis = len(blobs) > 0
        analysis_result = None
        if has_analysis:
            ameta = vid["analysis_meta"]
            analysis_result = {
                "blobs": blobs,
                "n_blobs_beam": sum(1 for b in blobs if b["region"] == "beam"),
                "n_blobs_diffraction": sum(1 for b in blobs if b["region"] == "diffraction"),
                "n_analysis_frames_used": ameta.get("n_analysis_frames_used", 0),
                "split_row": ameta.get("split_row", frames.shape[1] // 2),
            }

        _sessions[session_id] = RheedSession(
            session_id=session_id,
            filename=vid["filename"],
            mode=vid["mode"],
            width=vid["width"],
            height=vid["height"],
            nframes=vid["nframes"],
            frames=frames,
            analysis_result=analysis_result,
            analysis_status="complete" if has_analysis else "idle",
        )
        return {
            "type": "single",
            "session_id": session_id,
            "filename": vid["filename"],
            "mode": vid["mode"],
            "width": vid["width"],
            "height": vid["height"],
            "nframes": vid["nframes"],
            "analysis_result": analysis_result,
        }

    else:  # multi
        meta = inst["metadata"]
        videos = inst["videos"]  # sorted by strip_index

        def _load_all():
            return [db.load_frames(v["frames_path"]) for v in videos]

        frames_list = await loop.run_in_executor(_executor, _load_all)

        session_id = uuid.uuid4().hex
        _multi_sessions[session_id] = MultiRheedSession(
            session_id=session_id,
            filenames=[v["filename"] for v in videos],
            frames_list=frames_list,
            width=meta["width"],
            height=meta["height"],
            nframes=meta["nframes"],
            mode=meta["mode"],
            analysis_status="complete",
            assignments=meta.get("assignments"),
            reference_centers=meta.get("reference_centers"),
        )
        return {
            "type": "multi",
            "session_id": session_id,
            "filenames": [v["filename"] for v in videos],
            "nstrips": len(videos),
            "nframes": meta["nframes"],
            "width": meta["width"],
            "height": meta["height"],
            "mode": meta["mode"],
            "assignments": meta.get("assignments"),
            "reference_centers": meta.get("reference_centers"),
        }


@app.delete("/api/saved/{instance_id}")
async def delete_saved_instance(instance_id: str):
    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(_executor, lambda: db.delete_instance(instance_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved instance not found")
    return {"deleted": instance_id}


# ── Serve built frontend (production) ─────────────────────────────────────────

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
