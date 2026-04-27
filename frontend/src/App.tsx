import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}
import type { SessionInfo, PixelSelection, AnalysisResult, AnalysisStatus, BlobResult, MultiSessionInfo, MultiAnalysisResult } from './types'
import { uploadFile, fetchPixel, startAnalysis, pollAnalysisStatus, deleteSession, uploadMultiFiles, startMultiAnalysis, pollMultiAnalysisStatus, deleteMultiSession, exportStripAsSession, saveSingleToLibrary, saveMultiToLibrary, loadFromLibrary } from './api'
import { useFrameLoader } from './hooks/useFrameLoader'
import { usePlayerLoop } from './hooks/usePlayerLoop'
import UploadPanel from './components/UploadPanel'
import LibraryPanel from './components/LibraryPanel'
import VideoPlayer from './components/VideoPlayer'
import AnalysisPanel from './components/AnalysisPanel'
import PixelPlot from './components/PixelPlot'
import MultiStripLayout from './components/MultiStripLayout'
import './index.css'

// ── Context ───────────────────────────────────────────────────────────────────

export type ActiveMode = 'pixel' | 'zoom' | 'edit'
export type EditSubMode = 'select' | 'add'
export interface ZoomRegion { x: number; y: number; w: number; h: number }

interface AppState {
  // Session
  session: SessionInfo | null
  uploadProgress: number
  uploadError: string | null
  // Playback
  currentFrame: number
  isPlaying: boolean
  fps: number
  contrast: number
  // Frame cache (ref-backed; cacheVersion triggers re-renders)
  frameCache: Map<number, HTMLImageElement>
  cacheVersion: number
  // Pixel
  pixelSelection: PixelSelection | null
  pixelLoading: boolean
  // Analysis
  analysisStatus: AnalysisStatus
  analysisResult: AnalysisResult | null
  analysisError: string | null
  showBlobs: boolean
  // Toolbox
  activeMode: ActiveMode
  zoomRegion: ZoomRegion | null
  // Edit mode
  editedBlobs: BlobResult[] | null
  selectedBlobIds: Set<number>
  editSubMode: EditSubMode
  // Multi-strip fixer
  multiSession: MultiSessionInfo | null
  multiAnalysisStatus: AnalysisStatus
  multiResult: MultiAnalysisResult | null
  multiAnalysisError: string | null
  multiUploadProgress: number
  currentStrip: number
  multiView: boolean  // true = show MultiStripLayout, false = show MainLayout for exported strip
  // Actions
  handleUpload: (file: File, w?: number, h?: number) => void
  handleNewFile: () => void
  setCurrentFrame: (updater: number | ((prev: number) => number)) => void
  setIsPlaying: (v: boolean) => void
  setFps: (v: number) => void
  setContrast: (v: number) => void
  handlePixelClick: (x: number, y: number) => void
  handleAnalyze: (params?: object) => void
  setShowBlobs: (v: boolean) => void
  setActiveMode: (mode: ActiveMode) => void
  setZoomRegion: (region: ZoomRegion | null) => void
  setEditedBlobs: React.Dispatch<React.SetStateAction<BlobResult[] | null>>
  setSelectedBlobIds: (ids: Set<number>) => void
  setEditSubMode: (mode: EditSubMode) => void
  handleMultiUpload: (files: File[]) => void
  handleMultiAnalyze: () => void
  handleMultiNewFile: () => void
  handleOpenStripInMain: (stripIndex: number) => void
  setCurrentStrip: (strip: number) => void
  setMultiView: (v: boolean) => void
  onFrameReady: (index: number, img: HTMLImageElement) => void
  // Library
  libraryOpen: boolean
  handleOpenLibrary: () => void
  handleCloseLibrary: () => void
  handleSaveSingle: () => void
  handleSaveMulti: () => void
  handleLoadFromLibrary: (instanceId: string) => Promise<void>
  saveError: string | null
  saving: boolean
  saveSuccess: boolean
  savedInstanceId: string | null
  multiSavedInstanceId: string | null
}

