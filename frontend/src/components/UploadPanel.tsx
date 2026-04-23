import { useCallback, useRef, useState } from 'react'
import { useApp } from '../App'

export default function UploadPanel() {
  const { handleUpload, uploadProgress, uploadError, handleMultiUpload, multiAnalysisError, multiUploadProgress, handleOpenLibrary } = useApp()
  const [tab, setTab] = useState<'single' | 'multi'>('single')

  return (
    <div className="upload-shell">
      <div className="upload-card">
        <div className="upload-brand">
          <div className="upload-logo">RHEED</div>
          <div className="upload-subtitle">Analysis Suite</div>
        </div>

        <div className="upload-tabs">
          <button
            className={`upload-tab ${tab === 'single' ? 'upload-tab--active' : ''}`}
            onClick={() => setTab('single')}
          >
            Single File
          </button>
          <button
            className={`upload-tab ${tab === 'multi' ? 'upload-tab--active' : ''}`}
            onClick={() => setTab('multi')}
          >
            Multi-Strip Fixer
          </button>
        </div>

        {tab === 'single'
          ? <SingleUpload progress={uploadProgress} error={uploadError} onUpload={handleUpload} />
          : <MultiUpload progress={multiUploadProgress} error={multiAnalysisError} onUpload={handleMultiUpload} />
        }

        <button className="btn-link" style={{ textAlign: 'center', marginTop: 4 }} onClick={handleOpenLibrary}>
          Open saved library →
        </button>
      </div>
    </div>
  )
}

// ── Single-file upload ─────────────────────────────────────────────────────────

function SingleUpload({
  progress, error, onUpload,
}: {
  progress: number
  error: string | null
  onUpload: (file: File, w?: number, h?: number) => void
}) {
  const [dragging, setDragging] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [width, setWidth] = useState('')
  const [height, setHeight] = useState('')
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const doUpload = useCallback(async (file: File) => {
    setUploading(true)
    const w = parseInt(width) || undefined
    const h = parseInt(height) || undefined
    await onUpload(file, w, h)
    setUploading(false)
  }, [onUpload, width, height])

  return (
    <>
      <div
        className={`drop-zone ${dragging ? 'drop-zone--active' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) doUpload(f) }}
        onClick={() => inputRef.current?.click()}
      >
        <input ref={inputRef} type="file" accept=".imm" style={{ display: 'none' }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) doUpload(f) }} />
        <div className="drop-icon">⬆</div>
        <div className="drop-label">Drop an .imm file here</div>
        <div className="drop-hint">or click to browse</div>
      </div>

      {uploading && (
        <div className="upload-progress-wrap">
          <div className="upload-progress-bar" style={{ width: `${progress}%` }} />
          <span className="upload-progress-label">
            {progress < 100 ? `Uploading… ${progress}%` : 'Parsing frames…'}
          </span>
        </div>
      )}

      {error && <div className="upload-error"><strong>Error:</strong> {error}</div>}

      <button className="btn-link" onClick={() => setShowAdvanced(v => !v)}>
        {showAdvanced ? '▾' : '▸'} Manual dimensions (if auto-detect fails)
      </button>

      {showAdvanced && (
        <div className="advanced-dims">
          <label>Width<input type="number" className="dim-input" placeholder="1024" value={width} onChange={e => setWidth(e.target.value)} /></label>
          <label>Height<input type="number" className="dim-input" placeholder="1024" value={height} onChange={e => setHeight(e.target.value)} /></label>
        </div>
      )}
    </>
  )
}

// ── Multi-file upload ──────────────────────────────────────────────────────────

function MultiUpload({
  progress, error, onUpload,
}: {
  progress: number
  error: string | null
  onUpload: (files: File[]) => void
}) {
  const [dragging, setDragging] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const imm = Array.from(incoming).filter(f => f.name.endsWith('.imm'))
    setFiles(prev => {
      const existing = new Set(prev.map(f => f.name))
      return [...prev, ...imm.filter(f => !existing.has(f.name))]
    })
  }

  const removeFile = (name: string) => setFiles(prev => prev.filter(f => f.name !== name))

  const doUpload = async () => {
    if (files.length < 2) return
    setUploading(true)
    await onUpload(files)
    setUploading(false)
  }

  return (
    <>
      <p className="upload-multi-hint">
        Upload all .imm files from a multi-strip acquisition. The suite will detect
        the electron beam position in every frame and re-sort frames into the correct strips.
      </p>

      <div
        className={`drop-zone ${dragging ? 'drop-zone--active' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files) }}
        onClick={() => inputRef.current?.click()}
      >
        <input ref={inputRef} type="file" accept=".imm" multiple style={{ display: 'none' }}
          onChange={(e) => addFiles(e.target.files)} />
        <div className="drop-icon">⬆</div>
        <div className="drop-label">Drop .imm files here</div>
        <div className="drop-hint">or click to browse — select multiple</div>
      </div>

      {files.length > 0 && (
        <ul className="multi-file-list">
          {files.map((f, i) => (
            <li key={f.name} className="multi-file-item">
              <span className="multi-file-index">{i + 1}</span>
              <span className="multi-file-name">{f.name}</span>
              <button className="multi-file-remove" onClick={() => removeFile(f.name)} title="Remove">✕</button>
            </li>
          ))}
        </ul>
      )}

      {uploading && (
        <div className="upload-progress-wrap">
          <div className="upload-progress-bar" style={{ width: `${progress}%` }} />
          <span className="upload-progress-label">
            {progress < 100 ? `Uploading… ${progress}%` : 'Parsing frames…'}
          </span>
        </div>
      )}

      {error && <div className="upload-error"><strong>Error:</strong> {error}</div>}

      <button
        className="btn-analyze"
        onClick={doUpload}
        disabled={files.length < 2 || uploading}
        style={{ width: '100%', justifyContent: 'center' }}
      >
        {uploading ? <><span className="spinner" /> Uploading…</> : `Upload ${files.length} file${files.length !== 1 ? 's' : ''}`}
      </button>
    </>
  )
}
