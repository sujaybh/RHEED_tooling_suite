/**
 * ChartDownload — PNG / SVG / CSV export buttons for a Plotly chart.
 *
 * Usage:
 *   <ChartDownload
 *     getDiv={() => divRef.current}
 *     getCSV={() => ({ headers: ['frame','intensity'], rows: [[1,100],[2,110]] })}
 *     filename="pixel_512_256"
 *   />
 */
import type * as PlotlyType from 'plotly.js-dist-min'

interface CSVData {
  headers: string[]
  rows: (string | number)[][]
}

interface Props {
  getDiv: () => HTMLDivElement | null
  getCSV: () => CSVData | null
  filename: string
}

export default function ChartDownload({ getDiv, getCSV, filename }: Props) {
  async function downloadImage(format: 'png' | 'svg') {
    const div = getDiv()
    if (!div) return
    const Plotly: typeof PlotlyType = await import('plotly.js-dist-min') as typeof PlotlyType
    await Plotly.downloadImage(div, {
      format,
      filename,
      width: 1400,
      height: 520,
    })
  }

  function downloadCSV() {
    const data = getCSV()
    if (!data || !data.rows.length) return
    const lines = [
      data.headers.join(','),
      ...data.rows.map(r => r.join(',')),
    ]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `${filename}.csv`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div className="chart-download-bar">
      <span className="chart-download-label">Download</span>
      <button
        className="chart-dl-btn"
        title="Download as PNG image"
        onClick={() => downloadImage('png')}
      >
        PNG
      </button>
      <button
        className="chart-dl-btn"
        title="Download as SVG image"
        onClick={() => downloadImage('svg')}
      >
        SVG
      </button>
      <button
        className="chart-dl-btn chart-dl-btn--csv"
        title="Download raw data as CSV"
        onClick={downloadCSV}
      >
        CSV
      </button>
    </div>
  )
}