const AppContext = createContext<AppState>(null as unknown as AppState)
export const useApp = () => useContext(AppContext)

// ── Provider + root layout ────────────────────────────────────────────────────

export default function App() {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const [currentFrame, setCurrentFrameRaw] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [fps, setFps] = useState(10)
  const [contrast, setContrast] = useState(1.0)
  // contrastCommitted is debounced — only this value drives frame fetches.
  // The raw contrast value still updates instantly for the slider label.
  const contrastCommitted = useDebounce(contrast, 150)

  // Frame cache lives in refs so mutations don't cause re-renders;
  // cacheVersion is the only state that triggers a redraw.
  const frameCacheRef = useRef<Map<number, HTMLImageElement>>(new Map())
  const loadingSetRef = useRef<Set<number>>(new Set())
  const [cacheVersion, setCacheVersion] = useState(0)
  // Incremented each time the cache is flushed; in-flight loads that captured
  // an older generation will self-discard on completion.
  const generationRef = useRef(0)

  const [pixelSelection, setPixelSelection] = useState<PixelSelection | null>(null)
  const [pixelLoading, setPixelLoading] = useState(false)

  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle')
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const [showBlobs, setShowBlobs] = useState(true)
  const [activeMode, setActiveMode] = useState<ActiveMode>('pixel')
  const [zoomRegion, setZoomRegion] = useState<ZoomRegion | null>(null)
  const [editedBlobs, setEditedBlobs] = useState<BlobResult[] | null>(null)
  const [selectedBlobIds, setSelectedBlobIds] = useState<Set<number>>(new Set())
  const [editSubMode, setEditSubMode] = useState<EditSubMode>('select')

  // Multi-strip fixer
  const [multiSession, setMultiSession] = useState<MultiSessionInfo | null>(null)
  const [multiAnalysisStatus, setMultiAnalysisStatus] = useState<AnalysisStatus>('idle')
  const [multiResult, setMultiResult] = useState<MultiAnalysisResult | null>(null)
  const [multiAnalysisError, setMultiAnalysisError] = useState<string | null>(null)
  const [multiUploadProgress, setMultiUploadProgress] = useState(0)
  const [currentStrip, setCurrentStrip] = useState(0)
  const [multiView, setMultiView] = useState(true)

  // Library
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [savedInstanceId, setSavedInstanceId] = useState<string | null>(null)
  const [multiSavedInstanceId, setMultiSavedInstanceId] = useState<string | null>(null)

  const setCurrentFrame = useCallback(
    (updater: number | ((prev: number) => number)) => {
      setCurrentFrameRaw(updater as (prev: number) => number)
    },
    [],
  )

  const onFrameReady = useCallback((index: number, img: HTMLImageElement) => {
    frameCacheRef.current.set(index, img)
    setCacheVersion((v) => v + 1)
  }, [])

  // ── Upload ────────────────────────────────────────────────────────────────

  const handleUpload = useCallback(async (file: File, w?: number, h?: number) => {
    setUploadError(null)
    setUploadProgress(0)
    try {
      const info = await uploadFile(file, setUploadProgress, w, h)
      setSession(info)
      setCurrentFrameRaw(0)
      setIsPlaying(false)
      setPixelSelection(null)
      setAnalysisStatus('idle')
      setAnalysisResult(null)
      setAnalysisError(null)
      setEditedBlobs(null)
      setSelectedBlobIds(new Set())
      setEditSubMode('select')
      frameCacheRef.current.clear()
      loadingSetRef.current.clear()
      setCacheVersion(0)
      setSavedInstanceId(null)
      setSaveError(null)
      setSaveSuccess(false)
    } catch (e: unknown) {
      setUploadError((e as Error).message)
    }
  }, [])

  const handleNewFile = useCallback(async () => {
    if (session) {
      await deleteSession(session.sessionId).catch(() => {})
    }
    setSession(null)
    setUploadProgress(0)
    setUploadError(null)
    setPixelSelection(null)
    setAnalysisStatus('idle')
    setAnalysisResult(null)
    setAnalysisError(null)
    setEditedBlobs(null)
    setSelectedBlobIds(new Set())
    setEditSubMode('select')
    frameCacheRef.current.clear()
    loadingSetRef.current.clear()
  }, [session])

  // ── Pixel click ───────────────────────────────────────────────────────────

  const handlePixelClick = useCallback(async (x: number, y: number) => {
    if (!session) return
    setPixelLoading(true)
    try {
      const data = await fetchPixel(session.sessionId, x, y)
      setPixelSelection(data)
    } catch (e) {
      console.error('Pixel fetch failed:', e)
    } finally {
      setPixelLoading(false)
    }
  }, [session])

  // ── Analysis ──────────────────────────────────────────────────────────────

  const handleAnalyze = useCallback(async (params?: object) => {
    if (!session) return
    setAnalysisError(null)
    try {
      await startAnalysis(session.sessionId, params)
      setAnalysisStatus('running')
    } catch (e: unknown) {
      setAnalysisError((e as Error).message)
    }
  }, [session])

  // ── Multi-strip ───────────────────────────────────────────────────────────

  const handleMultiUpload = useCallback(async (files: File[]) => {
    setMultiAnalysisError(null)
    setMultiUploadProgress(0)
    try {
      const info = await uploadMultiFiles(files, setMultiUploadProgress)
      setMultiSession(info)
      setMultiAnalysisStatus('idle')
      setMultiResult(null)
      setCurrentStrip(0)
      setMultiSavedInstanceId(null)
      setSaveError(null)
      setSaveSuccess(false)
    } catch (e: unknown) {
      setMultiAnalysisError((e as Error).message)
    }
  }, [])

  const handleMultiAnalyze = useCallback(async () => {
    if (!multiSession) return
    setMultiAnalysisError(null)
    try {
      await startMultiAnalysis(multiSession.sessionId)
      setMultiAnalysisStatus('running')
    } catch (e: unknown) {
      setMultiAnalysisError((e as Error).message)
    }
  }, [multiSession])

  const handleMultiNewFile = useCallback(async () => {
    if (multiSession) {
      await deleteMultiSession(multiSession.sessionId).catch(() => {})
    }
    setMultiSession(null)
    setMultiAnalysisStatus('idle')
    setMultiResult(null)
    setMultiAnalysisError(null)
    setMultiUploadProgress(0)
    setCurrentStrip(0)
    setMultiView(true)
  }, [multiSession])

  const handleOpenStripInMain = useCallback(async (stripIndex: number) => {
    if (!multiSession) return
    try {
      const info = await exportStripAsSession(multiSession.sessionId, stripIndex)
      setSession(info)
      setCurrentFrameRaw(0)
      setIsPlaying(false)
      setPixelSelection(null)
      setAnalysisStatus('idle')
      setAnalysisResult(null)
      setAnalysisError(null)
      setEditedBlobs(null)
      setSelectedBlobIds(new Set())
      frameCacheRef.current.clear()
      loadingSetRef.current.clear()
      setCacheVersion(0)
      setMultiView(false)   // switch to main layout, multi-strip preserved in state
    } catch (e: unknown) {
      console.error('Export strip failed:', e)
    }
  }, [multiSession])

  // ── Library ───────────────────────────────────────────────────────────────

  const handleOpenLibrary = useCallback(() => setLibraryOpen(true), [])
  const handleCloseLibrary = useCallback(() => setLibraryOpen(false), [])

  const handleSaveSingle = useCallback(async () => {
    if (!session) return
    setSaveError(null)
    setSaveSuccess(false)
    setSaving(true)
    try {
      const blobs = editedBlobs ?? []
      const ameta = analysisResult
        ? { split_row: analysisResult.split_row, n_analysis_frames_used: analysisResult.n_analysis_frames_used }
        : {}
      const result = await saveSingleToLibrary(session.sessionId, blobs, ameta, savedInstanceId ?? undefined)
      setSavedInstanceId(result.instance_id)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 2500)
      // Notify library panel to refresh if it's open
      window.dispatchEvent(new CustomEvent('rheed:saved', { detail: `Saved "${session.filename}".` }))
    } catch (e: unknown) {
      setSaveError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [session, editedBlobs, analysisResult, savedInstanceId])

  const handleSaveMulti = useCallback(async () => {
    if (!multiSession) return
    setSaveError(null)
    setSaveSuccess(false)
    setSaving(true)
    try {
      const result = await saveMultiToLibrary(multiSession.sessionId, multiSavedInstanceId ?? undefined)
      setMultiSavedInstanceId(result.instance_id)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 2500)
      window.dispatchEvent(new CustomEvent('rheed:saved', { detail: `Saved multi-strip session.` }))
    } catch (e: unknown) {
      setSaveError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [multiSession, multiSavedInstanceId])

  const handleLoadFromLibrary = useCallback(async (instanceId: string) => {
    const loaded = await loadFromLibrary(instanceId)

    if (loaded.type === 'single') {
      const { sessionInfo, analysisResult: ar } = loaded
      setSession(sessionInfo)
      setCurrentFrameRaw(0)
      setIsPlaying(false)
      setPixelSelection(null)
      frameCacheRef.current.clear()
      loadingSetRef.current.clear()
      setCacheVersion(0)
      setEditSubMode('select')
      setSelectedBlobIds(new Set())
      if (ar) {
        setAnalysisResult(ar)
        setEditedBlobs(ar.blobs)
        setAnalysisStatus('complete')
      } else {
        setAnalysisResult(null)
        setEditedBlobs(null)
        setAnalysisStatus('idle')
      }
      setAnalysisError(null)
      setMultiView(false)
      setSavedInstanceId(instanceId)       // track so re-save replaces this record
      setMultiSavedInstanceId(null)
    } else {
      const { multiSessionInfo, multiResult: mr } = loaded
      setMultiSession(multiSessionInfo)
      setMultiResult(mr)
      setMultiAnalysisStatus('complete')
      setMultiAnalysisError(null)
      setCurrentStrip(0)
      setMultiView(true)
      setMultiSavedInstanceId(instanceId)  // track so re-save replaces this record
      setSavedInstanceId(null)
    }
    setSaveSuccess(false)
    setSaveError(null)
  }, [])

  // Poll multi-strip analysis
  useEffect(() => {
    if (!multiSession || multiAnalysisStatus !== 'running') return
    const id = setInterval(async () => {
      try {
        const s = await pollMultiAnalysisStatus(multiSession.sessionId)
        if (s.status === 'complete') {
          setMultiResult(s.result!)
          setMultiAnalysisStatus('complete')
        } else if (s.status === 'error') {
          setMultiAnalysisError(s.detail ?? 'Unknown error')
          setMultiAnalysisStatus('error')
        }
      } catch (e) { console.error('Multi-poll error:', e) }
    }, 1500)
    return () => clearInterval(id)
  }, [multiSession, multiAnalysisStatus])

  // Poll while analysis is running
  useEffect(() => {
    if (!session || analysisStatus !== 'running') return
    const id = setInterval(async () => {
      try {
        const s = await pollAnalysisStatus(session.sessionId)
        if (s.status === 'complete') {
          setAnalysisResult(s.result!)
          setEditedBlobs(s.result!.blobs)
          setSelectedBlobIds(new Set())
          setAnalysisStatus('complete')
        } else if (s.status === 'error') {
          setAnalysisError(s.detail ?? 'Unknown error')
          setAnalysisStatus('error')
        }
      } catch (e) {
        console.error('Poll error:', e)
      }
    }, 1500)
    return () => clearInterval(id)
  }, [session, analysisStatus])

  // Stop playing when reaching last frame
  const handlePlayEnd = useCallback(() => setIsPlaying(false), [])

  // ── Player loop (rAF) ─────────────────────────────────────────────────────

  usePlayerLoop(isPlaying, fps, session?.nframes ?? 0, setCurrentFrame, handlePlayEnd)

  // ── Frame prefetch hook ───────────────────────────────────────────────────

  const { prefetchAround } = useFrameLoader(
    session?.sessionId ?? null,
    session?.nframes ?? 0,
    contrastCommitted,
    generationRef,
    frameCacheRef.current,
    loadingSetRef.current,
    onFrameReady,
  )

  // Flush frame cache only when the debounced contrast settles,
  // not on every slider tick. Bump the generation so any still-in-flight
  // loads from the previous contrast value discard themselves on arrival.
  useEffect(() => {
    generationRef.current += 1
    frameCacheRef.current.clear()
    loadingSetRef.current.clear()
    setCacheVersion((v) => v + 1)
  }, [contrastCommitted])

  // Prefetch whenever frame changes
  useEffect(() => {
    if (session) prefetchAround(currentFrame)
  }, [currentFrame, session, prefetchAround])

  // ── Context value ─────────────────────────────────────────────────────────

  const ctx: AppState = {
    session, uploadProgress, uploadError,
    currentFrame, isPlaying, fps, contrast,
    frameCache: frameCacheRef.current,
    cacheVersion,
    pixelSelection, pixelLoading,
    analysisStatus, analysisResult, analysisError, showBlobs,
    activeMode, zoomRegion,
    editedBlobs, selectedBlobIds, editSubMode,
    multiSession, multiAnalysisStatus, multiResult, multiAnalysisError, multiUploadProgress, currentStrip,
    handleUpload, handleNewFile,
    setCurrentFrame, setIsPlaying, setFps, setContrast,
    handlePixelClick, handleAnalyze, setShowBlobs,
    setActiveMode, setZoomRegion,
    setEditedBlobs, setSelectedBlobIds, setEditSubMode,
    handleMultiUpload, handleMultiAnalyze, handleMultiNewFile, setCurrentStrip,
    handleOpenStripInMain, setMultiView, multiView,
    onFrameReady,
    libraryOpen, handleOpenLibrary, handleCloseLibrary,
    handleSaveSingle, handleSaveMulti, handleLoadFromLibrary,
    saveError, saving, saveSuccess, savedInstanceId, multiSavedInstanceId,
  }

  return (
    <AppContext.Provider value={ctx}>
      {libraryOpen && <LibraryPanel />}
      {multiSession && multiView ? (
        <MultiStripLayout />
      ) : !session ? (
        <UploadPanel />
      ) : (
        <MainLayout />
      )}
    </AppContext.Provider>
  )
}

// ── Main layout ───────────────────────────────────────────────────────────────

function MainLayout() {
  const {
    session, handleNewFile, multiSession, setMultiView,
    handleOpenLibrary, handleSaveSingle,
    saving, saveError, saveSuccess, savedInstanceId,
    editedBlobs, analysisStatus,
  } = useApp()

  const canSave = analysisStatus === 'complete' || (editedBlobs !== null && editedBlobs.length > 0)
  const isResave = savedInstanceId !== null

  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="topbar-title">RHEED Analysis Suite</span>
        <span className="topbar-file">{session!.filename}</span>
        <span className="topbar-meta">
          {session!.width}×{session!.height} · {session!.mode} · {session!.nframes} frames
        </span>
        {saveError && <span className="topbar-save-error">{saveError}</span>}
        {multiSession && (
          <button className="btn-ghost" onClick={() => setMultiView(true)}>← Back to Strips</button>
        )}
        <button className="btn-ghost" onClick={handleOpenLibrary}>Library</button>
        {canSave && (
          <button
            className={`btn-ghost btn-ghost--save ${saveSuccess ? 'btn-ghost--saved' : ''}`}
            onClick={handleSaveSingle}
            disabled={saving}
          >
            {saving
              ? <><span className="spinner" style={{ width: 10, height: 10, marginRight: 5 }} />Saving…</>
              : saveSuccess
                ? 'Saved ✓'
                : isResave ? 'Update' : 'Save'}
          </button>
        )}
        <button className="btn-ghost" onClick={handleNewFile}>New File</button>
      </header>

      <div className="main-content">
        <div className="left-pane">
          <VideoPlayer />
        </div>
        <div className="right-pane">
          <AnalysisPanel />
          <PixelPlot />
        </div>
      </div>
    </div>
  )
}
