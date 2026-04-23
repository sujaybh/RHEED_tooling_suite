import { useApp } from '../App'

export default function Toolbox() {
  const {
    activeMode, setActiveMode,
    zoomRegion, setZoomRegion,
    analysisStatus,
    editSubMode, setEditSubMode,
    selectedBlobIds, setSelectedBlobIds,
    editedBlobs, setEditedBlobs,
  } = useApp()

  const deleteSelected = () => {
    if (!editedBlobs) return
    setEditedBlobs(editedBlobs.filter(b => !selectedBlobIds.has(b.blob_id)))
    setSelectedBlobIds(new Set())
  }

  return (
    <div className="toolbox">
      <span className="toolbox-label">Mode</span>

      <button
        className={`tool-btn ${activeMode === 'pixel' ? 'tool-btn--active' : ''}`}
        onClick={() => setActiveMode('pixel')}
        title="Click a pixel to plot its intensity over time"
      >
        Pixel
      </button>

      <button
        className={`tool-btn ${activeMode === 'zoom' ? 'tool-btn--active' : ''}`}
        onClick={() => setActiveMode('zoom')}
        title="Drag to zoom into a region"
      >
        Zoom
      </button>

      {analysisStatus === 'complete' && (
        <button
          className={`tool-btn ${activeMode === 'edit' ? 'tool-btn--active' : ''}`}
          onClick={() => setActiveMode('edit')}
          title="Edit blobs: move, resize, add, delete"
        >
          Edit
        </button>
      )}

      {/* Zoom reset */}
      {zoomRegion && (
        <>
          <div className="toolbox-divider" />
          <button
            className="tool-btn tool-btn--reset"
            onClick={() => setZoomRegion(null)}
            title="Reset to full image"
          >
            Reset
          </button>
        </>
      )}

      {/* Edit sub-tools */}
      {activeMode === 'edit' && (
        <>
          <div className="toolbox-divider" />

          <button
            className={`tool-btn ${editSubMode === 'select' ? 'tool-btn--active' : ''}`}
            onClick={() => setEditSubMode('select')}
            title="Select and move blobs"
          >
            Select
          </button>

          <button
            className={`tool-btn ${editSubMode === 'add' ? 'tool-btn--active' : ''}`}
            onClick={() => setEditSubMode('add')}
            title="Click anywhere to place a new blob"
          >
            Add
          </button>

          {selectedBlobIds.size > 0 && (
            <button
              className="tool-btn tool-btn--delete"
              onClick={deleteSelected}
              title={`Delete ${selectedBlobIds.size} selected blob${selectedBlobIds.size > 1 ? 's' : ''}`}
            >
              Delete ({selectedBlobIds.size})
            </button>
          )}
        </>
      )}
    </div>
  )
}
