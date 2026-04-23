import { useEffect, useState } from 'react'
import { useApp } from '../App'
import type { SavedInstance, SavedVideo } from '../types'
import { listLibrary, deleteFromLibrary, fetchLibraryInstance } from '../api'

export default function LibraryPanel() {
  const { handleCloseLibrary, handleLoadFromLibrary } = useApp()
  const [instances, setInstances] = useState<SavedInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [saveFeedback, setSaveFeedback] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await listLibrary()
      setInstances(list)
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  // Allow parent to trigger a refresh + show a "Saved!" flash
  useEffect(() => {
    const handler = (e: Event) => {
      const msg = (e as CustomEvent<string>).detail
      setSaveFeedback(msg)
      setTimeout(() => setSaveFeedback(null), 3000)
      refresh()
    }
    window.addEventListener('rheed:saved', handler)
    return () => window.removeEventListener('rheed:saved', handler)
  }, [])

  const handleDelete = async (id: string) => {
    try {
      await deleteFromLibrary(id)
      setPendingDelete(null)
      setInstances(prev => prev.filter(i => i.id !== id))
    } catch (e: unknown) {
      setError((e as Error).message)
    }
  }

  const handleLoad = async (id: string) => {
    setLoadingId(id)
    try {
      await handleLoadFromLibrary(id)
      handleCloseLibrary()
    } catch (e: unknown) {
      setError((e as Error).message)
    } finally {
      setLoadingId(null)
    }
  }

  return (
    <div className="library-overlay" onClick={handleCloseLibrary}>
      <div className="library-drawer" onClick={e => e.stopPropagation()}>
        <div className="library-header">
          <span className="library-title">Saved Library</span>
          <button className="library-close" onClick={handleCloseLibrary} title="Close">✕</button>
        </div>

        {saveFeedback && (
          <div className="library-save-feedback">{saveFeedback}</div>
        )}

        {error && (
          <div className="upload-error" style={{ margin: '10px 16px 0' }}>{error}</div>
        )}

        <div className="library-body">
          {loading ? (
            <div className="library-empty">
              <span className="spinner" style={{ width: 14, height: 14 }} /> Loading…
            </div>
          ) : instances.length === 0 ? (
            <div className="library-empty">
              No saved instances yet.<br />
              <span style={{ fontSize: 11 }}>
                Run an analysis and click Save to store it here.
              </span>
            </div>
          ) : (
            instances.map(inst => (
              <InstanceRow
                key={inst.id}
                inst={inst}
                expanded={expandedId === inst.id}
                onToggleExpand={() => setExpandedId(expandedId === inst.id ? null : inst.id)}
                onLoad={() => handleLoad(inst.id)}
                loadingId={loadingId}
                pendingDelete={pendingDelete}
                onRequestDelete={() => setPendingDelete(inst.id)}
                onCancelDelete={() => setPendingDelete(null)}
                onConfirmDelete={() => handleDelete(inst.id)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}

// ── Instance row ───────────────────────────────────────────────────────────────

function InstanceRow({
  inst,
  expanded,
  onToggleExpand,
  onLoad,
  loadingId,
  pendingDelete,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  inst: SavedInstance
  expanded: boolean
  onToggleExpand: () => void
  onLoad: () => void
  loadingId: string | null
  pendingDelete: string | null
  onRequestDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  const isLoading = loadingId === inst.id
  const isDeleting = pendingDelete === inst.id
  const [videos, setVideos] = useState<SavedVideo[] | null>(null)
  const [videosLoading, setVideosLoading] = useState(false)

  // Lazily fetch strip details when expanded
  useEffect(() => {
    if (!expanded || inst.type !== 'multi' || videos !== null) return
    setVideosLoading(true)
    fetchLibraryInstance(inst.id)
      .then(full => setVideos(full.videos ?? []))
      .catch(() => setVideos([]))
      .finally(() => setVideosLoading(false))
  }, [expanded, inst.id, inst.type, videos])

  const date = new Date(inst.createdAt)
  const dateStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

  const meta = inst.metadata
  const dims = meta.width && meta.height ? `${meta.width}×${meta.height}` : null
  const frames = meta.nframes ? `${meta.nframes} frames` : null

  return (
    <div className={`lib-row ${isDeleting ? 'lib-row--deleting' : ''}`}>
      <div className="lib-row-main">
        <div className="lib-row-info">
          <span className={`lib-type-badge ${inst.type === 'multi' ? 'lib-type-badge--multi' : 'lib-type-badge--single'}`}>
            {inst.type === 'multi' ? `Multi (${meta.nstrips ?? inst.videoCount})` : 'Single'}
          </span>
          <span className="lib-name">{inst.name}</span>
        </div>
        <div className="lib-row-meta">
          {dims && <span className="lib-meta-chip">{dims}</span>}
          {frames && <span className="lib-meta-chip">{frames}</span>}
          <span className="lib-meta-date">{dateStr} {timeStr}</span>
        </div>
      </div>

      <div className="lib-row-actions">
        {inst.type === 'multi' && (
          <button className="lib-action-btn lib-action-btn--ghost" onClick={onToggleExpand}>
            {expanded ? '▲ Hide strips' : `▼ ${inst.videoCount} strips`}
          </button>
        )}

        {!isDeleting ? (
          <>
            <button
              className="lib-action-btn lib-action-btn--load"
              onClick={onLoad}
              disabled={isLoading || loadingId !== null}
            >
              {isLoading
                ? <><span className="spinner" style={{ width: 10, height: 10 }} /> Loading…</>
                : 'Load'}
            </button>
            <button
              className="lib-action-btn lib-action-btn--delete"
              onClick={onRequestDelete}
              disabled={isLoading || loadingId !== null}
            >
              Delete
            </button>
          </>
        ) : (
          <>
            <span className="lib-delete-confirm-label">Delete permanently?</span>
            <button className="lib-action-btn lib-action-btn--confirm-delete" onClick={onConfirmDelete}>
              Yes, delete
            </button>
            <button className="lib-action-btn lib-action-btn--ghost" onClick={onCancelDelete}>
              Cancel
            </button>
          </>
        )}
      </div>

      {expanded && inst.type === 'multi' && (
        <div className="lib-strip-list">
          {videosLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: 11 }}>
              <span className="spinner" style={{ width: 10, height: 10 }} /> Loading strips…
            </div>
          )}
          {!videosLoading && videos && videos.map((v, i) => (
            <div key={v.id} className="lib-strip-item">
              <span className="lib-strip-num">{i + 1}</span>
              <span className="lib-strip-name">{v.filename}</span>
              <span className="lib-meta-chip">{v.nframes} fr</span>
            </div>
          ))}
          <div className="lib-strip-note">
            Click Load to restore the full multi-strip session with fixed assignments.
          </div>
        </div>
      )}
    </div>
  )
}
