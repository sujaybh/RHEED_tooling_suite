# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

**Backend** (FastAPI, port 8000):
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

**Frontend** (Vite dev server, port 5173):
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. In dev mode, Vite proxies `/api/*` to the backend on port 8000.

**Production** (single server):
```bash
cd frontend && npm run build
cd ../backend && python -m uvicorn main:app   # serves frontend from /
```

## Architecture

```
RHEED_suite/
├── ksa_img_imm_to_text.py      # original standalone parser — do not modify
├── backend/
│   ├── main.py                 # FastAPI app, all endpoints
│   ├── imm_parser.py           # .imm binary parsing → numpy arrays
│   └── blob_analysis.py        # blob_log detection + intensity tracking
└── frontend/src/
    ├── App.tsx                 # global React context + all state
    ├── api.ts                  # typed fetch wrappers for every endpoint
    ├── types.ts                # TypeScript interfaces matching API shapes
    ├── hooks/
    │   ├── useFrameLoader.ts   # async frame cache, img.decode(), prefetch ±3
    │   └── usePlayerLoop.ts    # rAF-based playback (not setInterval)
    └── components/
        ├── FrameCanvas.tsx     # <canvas> drawing + click → pixel coords
        ├── BlobOverlay.tsx     # <svg> circles over canvas (separate layer)
        ├── PlayerControls.tsx  # play/pause/scrub/fps/brightness
        ├── PixelPlot.tsx       # Plotly: per-pixel intensity vs frame
        └── AnalysisPanel.tsx   # Analyze button + Plotly blob traces
```

## Key Design Decisions

**IMM parsing**: `imm_parser.py` auto-detects dimensions from file size by trying common resolutions. Real data is `1024×1024 gray16, 172 frames, ~345 MB`. All frames are loaded into a single contiguous `(nframes, H, W) uint16` numpy array for O(1) frame access. PNG frames encode with `compress_level=1` (fast, ~5 ms).

**Two-layer canvas**: The `<canvas>` draws pixel data; an absolutely-positioned `<svg>` draws blob circles on top. This avoids redrawing blobs on every frame change.

**Frame cache**: `HTMLImageElement` objects are cached in a `Map` ref (not React state). A `cacheVersion` counter in state triggers re-renders when new frames load. Frames are pre-fetched ±3 around the current position.

**Blob detection**: `blob_analysis.py` splits the image at `H//2`, normalizes each half independently (critical — prevents the bright beam spot from suppressing the LoG response in the diffraction half), then runs `skimage.feature.blob_log` with separate parameters for each zone.

**Analysis is async**: `POST /api/analyze/{id}` returns `202` immediately and spawns a background `asyncio.Task` via `run_in_executor`. The frontend polls `GET /api/analyze/{id}/status` every 1.5 s.

**Brightness control**: The contrast slider in `PlayerControls` updates a `contrast` state value in context. **This currently updates the state but the frame URLs are not yet contrast-parameterized** — frames are served at the default `contrast=1.0`. To wire it up: pass `contrast` into `frameUrl()` calls in `useFrameLoader` and clear the cache on contrast change.

## Adding New Analysis Modules

1. Create `backend/my_analysis.py` with a single top-level function that takes `frames: np.ndarray` and returns a JSON-serializable dict.
2. Add `POST /api/my-analysis/{session_id}` and `GET /api/my-analysis/{session_id}/status` endpoints in `main.py` following the same async task pattern as `/analyze`.
3. Add a new `<div className="panel-card">` block in the right pane (in `App.tsx` or a new component) to display results.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload `.imm` → returns `session_id`, auto-detects dimensions |
| GET | `/api/frame/{id}/{n}` | PNG of frame `n` (uint8 preview) |
| GET | `/api/pixel/{id}?x=&y=` | Raw uint16 intensities for all frames at pixel (x,y) |
| POST | `/api/analyze/{id}` | Start blob detection (async, returns 202) |
| GET | `/api/analyze/{id}/status` | Poll analysis progress/results |
| DELETE | `/api/session/{id}` | Free session memory |
