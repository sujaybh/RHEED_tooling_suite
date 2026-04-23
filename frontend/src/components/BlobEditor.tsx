import { useRef, useState } from 'react'
import { useApp } from '../App'
import { fetchBlobIntensities } from '../api'
import type { BlobResult } from '../types'

interface Props {
  displayWidth: number
  displayHeight: number
  viewBox: string
}

interface DragState {
  pointerId: number
  type: 'move' | 'resize'
  clickedBlobId: number
  startSvgPos: { x: number; y: number }
  moved: boolean
  // move: each affected blob's start center
  moveMap: Map<number, { cx: number; cy: number }>
  // resize: the blob being resized (snapshot)
  resizeBlob: BlobResult
}

export default function BlobEditor({ displayWidth, displayHeight, viewBox }: Props) {
  const {
    session,
    editedBlobs,
    setEditedBlobs,
    selectedBlobIds,
    setSelectedBlobIds,
    editSubMode,
    analysisResult,
  } = useApp()

  const svgRef = useRef<SVGSVGElement>(null)
  const dragRef = useRef<DragState | null>(null)
  // Live position overrides while dragging — committed on pointerUp
  const [liveOv, setLiveOv] = useState<Map<number, Partial<BlobResult>>>(new Map())

  const blobs = editedBlobs ?? []
  const splitRow = analysisResult?.split_row ?? (session ? session.height / 2 : 0)

  // SVG scale: how many SVG/image units equal 1 screen pixel
  const vbW = parseFloat(viewBox.split(' ')[2])
  const svgScale = vbW / displayWidth

  // Convert screen coords → SVG/image coords
  const clientToSvg = (clientX: number, clientY: number): { x: number; y: number } => {
    const svg = svgRef.current
    if (!svg) return { x: 0, y: 0 }
    const pt = svg.createSVGPoint()
    pt.x = clientX
    pt.y = clientY
    const m = svg.getScreenCTM()
    if (!m) return { x: 0, y: 0 }
    const p = pt.matrixTransform(m.inverse())
    return { x: p.x, y: p.y }
  }

  // Apply any live drag override on top of the committed blob state
  const resolve = (blob: BlobResult): BlobResult => {
    const ov = liveOv.get(blob.blob_id)
    return ov ? { ...blob, ...ov } : blob
  }

  // ── Per-blob pointer-down (called from blob circle / resize handle) ──────────
  const startBlobDrag = (
    e: React.PointerEvent,
    blob: BlobResult,
    isHandle: boolean,
  ) => {
    e.stopPropagation()
    const svgPos = clientToSvg(e.clientX, e.clientY)

    // Route all subsequent pointer events to the SVG for smooth tracking
    svgRef.current?.setPointerCapture(e.pointerId)

    if (isHandle) {
      dragRef.current = {
        pointerId: e.pointerId,
        type: 'resize',
        clickedBlobId: blob.blob_id,
        startSvgPos: svgPos,
        moved: false,
        moveMap: new Map(),
        resizeBlob: { ...blob },
      }
    } else {
      // If clicked blob is in the current selection, move all selected blobs;
      // otherwise this click will select only this blob (decided on pointerUp).
      const affectedIds = selectedBlobIds.has(blob.blob_id)
        ? Array.from(selectedBlobIds)
        : [blob.blob_id]

      const moveMap = new Map<number, { cx: number; cy: number }>()
      for (const id of affectedIds) {
        const b = blobs.find(b => b.blob_id === id)
        if (b) moveMap.set(id, { cx: b.center_x, cy: b.center_y })
      }

      dragRef.current = {
        pointerId: e.pointerId,
        type: 'move',
        clickedBlobId: blob.blob_id,
        startSvgPos: svgPos,
        moved: false,
        moveMap,
        resizeBlob: { ...blob },
      }
    }
  }

  // ── SVG background pointer-down (blobs stopPropagation, so this is background only) ──
  const handleSvgPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    const svgPos = clientToSvg(e.clientX, e.clientY)

    if (editSubMode === 'add') {
      const region: 'beam' | 'diffraction' = svgPos.y < splitRow ? 'beam' : 'diffraction'
      const color = region === 'beam' ? '#ff5252' : '#44ffbb'
      const maxId = blobs.reduce((m, b) => Math.max(m, b.blob_id), 0)
      const newBlob: BlobResult = {
        blob_id: maxId + 1,
        region,
        center_x: svgPos.x,
        center_y: svgPos.y,
        radius_px: 20,
        color,
        mean_intensities: [],
      }
      setEditedBlobs([...blobs, newBlob])
      setSelectedBlobIds(new Set([newBlob.blob_id]))

      // Fetch real intensity trace in the background; patch blob when ready
      if (session) {
        const { blob_id, center_x, center_y, radius_px } = newBlob
        fetchBlobIntensities(session.sessionId, center_x, center_y, radius_px)
          .then(mean_intensities => {
            setEditedBlobs(prev =>
              prev ? prev.map(b => b.blob_id === blob_id ? { ...b, mean_intensities } : b) : prev
            )
          })
          .catch(console.error)
      }
    } else {
      // Click on empty space → deselect all
      setSelectedBlobIds(new Set())
    }
  }

  // ── SVG pointer-move (fires for captured drags regardless of cursor position) ──
  const handleSvgPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const ds = dragRef.current
    if (!ds || e.pointerId !== ds.pointerId) return

    const svgPos = clientToSvg(e.clientX, e.clientY)
    const dx = svgPos.x - ds.startSvgPos.x
    const dy = svgPos.y - ds.startSvgPos.y

    // Mark as a real drag once cursor moves more than 3 screen-pixels
    if (!ds.moved && Math.hypot(dx, dy) > 3 * svgScale) {
      ds.moved = true
    }
    if (!ds.moved) return

    const W = session?.width ?? 1024
    const H = session?.height ?? 1024
    const newOv = new Map<number, Partial<BlobResult>>()

    if (ds.type === 'move') {
      for (const [id, start] of ds.moveMap) {
        newOv.set(id, {
          center_x: Math.max(0, Math.min(W - 1, start.cx + dx)),
          center_y: Math.max(0, Math.min(H - 1, start.cy + dy)),
        })
      }
    } else {
      const { center_x: cx, center_y: cy } = ds.resizeBlob
      const r = Math.hypot(svgPos.x - cx, svgPos.y - cy)
      newOv.set(ds.clickedBlobId, { radius_px: Math.max(4, r) })
    }

    setLiveOv(newOv)
  }

  // ── SVG pointer-up ────────────────────────────────────────────────────────────
  const handleSvgPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const ds = dragRef.current
    if (!ds || e.pointerId !== ds.pointerId) return
    dragRef.current = null

    if (ds.moved) {
      // Commit live overrides into editedBlobs
      const updated = blobs.map(b => {
        const ov = liveOv.get(b.blob_id)
        return ov ? { ...b, ...ov } : b
      })
      setEditedBlobs(updated)
      setLiveOv(new Map())
      // Keep selection on whatever was being dragged
      setSelectedBlobIds(
        ds.type === 'move'
          ? new Set(ds.moveMap.keys())
          : new Set([ds.clickedBlobId]),
      )

      // Re-fetch intensity traces for every blob whose ROI changed
      if (session) {
        const affectedIds = ds.type === 'move'
          ? Array.from(ds.moveMap.keys())
          : [ds.clickedBlobId]

        for (const id of affectedIds) {
          const blob = updated.find(b => b.blob_id === id)
          if (!blob) continue
          const { center_x, center_y, radius_px } = blob
          fetchBlobIntensities(session.sessionId, center_x, center_y, radius_px)
            .then(mean_intensities => {
              setEditedBlobs(prev =>
                prev ? prev.map(b => b.blob_id === id ? { ...b, mean_intensities } : b) : prev
              )
            })
            .catch(console.error)
        }
      }
    } else {
      // Pure click — handle selection
      setLiveOv(new Map())
      const id = ds.clickedBlobId

      if (e.shiftKey) {
        const next = new Set(selectedBlobIds)
        if (next.has(id)) next.delete(id); else next.add(id)
        setSelectedBlobIds(next)
      } else {
        // Toggle solo-select / deselect
        setSelectedBlobIds(
          selectedBlobIds.size === 1 && selectedBlobIds.has(id)
            ? new Set()
            : new Set([id]),
        )
      }
    }
  }

  const handleR = 10 * svgScale   // resize handle radius — constant ~10px on screen
  const strokeW = 2.5 * svgScale  // blob stroke — constant ~2.5px on screen

  return (
    <svg
      ref={svgRef}
      viewBox={viewBox}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: displayWidth,
        height: displayHeight,
        cursor: editSubMode === 'add' ? 'crosshair' : 'default',
        pointerEvents: 'all',
      }}
      onPointerDown={handleSvgPointerDown}
      onPointerMove={handleSvgPointerMove}
      onPointerUp={handleSvgPointerUp}
    >
      {blobs.map(blob => {
        const b = resolve(blob)
        const selected = selectedBlobIds.has(b.blob_id)

        return (
          <g key={b.blob_id}>
            {/* Main circle — transparent fill makes whole area hit-testable */}
            <circle
              cx={b.center_x}
              cy={b.center_y}
              r={b.radius_px}
              fill={selected ? `${b.color}28` : 'transparent'}
              stroke={b.color}
              strokeWidth={selected ? strokeW * 1.5 : strokeW}
              opacity={0.9}
              style={{ cursor: 'move', pointerEvents: 'all' }}
              onPointerDown={e => startBlobDrag(e, blob, false)}
            />

            {/* Dashed selection ring */}
            {selected && (
              <circle
                cx={b.center_x}
                cy={b.center_y}
                r={b.radius_px + 3 * svgScale}
                fill="none"
                stroke={b.color}
                strokeWidth={svgScale}
                strokeDasharray={`${5 * svgScale} ${3 * svgScale}`}
                opacity={0.5}
                style={{ pointerEvents: 'none' }}
              />
            )}

            {/* Label */}
            <text
              x={b.center_x + b.radius_px + 3 * svgScale}
              y={b.center_y + 4 * svgScale}
              fill={b.color}
              fontSize={14 * svgScale}
              fontFamily="JetBrains Mono, monospace"
              opacity={0.9}
              style={{ pointerEvents: 'none', userSelect: 'none' }}
            >
              {b.blob_id}
            </text>

            {/* Resize handle — right side of circle, only when selected */}
            {selected && (
              <circle
                cx={b.center_x + b.radius_px}
                cy={b.center_y}
                r={handleR}
                fill={b.color}
                stroke="white"
                strokeWidth={svgScale}
                opacity={0.95}
                style={{ cursor: 'ew-resize', pointerEvents: 'all' }}
                onPointerDown={e => startBlobDrag(e, blob, true)}
              />
            )}
          </g>
        )
      })}
    </svg>
  )
}
