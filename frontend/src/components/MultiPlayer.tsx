import { useCallback, useEffect, useRef, useState } from 'react'
import { multiFrameUrl } from '../api'
import { usePlayerLoop } from '../hooks/usePlayerLoop'

interface Props {
  sessionId: string
  stripIndex: number
  nframes: number
  width: number
  height: number
  assignments: number[][]   // assignments[strip_i][frame_n] = orig_strip_j
  stripNames: string[]
}

const FPS_OPTIONS = [1, 2, 5, 10, 15, 24, 30]
const PREFETCH = 3

export default function MultiPlayer({ sessionId, stripIndex, nframes, width, height, assignments, stripNames }: Props) {
  const canvasRef  = useRef<HTMLCanvasElement>(null)
  const cacheRef   = useRef<Map<number, HTMLImageElement>>(new Map())
  const loadingRef = useRef<Set<number>>(new Set())

  const [currentFrame, setCurrentFrame] = useState(0)
  const [isPlaying, setIsPlaying]       = useState(false)
  const [fps, setFps]                   = useState(10)
  const [contrast, setContrast]         = useState(1.0)
  const [cacheVer, setCacheVer]         = useState(0)

  const displayW = 512
  const displayH = Math.round((displayW * height) / width)

  // Draw whenever cache updates or frame changes
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const img = cacheRef.current.get(currentFrame)
    if (img) ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  }, [currentFrame, cacheVer])

  const loadFrame = useCallback((index: number) => {
    if (index < 0 || index >= nframes) return
    if (cacheRef.current.has(index) || loadingRef.current.has(index)) return
    loadingRef.current.add(index)
    const img = new Image()
    img.src = multiFrameUrl(sessionId, stripIndex, index, contrast)
    img.decode()
      .then(() => {
        loadingRef.current.delete(index)
        cacheRef.current.set(index, img)
        setCacheVer(v => v + 1)
      })
      .catch(() => loadingRef.current.delete(index))
  }, [sessionId, stripIndex, nframes, contrast])

  // Prefetch around current frame
  useEffect(() => {
    for (let d = -PREFETCH; d <= PREFETCH; d++) loadFrame(currentFrame + d)
  }, [currentFrame, loadFrame])

  // Contrast change: clear cache and reload
  useEffect(() => {
    cacheRef.current.clear()
    loadingRef.current.clear()
    setCacheVer(0)
    loadFrame(currentFrame)
  }, [contrast]) // eslint-disable-line react-hooks/exhaustive-deps

  const setFrame = useCallback((updater: number | ((p: number) => number)) => {
    setCurrentFrame(updater as (p: number) => number)
  }, [])

  const handleEnd = useCallback(() => setIsPlaying(false), [])
  usePlayerLoop(isPlaying, fps, nframes, setFrame, handleEnd)

  const go = (f: number) => setCurrentFrame(Math.max(0, Math.min(nframes - 1, f)))

  // Which original strip does this frame come from?
  const origStrip = assignments[stripIndex]?.[currentFrame] ?? stripIndex
  const swapped   = origStrip !== stripIndex

  return (
    <div className="video-player">
      {/* Canvas */}
      <div className="canvas-wrapper" style={{ width: displayW, height: displayH, position: 'relative' }}>
        <canvas
          ref={canvasRef}
          width={width}
          height={height}
          style={{ width: displayW, height: displayH, imageRendering: 'pixelated', display: 'block' }}
        />
        {/* Source indicator — shows when a frame was reassigned */}
        <div className={`frame-source-badge ${swapped ? 'frame-source-badge--swapped' : ''}`}>
          {swapped
            ? `from strip ${origStrip + 1} (${stripNames[origStrip]})`
            : `strip ${origStrip + 1} (original)`}
        </div>
      </div>

      {/* Controls */}
      <div className="player-controls">
        <div className="player-buttons">
          <button className="ctrl-btn" title="First" onClick={() => go(0)}>⏮</button>
          <button className="ctrl-btn" title="Prev"  onClick={() => go(currentFrame - 1)}>⏪</button>
          <button className="ctrl-btn ctrl-btn--play" onClick={() => setIsPlaying(p => !p)}>
            {isPlaying ? '⏸' : '▶'}
          </button>
          <button className="ctrl-btn" title="Next" onClick={() => go(currentFrame + 1)}>⏩</button>
          <button className="ctrl-btn" title="Last" onClick={() => go(nframes - 1)}>⏭</button>
        </div>

        <input
          type="range" className="scrubbar"
          min={0} max={nframes - 1} value={currentFrame}
          onChange={(e) => go(parseInt(e.target.value))}
        />

        <div className="player-meta">
          <span className="frame-counter">
            Frame <strong>{currentFrame + 1}</strong> / {nframes}
          </span>
          <label className="ctrl-label">
            FPS
            <select className="ctrl-select" value={fps} onChange={(e) => setFps(parseInt(e.target.value))}>
              {FPS_OPTIONS.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className="ctrl-label">
            Brightness
            <input type="range" className="contrast-slider" min={0.5} max={8} step={0.1}
              value={contrast} onChange={(e) => setContrast(parseFloat(e.target.value))} />
            <span className="contrast-val">{contrast.toFixed(1)}×</span>
          </label>
        </div>
      </div>
    </div>
  )
}
