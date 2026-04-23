import { useEffect, useRef, useState } from 'react'
import { useApp } from '../App'
import BlobOverlay from './BlobOverlay'
import BlobEditor from './BlobEditor'

export default function FrameCanvas() {
  const {
    session,
    currentFrame,
    frameCache,
    cacheVersion,
    analysisResult,
    editedBlobs,
    showBlobs,
    handlePixelClick,
    activeMode,
    zoomRegion,
    setZoomRegion,
  } = useApp()

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [hovered, setHovered] = useState<{ x: number; y: number } | null>(null)

  // Drag state — refs so event handlers always read the latest values
  const isDraggingRef = useRef(false)
  const dragStartRef = useRef<{ x: number; y: number } | null>(null)
  const dragCurrentRef = useRef<{ x: number; y: number } | null>(null)
  const didDragRef = useRef(false)
  // selRect state triggers the SVG overlay re-render
  const [selRect, setSelRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null)

  const displayW = 512
  const displayH = session ? Math.round((displayW * session.height) / session.width) : 512

  // Draw whenever frame, cache, or zoom region changes
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !session) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const img = frameCache.get(currentFrame)
    if (!img) return
    if (zoomRegion) {
      ctx.drawImage(
        img,
        zoomRegion.x, zoomRegion.y, zoomRegion.w, zoomRegion.h,
        0, 0, canvas.width, canvas.height,
      )
    } else {
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    }
  }, [currentFrame, cacheVersion, session, frameCache, zoomRegion])

  if (!session) return null

  // Convert display-pixel coords → image coords, accounting for active zoom
  const displayToImage = (dx: number, dy: number) => {
    if (zoomRegion) {
      return {
        x: Math.floor(zoomRegion.x + (dx / displayW) * zoomRegion.w),
        y: Math.floor(zoomRegion.y + (dy / displayH) * zoomRegion.h),
      }
    }
    return {
      x: Math.floor((dx / displayW) * session.width),
      y: Math.floor((dy / displayH) * session.height),
    }
  }

  const eventToDisplay = (e: React.PointerEvent | React.MouseEvent) => {
    const rect = (e.currentTarget as Element).getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(displayW, e.clientX - rect.left)),
      y: Math.max(0, Math.min(displayH, e.clientY - rect.top)),
    }
  }

  // ── Event handlers ──────────────────────────────────────────────────────────

  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (activeMode !== 'zoom') return
    e.preventDefault()
    // Capture the pointer so we keep receiving pointermove/pointerup even
    // if the cursor leaves the canvas element during the drag.
    e.currentTarget.setPointerCapture(e.pointerId)
    const pos = eventToDisplay(e)
    isDraggingRef.current = true
    dragStartRef.current = pos
    dragCurrentRef.current = pos
    didDragRef.current = false
    setSelRect(null)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const dp = eventToDisplay(e)

    // Always update the hover tooltip from pointer position
    if (!isDraggingRef.current) {
      setHovered(displayToImage(dp.x, dp.y))
    }

    if (activeMode === 'zoom' && isDraggingRef.current && dragStartRef.current) {
      dragCurrentRef.current = dp
      didDragRef.current = true
      const s = dragStartRef.current
      setSelRect({
        x: Math.min(s.x, dp.x),
        y: Math.min(s.y, dp.y),
        w: Math.abs(dp.x - s.x),
        h: Math.abs(dp.y - s.y),
      })
      // Update tooltip to show image coords of current drag position
      setHovered(displayToImage(dp.x, dp.y))
    }
  }

  const finalizeDrag = () => {
    if (!isDraggingRef.current) return
    isDraggingRef.current = false

    if (didDragRef.current && dragStartRef.current && dragCurrentRef.current) {
      const tl = displayToImage(
        Math.min(dragStartRef.current.x, dragCurrentRef.current.x),
        Math.min(dragStartRef.current.y, dragCurrentRef.current.y),
      )
      const br = displayToImage(
        Math.max(dragStartRef.current.x, dragCurrentRef.current.x),
        Math.max(dragStartRef.current.y, dragCurrentRef.current.y),
      )
      const w = br.x - tl.x
      const h = br.y - tl.y
      if (w >= 8 && h >= 8) {
        setZoomRegion({ x: tl.x, y: tl.y, w, h })
      }
    }

    dragStartRef.current = null
    dragCurrentRef.current = null
    // Reset didDrag so pixel clicks work again after switching modes
    didDragRef.current = false
    setSelRect(null)
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (activeMode !== 'zoom') return
    finalizeDrag()
  }

  const handlePointerLeave = (e: React.PointerEvent<HTMLCanvasElement>) => {
    // Only clear hover when not dragging (captured pointer keeps sending events)
    if (!isDraggingRef.current) setHovered(null)
  }

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (activeMode !== 'pixel') return
    // didDragRef guards against a click firing right after a zoom drag gesture
    if (didDragRef.current) return
    const dp = eventToDisplay(e)
    const { x, y } = displayToImage(dp.x, dp.y)
    handlePixelClick(x, y)
  }

  // Blob overlay viewBox: full image or zoomed sub-region
  const blobViewBox = zoomRegion
    ? `${zoomRegion.x} ${zoomRegion.y} ${zoomRegion.w} ${zoomRegion.h}`
    : `0 0 ${session.width} ${session.height}`

  return (
    <div
      className="canvas-wrapper"
      style={{ width: displayW, height: displayH, position: 'relative' }}
    >
      <canvas
        ref={canvasRef}
        width={session.width}
        height={session.height}
        style={{
          width: displayW,
          height: displayH,
          imageRendering: 'pixelated',
          cursor: activeMode === 'edit' ? 'default' : 'crosshair',
          display: 'block',
        }}
        onClick={handleClick}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
      />

      {/* Drag selection overlay (zoom mode only) */}
      {selRect && (
        <svg
          style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
          width={displayW}
          height={displayH}
        >
          <rect x={0} y={0} width={displayW} height={displayH} fill="rgba(0,0,0,0.45)" />
          <rect
            x={selRect.x}
            y={selRect.y}
            width={selRect.w}
            height={selRect.h}
            fill="rgba(88,196,220,0.08)"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeDasharray="5 3"
          />
        </svg>
      )}

      {/* Edit mode: interactive blob editor replaces the read-only overlay */}
      {activeMode === 'edit'
        ? <BlobEditor
            displayWidth={displayW}
            displayHeight={displayH}
            viewBox={blobViewBox}
          />
        : showBlobs && (editedBlobs ?? analysisResult?.blobs) && (
            <BlobOverlay
              blobs={(editedBlobs ?? analysisResult!.blobs)}
              imageWidth={session.width}
              imageHeight={session.height}
              displayWidth={displayW}
              displayHeight={displayH}
              viewBox={blobViewBox}
            />
          )
      }

      {hovered && (
        <div className="pixel-tooltip">
          ({hovered.x}, {hovered.y})
        </div>
      )}
    </div>
  )
}
