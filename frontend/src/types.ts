export interface SessionInfo {
  sessionId: string
  filename: string
  mode: 'gray16' | 'rgb96'
  width: number
  height: number
  nframes: number
}

export interface BlobResult {
  blob_id: number
  region: 'beam' | 'diffraction'
  center_x: number
  center_y: number
  radius_px: number
  color: string
  mean_intensities: number[]
}

export interface AnalysisResult {
  blobs: BlobResult[]
  n_blobs_beam: number
  n_blobs_diffraction: number
  n_analysis_frames_used: number
  split_row: number
}

export type AnalysisStatus = 'idle' | 'running' | 'complete' | 'error'

export interface MultiSessionInfo {
  sessionId: string
  filenames: string[]
  nstrips: number
  nframes: number
  width: number
  height: number
  mode: string
}

export interface MultiAnalysisResult {
  assignments: number[][]                       // assignments[strip_i][frame_n] = orig_strip_j
  reference_centers: Array<{ x: number; y: number }>
}

export interface PixelSelection {
  x: number
  y: number
  intensities: number[]
}

// ── Library (saved instances) ──────────────────────────────────────────────────

export interface SavedVideo {
  id: string
  stripIndex: number | null
  filename: string
  mode: string
  width: number
  height: number
  nframes: number
  blobs: BlobResult[]
  analysisMeta: { split_row?: number; n_analysis_frames_used?: number }
}

export interface SavedInstance {
  id: string
  name: string
  type: 'single' | 'multi'
  createdAt: string
  videoCount: number
  metadata: {
    nframes?: number
    width?: number
    height?: number
    mode?: string
    nstrips?: number
    assignments?: number[][]
    reference_centers?: Array<{ x: number; y: number }>
  }
  videos?: SavedVideo[]
}
