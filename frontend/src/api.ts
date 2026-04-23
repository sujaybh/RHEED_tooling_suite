import type { SessionInfo, PixelSelection, AnalysisResult, AnalysisStatus, MultiSessionInfo, MultiAnalysisResult, SavedInstance, BlobResult } from './types'

const BASE = '/api'

export async function uploadFile(
  file: File,
  onProgress?: (pct: number) => void,
  width?: number,
  height?: number,
): Promise<SessionInfo> {
  const params = new URLSearchParams()
  if (width) params.set('width', String(width))
  if (height) params.set('height', String(height))

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/upload?${params}`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const data = JSON.parse(xhr.responseText)
        resolve({
          sessionId: data.session_id,
          filename: data.filename,
          mode: data.mode,
          width: data.width,
          height: data.height,
          nframes: data.nframes,
        })
      } else {
        const err = JSON.parse(xhr.responseText)
        reject(new Error(err.detail || `Upload failed: ${xhr.status}`))
      }
    }
    xhr.onerror = () => reject(new Error('Network error during upload'))

    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

export function frameUrl(sessionId: string, frameIndex: number, contrast = 1.0): string {
  return `${BASE}/frame/${sessionId}/${frameIndex}?contrast=${contrast}`
}

export async function fetchPixel(
  sessionId: string,
  x: number,
  y: number,
): Promise<PixelSelection> {
  const res = await fetch(`${BASE}/pixel/${sessionId}?x=${x}&y=${y}`)
  if (!res.ok) throw new Error(`Pixel fetch failed: ${res.status}`)
  return res.json()
}

export async function startAnalysis(
  sessionId: string,
  params?: Partial<{
    n_analysis_frames: number
    beam_roi_fraction: number
    beam_threshold: number
    diff_threshold: number
  }>,
): Promise<void> {
  const res = await fetch(`${BASE}/analyze/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params ?? {}),
  })
  if (!res.ok && res.status !== 202) {
    const err = await res.json()
    throw new Error(err.detail || `Analyze failed: ${res.status}`)
  }
}

export async function pollAnalysisStatus(
  sessionId: string,
): Promise<{ status: AnalysisStatus; result?: AnalysisResult; detail?: string }> {
  const res = await fetch(`${BASE}/analyze/${sessionId}/status`)
  if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`)
  return res.json()
}

export async function fetchBlobIntensities(
  sessionId: string,
  center_x: number,
  center_y: number,
  radius_px: number,
): Promise<number[]> {
  const res = await fetch(`${BASE}/blob-intensities/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ center_x, center_y, radius_px }),
  })
  if (!res.ok) throw new Error(`Blob intensities fetch failed: ${res.status}`)
  const data = await res.json()
  return data.mean_intensities
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`${BASE}/session/${sessionId}`, { method: 'DELETE' })
}

// ── Multi-strip API ────────────────────────────────────────────────────────────

export async function uploadMultiFiles(
  files: File[],
  onProgress?: (pct: number) => void,
): Promise<MultiSessionInfo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/multi-upload`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const d = JSON.parse(xhr.responseText)
        resolve({
          sessionId: d.session_id,
          filenames: d.filenames,
          nstrips: d.nstrips,
          nframes: d.nframes,
          width: d.width,
          height: d.height,
          mode: d.mode,
        })
      } else {
        const err = JSON.parse(xhr.responseText)
        reject(new Error(err.detail || `Multi-upload failed: ${xhr.status}`))
      }
    }
    xhr.onerror = () => reject(new Error('Network error during multi-upload'))

    const form = new FormData()
    for (const f of files) form.append('files', f)
    xhr.send(form)
  })
}

export function multiFrameUrl(sessionId: string, stripIndex: number, frameIndex: number, contrast = 1.0): string {
  return `${BASE}/multi-frame/${sessionId}/${stripIndex}/${frameIndex}?contrast=${contrast}`
}

export async function startMultiAnalysis(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/multi-analyze/${sessionId}`, { method: 'POST' })
  if (!res.ok && res.status !== 202) {
    const err = await res.json()
    throw new Error(err.detail || `Multi-analyze failed: ${res.status}`)
  }
}

