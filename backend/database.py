"""
SQLite persistence layer for RHEED Analysis Suite.

Instances are stored in rheed_library.db.
Frames are stored as raw .npy files in saved_data/ (NOT compressed — np.save
is O(memcpy) fast; np.savez_compressed runs full zlib on every frame which
can take several minutes for a 345 MB uint16 stack).

Python 3.12 deprecated implicit transaction control (isolation_level="").
We use isolation_level=None (autocommit) throughout and issue explicit
BEGIN / COMMIT around multi-statement write operations.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("rheed.db")

DB_PATH = Path(__file__).parent / "rheed_library.db"
FRAMES_DIR = Path(__file__).parent / "saved_data"
FRAMES_DIR.mkdir(exist_ok=True)


# ── Connection ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    """Open a connection in autocommit mode (isolation_level=None).
    Each caller is responsible for explicit BEGIN/COMMIT when needed."""
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS saved_instances (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            type        TEXT NOT NULL CHECK(type IN ('single', 'multi')),
            created_at  TEXT NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS saved_videos (
            id            TEXT PRIMARY KEY,
            instance_id   TEXT NOT NULL,
            strip_index   INTEGER,
            filename      TEXT NOT NULL,
            mode          TEXT NOT NULL,
            width         INTEGER NOT NULL,
            height        INTEGER NOT NULL,
            nframes       INTEGER NOT NULL,
            frames_path   TEXT NOT NULL,
            analysis_meta TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (instance_id) REFERENCES saved_instances(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS saved_blobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id         TEXT NOT NULL,
            blob_id          INTEGER NOT NULL,
            region           TEXT NOT NULL,
            center_x         REAL NOT NULL,
            center_y         REAL NOT NULL,
            radius_px        REAL NOT NULL,
            color            TEXT NOT NULL,
            mean_intensities TEXT NOT NULL,
            FOREIGN KEY (video_id) REFERENCES saved_videos(id) ON DELETE CASCADE
        );
        """)
    finally:
        conn.close()


# ── Frame I/O ──────────────────────────────────────────────────────────────────

def save_frames(frames: np.ndarray, video_id: str) -> str:
    """Write frames as a raw .npy file (uncompressed, O(memcpy) speed)."""
    path = FRAMES_DIR / f"{video_id}.npy"
    t0 = time.perf_counter()
    np.save(str(path), frames)
    mb = path.stat().st_size / 1_048_576
    log.info("  [frames] wrote %s  shape=%s dtype=%s  %.1f MB  %.2fs",
             path.name, frames.shape, frames.dtype, mb, time.perf_counter() - t0)
    return str(path)


def load_frames(frames_path: str) -> np.ndarray:
    t0 = time.perf_counter()
    # Support both old .npz saves and new .npy saves
    if frames_path.endswith(".npz"):
        arr = np.load(frames_path)["frames"]
    else:
        arr = np.load(frames_path)
    log.info("  [frames] loaded %s  shape=%s  %.2fs",
             Path(frames_path).name, arr.shape, time.perf_counter() - t0)
    return arr


def _delete_frames(frames_path: str) -> None:
    p = Path(frames_path)
    if p.exists():
        p.unlink()
        log.info("  [frames] deleted %s", p.name)


# ── Atomic save helpers ────────────────────────────────────────────────────────

def save_single_instance(
    name: str,
    filename: str,
    mode: str,
    width: int,
    height: int,
    nframes: int,
    frames: np.ndarray,
    analysis_meta: dict,
    blobs: list[dict],
) -> str:
    """Save a single-video instance atomically. Returns the new instance_id."""
    t_total = time.perf_counter()
    inst_id = uuid.uuid4().hex
    video_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    metadata = {"nframes": nframes, "width": width, "height": height, "mode": mode}

    log.info("[save_single] START  name=%r  filename=%r  frames=%s  blobs=%d",
             name, filename, frames.shape, len(blobs))
    log.info("[save_single] inst_id=%s  video_id=%s", inst_id, video_id)

    # Save frames to disk first (outside the DB transaction so we can roll back cleanly)
    log.info("[save_single] writing frames to disk …")
    t1 = time.perf_counter()
    frames_path = save_frames(frames, video_id)
    log.info("[save_single] frames written in %.2fs", time.perf_counter() - t1)

    log.info("[save_single] opening DB and beginning transaction …")
    conn = _conn()
    try:
        conn.execute("BEGIN")
        log.info("[save_single] INSERT saved_instances …")
        conn.execute(
            "INSERT INTO saved_instances (id, name, type, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (inst_id, name, "single", now, json.dumps(metadata)),
        )
        log.info("[save_single] INSERT saved_videos …")
        conn.execute(
            """INSERT INTO saved_videos
               (id, instance_id, strip_index, filename, mode, width, height, nframes, frames_path, analysis_meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (video_id, inst_id, None, filename, mode, width, height, nframes,
             frames_path, json.dumps(analysis_meta)),
        )
        if blobs:
            log.info("[save_single] INSERT %d saved_blobs …", len(blobs))
            conn.executemany(
                """INSERT INTO saved_blobs
                   (video_id, blob_id, region, center_x, center_y, radius_px, color, mean_intensities)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (video_id, b["blob_id"], b["region"], b["center_x"],
                     b["center_y"], b["radius_px"], b["color"],
                     json.dumps(b["mean_intensities"]))
                    for b in blobs
                ],
            )
        log.info("[save_single] COMMITting …")
        conn.execute("COMMIT")
        log.info("[save_single] COMMIT OK")
    except Exception as exc:
        log.error("[save_single] ERROR during DB write, rolling back: %s", exc)
        conn.execute("ROLLBACK")
        _delete_frames(frames_path)
        raise
    finally:
        conn.close()

    log.info("[save_single] DONE  total=%.2fs  inst_id=%s", time.perf_counter() - t_total, inst_id)
    return inst_id


