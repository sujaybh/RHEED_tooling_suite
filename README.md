# RHEED Suite

A web-based viewer and analysis tool for RHEED (Reflection High-Energy Electron Diffraction) `.imm` image sequence files. Upload a data set, scrub through frames, track pixel intensities over time, and run automated blob detection on diffraction patterns.

## Requirements

- Python 3.9+
- Node.js 18+

## Quick Start (Development)

Open two terminals.

**Terminal 1 — Backend:**
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload
```
The API will be available at `http://localhost:8000`.

**Terminal 2 — Frontend:**
```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. The frontend dev server proxies all `/api/*` requests to the backend automatically.

## Production (Single Server)

Build the frontend once, then serve everything from the backend:

```bash
cd frontend
npm run build

cd ../backend
python -m uvicorn main:app
```

Open `http://localhost:8000`.

## Usage

1. Click **Upload** and select a `.imm` file.
2. Use the player controls to scrub through frames or press play.
3. Click any pixel on the frame to plot its intensity over time.
4. Click **Analyze** to run blob detection on all frames and view the results.

## Project Structure

```
RHEED_suite/
├── backend/
│   ├── main.py           # FastAPI app and all endpoints
│   ├── imm_parser.py     # .imm binary parsing → numpy arrays
│   └── blob_analysis.py  # Blob detection and intensity tracking
└── frontend/src/
    ├── App.tsx            # Root component and global state
    ├── api.ts             # API client functions
    └── components/        # UI components (canvas, plots, controls)
```