export async function pollMultiAnalysisStatus(sessionId: string): Promise<{
  status: AnalysisStatus
  result?: MultiAnalysisResult
  detail?: string
}> {
  const res = await fetch(`${BASE}/multi-analyze/${sessionId}/status`)
  if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`)
  const d = await res.json()
  if (d.status === 'complete') {
    return {
      status: 'complete',
      result: { assignments: d.assignments, reference_centers: d.reference_centers },
    }
  }
  return d
}

export async function exportStripAsSession(
  multiSessionId: string,
  stripIndex: number,
): Promise<SessionInfo> {
  const res = await fetch(`${BASE}/multi-session/${multiSessionId}/export/${stripIndex}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Export failed: ${res.status}`)
  }
  const d = await res.json()
  return {
    sessionId: d.session_id,
    filename: d.filename,
    mode: d.mode,
    width: d.width,
    height: d.height,
    nframes: d.nframes,
  }
}

export async function deleteMultiSession(sessionId: string): Promise<void> {
  await fetch(`${BASE}/multi-session/${sessionId}`, { method: 'DELETE' })
}

// ── Library API ────────────────────────────────────────────────────────────────

export async function saveSingleToLibrary(
  sessionId: string,
  blobs: BlobResult[],
  analysisMeta: { split_row?: number; n_analysis_frames_used?: number },
  replaceId?: string,
  name?: string,
): Promise<{ instance_id: string; name: string }> {
  const res = await fetch(`${BASE}/save/single`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId, blobs, analysis_meta: analysisMeta,
      name, replace_instance_id: replaceId ?? null,
    }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Save failed: ${res.status}`)
  }
  return res.json()
}

export async function saveMultiToLibrary(
  multiSessionId: string,
  replaceId?: string,
  name?: string,
): Promise<{ instance_id: string; name: string }> {
  const res = await fetch(`${BASE}/save/multi`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      multi_session_id: multiSessionId,
      name, replace_instance_id: replaceId ?? null,
    }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Save failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchLibraryInstance(instanceId: string): Promise<SavedInstance> {
  const res = await fetch(`${BASE}/saved/${instanceId}`)
  if (!res.ok) throw new Error(`Fetch failed: ${res.status}`)
  const d = await res.json()
  return {
    id: d.id,
    name: d.name,
    type: d.type,
    createdAt: d.created_at,
    videoCount: d.videos?.length ?? 0,
    metadata: d.metadata,
    videos: d.videos?.map((v: any) => ({
      id: v.id,
      stripIndex: v.strip_index,
      filename: v.filename,
      mode: v.mode,
      width: v.width,
      height: v.height,
      nframes: v.nframes,
      blobs: v.blobs ?? [],
      analysisMeta: v.analysis_meta ?? {},
    })),
  }
}

export async function listLibrary(): Promise<SavedInstance[]> {
  const res = await fetch(`${BASE}/saved`)
  if (!res.ok) throw new Error(`List failed: ${res.status}`)
  const d = await res.json()
  return d.instances.map((i: any) => ({
    id: i.id,
    name: i.name,
    type: i.type,
    createdAt: i.created_at,
    videoCount: i.video_count,
    metadata: i.metadata,
  }))
}

export type LoadedSingle = {
  type: 'single'
  sessionInfo: SessionInfo
  analysisResult: AnalysisResult | null
}

export type LoadedMulti = {
  type: 'multi'
  multiSessionInfo: MultiSessionInfo
  multiResult: MultiAnalysisResult
}

export async function loadFromLibrary(instanceId: string): Promise<LoadedSingle | LoadedMulti> {
  const res = await fetch(`${BASE}/saved/${instanceId}/load`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Load failed: ${res.status}`)
  }
  const d = await res.json()
  if (d.type === 'single') {
    return {
      type: 'single',
      sessionInfo: {
        sessionId: d.session_id,
        filename: d.filename,
        mode: d.mode,
        width: d.width,
        height: d.height,
        nframes: d.nframes,
      },
      analysisResult: d.analysis_result ?? null,
    }
  } else {
    return {
      type: 'multi',
      multiSessionInfo: {
        sessionId: d.session_id,
        filenames: d.filenames,
        nstrips: d.nstrips,
        nframes: d.nframes,
        width: d.width,
        height: d.height,
        mode: d.mode,
      },
      multiResult: {
        assignments: d.assignments,
        reference_centers: d.reference_centers,
      },
    }
  }
}

export async function deleteFromLibrary(instanceId: string): Promise<void> {
  const res = await fetch(`${BASE}/saved/${instanceId}`, { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || `Delete failed: ${res.status}`)
  }
}