def save_multi_instance(
    name: str,
    filenames: list[str],
    frames_list: list[np.ndarray],
    mode: str,
    width: int,
    height: int,
    nframes: int,
    assignments: list,
    reference_centers: list,
) -> str:
    """Save a multi-strip instance atomically. Returns the new instance_id."""
    t_total = time.perf_counter()
    inst_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    metadata = {
        "nframes": nframes, "width": width, "height": height, "mode": mode,
        "nstrips": len(filenames), "assignments": assignments,
        "reference_centers": reference_centers,
    }

    log.info("[save_multi] START  name=%r  nstrips=%d  nframes=%d", name, len(filenames), nframes)
    log.info("[save_multi] inst_id=%s", inst_id)

    # Save all frame files to disk first
    video_ids = [uuid.uuid4().hex for _ in filenames]
    frames_paths = []
    try:
        for i, (vid_id, frames, fname) in enumerate(zip(video_ids, frames_list, filenames)):
            log.info("[save_multi] writing strip %d/%d  %r  shape=%s …",
                     i + 1, len(filenames), fname, frames.shape)
            t1 = time.perf_counter()
            frames_paths.append(save_frames(frames, vid_id))
            log.info("[save_multi] strip %d written in %.2fs", i + 1, time.perf_counter() - t1)
    except Exception as exc:
        log.error("[save_multi] ERROR writing frames: %s", exc)
        for p in frames_paths:
            _delete_frames(p)
        raise

    log.info("[save_multi] all frames written, opening DB …")
    conn = _conn()
    try:
        conn.execute("BEGIN")
        log.info("[save_multi] INSERT saved_instances …")
        conn.execute(
            "INSERT INTO saved_instances (id, name, type, created_at, metadata) VALUES (?, ?, ?, ?, ?)",
            (inst_id, name, "multi", now, json.dumps(metadata)),
        )
        for i, (vid_id, fname, fpath, frames) in enumerate(
            zip(video_ids, filenames, frames_paths, frames_list)
        ):
            log.info("[save_multi] INSERT saved_videos strip %d  %r", i, fname)
            conn.execute(
                """INSERT INTO saved_videos
                   (id, instance_id, strip_index, filename, mode, width, height, nframes, frames_path, analysis_meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (vid_id, inst_id, i, fname, mode, width, height,
                 frames.shape[0], fpath, "{}"),
            )
        log.info("[save_multi] COMMITting …")
        conn.execute("COMMIT")
        log.info("[save_multi] COMMIT OK")
    except Exception as exc:
        log.error("[save_multi] ERROR during DB write, rolling back: %s", exc)
        conn.execute("ROLLBACK")
        for p in frames_paths:
            _delete_frames(p)
        raise
    finally:
        conn.close()

    log.info("[save_multi] DONE  total=%.2fs  inst_id=%s", time.perf_counter() - t_total, inst_id)
    return inst_id


# ── Read / delete ──────────────────────────────────────────────────────────────

def list_instances() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            """SELECT i.id, i.name, i.type, i.created_at, i.metadata,
                      COUNT(v.id) as video_count
               FROM saved_instances i
               LEFT JOIN saved_videos v ON v.instance_id = i.id
               GROUP BY i.id
               ORDER BY i.created_at DESC"""
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        item = dict(r)
        item["metadata"] = json.loads(item["metadata"])
        result.append(item)
    return result


def get_instance_with_videos(instance_id: str) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, name, type, created_at, metadata FROM saved_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return None

        inst = dict(row)
        inst["metadata"] = json.loads(inst["metadata"])

        video_rows = conn.execute(
            """SELECT id, strip_index, filename, mode, width, height, nframes, frames_path, analysis_meta
               FROM saved_videos WHERE instance_id = ? ORDER BY COALESCE(strip_index, 0)""",
            (instance_id,),
        ).fetchall()

        inst["videos"] = []
        for v in video_rows:
            vid = dict(v)
            vid["analysis_meta"] = json.loads(vid["analysis_meta"])
            blob_rows = conn.execute(
                """SELECT blob_id, region, center_x, center_y, radius_px, color, mean_intensities
                   FROM saved_blobs WHERE video_id = ? ORDER BY blob_id""",
                (vid["id"],),
            ).fetchall()
            vid["blobs"] = [
                {**dict(b), "mean_intensities": json.loads(b["mean_intensities"])}
                for b in blob_rows
            ]
            inst["videos"].append(vid)
    finally:
        conn.close()

    return inst


def delete_instance(instance_id: str) -> bool:
    log.info("[delete] instance_id=%s", instance_id)
    conn = _conn()
    try:
        paths = conn.execute(
            "SELECT frames_path FROM saved_videos WHERE instance_id = ?",
            (instance_id,),
        ).fetchall()
        conn.execute("BEGIN")
        result = conn.execute(
            "DELETE FROM saved_instances WHERE id = ?", (instance_id,)
        )
        if result.rowcount == 0:
            conn.execute("ROLLBACK")
            log.info("[delete] instance not found (already deleted?)")
            return False
        conn.execute("COMMIT")
    except Exception as exc:
        log.error("[delete] ERROR: %s", exc)
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    for p in paths:
        _delete_frames(p["frames_path"])
    log.info("[delete] done, removed %d frame file(s)", len(paths))
    return True
