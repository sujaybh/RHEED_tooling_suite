import type { BlobResult } from '../types'

interface Props {
  blobs: BlobResult[]
  imageWidth: number
  imageHeight: number
  displayWidth: number
  displayHeight: number
  viewBox?: string
}

/**
 * SVG layer positioned absolutely over the canvas.
 * Uses the same viewBox as the raw image so blob coordinates are in image-space.
 * When zoomed, pass a viewBox matching the zoomed sub-region so blobs stay aligned.
 */
export default function BlobOverlay({ blobs, imageWidth, imageHeight, displayWidth, displayHeight, viewBox }: Props) {
  return (
    <svg
      viewBox={viewBox ?? `0 0 ${imageWidth} ${imageHeight}`}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: displayWidth,
        height: displayHeight,
        pointerEvents: 'none',
      }}
    >
      {blobs.map((blob) => (
        <g key={blob.blob_id}>
          <circle
            cx={blob.center_x}
            cy={blob.center_y}
            r={blob.radius_px}
            fill="none"
            stroke={blob.color}
            strokeWidth={2.5}
            opacity={0.85}
          />
          <text
            x={blob.center_x + blob.radius_px + 2}
            y={blob.center_y + 4}
            fill={blob.color}
            fontSize={14}
            fontFamily="JetBrains Mono, monospace"
            opacity={0.9}
          >
            {blob.blob_id}
          </text>
        </g>
      ))}
    </svg>
  )
}
