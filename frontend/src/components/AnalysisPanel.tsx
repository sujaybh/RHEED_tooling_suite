import { useEffect, useRef } from 'react'
import { useApp } from '../App'
import type { BlobResult } from '../types'

import type * as PlotlyType from 'plotly.js-dist-min'

let Plotly: typeof PlotlyType | null = null
import('plotly.js-dist-min').then((m) => { Plotly = m as typeof PlotlyType })

export default function AnalysisPanel() {
  const {
    analysisStatus,
    analysisResult,
    analysisError,
    editedBlobs,
    showBlobs,
    setShowBlobs,
    handleAnalyze,
    currentFrame,
  } = useApp()

  // Use the edited blob list if available; fall back to raw analysis result
  const displayBlobs = editedBlobs ?? analysisResult?.blobs ?? []
  const beamCount = displayBlobs.filter(b => b.region === 'beam').length
  const diffCount = displayBlobs.filter(b => b.region === 'diffraction').length

  const isRunning = analysisStatus === 'running'
  const isDone = analysisStatus === 'complete'

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        Blob Analysis
        <div className="panel-card-actions">
          {isDone && (
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={showBlobs}
                onChange={(e) => setShowBlobs(e.target.checked)}
              />
              Show blobs
            </label>
          )}
          <button
            className={`btn-analyze ${isRunning ? 'btn-analyze--running' : ''}`}
            onClick={() => handleAnalyze()}
            disabled={isRunning}
          >
            {isRunning ? (
              <><span className="spinner" /> Analyzing…</>
            ) : (
              isDone ? 'Re-analyze' : 'Analyze'
            )}
          </button>
        </div>
      </div>

      {isRunning && (
        <div className="analysis-hint">
          Detecting blobs in first 5 frames, then tracking across all frames.
          This typically takes 10–30 seconds.
        </div>
      )}

      {analysisError && (
        <div className="upload-error">{analysisError}</div>
      )}

      {isDone && analysisResult && (
        <>
          <div className="blob-summary">
            <span className="blob-badge blob-badge--beam">
              {beamCount} beam
            </span>
            <span className="blob-badge blob-badge--diff">
              {diffCount} diffraction
            </span>
          </div>

          <BlobPlot blobs={displayBlobs} currentFrame={currentFrame} />
        </>
      )}

      {analysisStatus === 'idle' && (
        <div className="panel-placeholder">
          Click Analyze to detect and track diffraction spots automatically.
        </div>
      )}
    </div>
  )
}

// ── Blob intensity plot ────────────────────────────────────────────────────────

function BlobPlot({ blobs, currentFrame }: { blobs: BlobResult[]; currentFrame: number }) {
  const divRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

  const buildTraces = () =>
    blobs.map((blob) => ({
      x: Array.from({ length: blob.mean_intensities.length }, (_, i) => i + 1),
      y: blob.mean_intensities,
      type: 'scatter' as const,
      mode: 'lines' as const,
      name: `#${blob.blob_id} (${blob.region})`,
      line: { color: blob.color, width: 1.5 },
    }))

  const buildLayout = (frame: number) => ({
    paper_bgcolor: '#1a1d2e',
    plot_bgcolor: '#141624',
    font: { color: '#c9d1d9', family: 'Inter, sans-serif', size: 11 },
    margin: { t: 28, r: 12, b: 40, l: 52 },
    xaxis: {
      title: { text: 'Frame', standoff: 6 },
      gridcolor: '#2d3148',
      zerolinecolor: '#2d3148',
      color: '#7a8099',
    },
    yaxis: {
      title: { text: 'Mean intensity (uint16)', standoff: 6 },
      gridcolor: '#2d3148',
      zerolinecolor: '#2d3148',
      color: '#7a8099',
    },
    legend: {
      bgcolor: 'rgba(0,0,0,0)',
      font: { size: 10 },
      orientation: 'v' as const,
      x: 1.01,
      y: 1,
    },
    shapes: [
      {
        type: 'line' as const,
        x0: frame + 1, x1: frame + 1,
        y0: 0, y1: 1, yref: 'paper' as const,
        line: { color: '#ffdd57', width: 1.5, dash: 'dash' as const },
      },
    ],
    height: 260,
  })

  useEffect(() => {
    if (!divRef.current || !Plotly) return
    if (!initialized.current) {
      Plotly.newPlot(divRef.current, buildTraces(), buildLayout(currentFrame), {
        displayModeBar: false,
        responsive: true,
      })
      initialized.current = true
    } else {
      Plotly.react(divRef.current, buildTraces(), buildLayout(currentFrame), {
        displayModeBar: false,
        responsive: true,
      })
    }
  }, [blobs])

  // Cheap vertical-line update on frame change
  useEffect(() => {
    if (!divRef.current || !Plotly || !initialized.current) return
    Plotly.relayout(divRef.current, {
      'shapes[0].x0': currentFrame + 1,
      'shapes[0].x1': currentFrame + 1,
    } as Partial<PlotlyType.Layout>)
  }, [currentFrame])

  return <div ref={divRef} style={{ width: '100%' }} />
}
