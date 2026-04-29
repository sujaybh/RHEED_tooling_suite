import { useEffect, useRef } from 'react'
import { useApp } from '../App'
import ChartDownload from './ChartDownload'

import type * as PlotlyType from 'plotly.js-dist-min'

// Lazy-import Plotly to keep initial bundle light
let Plotly: typeof PlotlyType | null = null
import('plotly.js-dist-min').then((m) => { Plotly = m as typeof PlotlyType })

export default function PixelPlot() {
  const { pixelSelection, pixelLoading, currentFrame, session } = useApp()
  const divRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

  // Build/update the plot whenever data changes
  useEffect(() => {
    if (!divRef.current || !Plotly || !pixelSelection) return

    const frames = Array.from({ length: pixelSelection.intensities.length }, (_, i) => i + 1)

    const trace = {
      x: frames,
      y: pixelSelection.intensities,
      type: 'scatter' as const,
      mode: 'lines' as const,
      name: `(${pixelSelection.x}, ${pixelSelection.y})`,
      line: { color: '#58c4dc', width: 1.5 },
    }

    const vline = {
      type: 'line' as const,
      x0: currentFrame + 1,
      x1: currentFrame + 1,
      y0: 0,
      y1: 1,
      yref: 'paper' as const,
      line: { color: '#ffdd57', width: 1.5, dash: 'dash' as const },
    }

    const layout = {
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
        title: { text: 'Intensity (uint16)', standoff: 6 },
        gridcolor: '#2d3148',
        zerolinecolor: '#2d3148',
        color: '#7a8099',
      },
      shapes: [vline],
      showlegend: false,
      height: 200,
    }

    const config = { displayModeBar: false, responsive: true }

    if (!initialized.current) {
      Plotly.newPlot(divRef.current, [trace], layout, config)
      initialized.current = true
    } else {
      Plotly.react(divRef.current, [trace], layout, config)
    }
  }, [pixelSelection, currentFrame])

  // Update only the vertical line when frame changes (avoid full redraw)
  useEffect(() => {
    if (!divRef.current || !Plotly || !initialized.current || !pixelSelection) return
    Plotly.relayout(divRef.current, {
      'shapes[0].x0': currentFrame + 1,
      'shapes[0].x1': currentFrame + 1,
    } as Partial<PlotlyType.Layout>)
  }, [currentFrame, pixelSelection])

  if (!pixelSelection && !pixelLoading) {
    return (
      <div className="panel-card">
        <div className="panel-card-header">Pixel Intensity</div>
        <div className="panel-placeholder">
          Click any pixel on the video to plot its intensity over time.
        </div>
      </div>
    )
  }

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        Pixel Intensity
        {pixelSelection && (
          <span className="panel-card-subtitle">
            ({pixelSelection.x}, {pixelSelection.y})
          </span>
        )}
      </div>
      {pixelLoading ? (
        <div className="panel-placeholder">Loading…</div>
      ) : (
        <>
          <div ref={divRef} style={{ width: '100%' }} />
          {pixelSelection && (
            <ChartDownload
              getDiv={() => divRef.current}
              getCSV={() => ({
                headers: ['frame', 'intensity'],
                rows: pixelSelection.intensities.map((v, i) => [i + 1, v]),
              })}
              filename={`pixel_${pixelSelection.x}_${pixelSelection.y}`}
            />
          )}
        </>
      )}
    </div>
  )
}
