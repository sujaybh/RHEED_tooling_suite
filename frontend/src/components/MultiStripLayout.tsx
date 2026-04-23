import { useApp } from '../App'
import MultiPlayer from './MultiPlayer'

export default function MultiStripLayout() {
  const {
    multiSession,
    multiAnalysisStatus,
    multiResult,
    multiAnalysisError,
    currentStrip,
    setCurrentStrip,
    handleMultiAnalyze,
    handleMultiNewFile,
    handleOpenStripInMain,
    handleOpenLibrary,
    handleSaveMulti,
    saving,
    saveError,
    saveSuccess,
    multiSavedInstanceId,
  } = useApp()

  if (!multiSession) return null

  const isRunning = multiAnalysisStatus === 'running'
  const isDone    = multiAnalysisStatus === 'complete'

  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="topbar-title">RHEED — Multi-Strip Fixer</span>
        <span className="topbar-meta">
          {multiSession.nstrips} strips · {multiSession.nframes} frames ·{' '}
          {multiSession.width}×{multiSession.height}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {saveError && <span className="topbar-save-error">{saveError}</span>}
          {!isDone && (
            <button
              className={`btn-analyze ${isRunning ? 'btn-analyze--running' : ''}`}
              onClick={handleMultiAnalyze}
              disabled={isRunning}
            >
              {isRunning
                ? <><span className="spinner" /> Sorting strips…</>
                : 'Find & Fix Strips'}
            </button>
          )}
          <button className="btn-ghost" onClick={handleOpenLibrary}>Library</button>
          {isDone && (
            <button
              className={`btn-ghost btn-ghost--save ${saveSuccess ? 'btn-ghost--saved' : ''}`}
              onClick={handleSaveMulti}
              disabled={saving}
            >
              {saving
                ? <><span className="spinner" style={{ width: 10, height: 10, marginRight: 5 }} />Saving…</>
                : saveSuccess
                  ? 'Saved ✓'
                  : multiSavedInstanceId ? 'Update' : 'Save'}
            </button>
          )}
          <button className="btn-ghost" onClick={handleMultiNewFile}>New Files</button>
        </div>
      </header>

      {isRunning && (
        <div className="multi-status-bar">
          <span className="spinner" />
          Detecting beam position in every frame across {multiSession.nstrips} strips…
        </div>
      )}

      {multiAnalysisError && (
        <div className="multi-status-bar multi-status-bar--error">
          {multiAnalysisError}
        </div>
      )}

      {!isDone && !isRunning && (
        <div className="multi-idle-hint">
          <p>
            Files loaded. Click <strong>Find &amp; Fix Strips</strong> to detect the electron
            beam position in every frame and reassign frames to the correct strips.
          </p>
          <ul className="multi-file-list" style={{ marginTop: 12, maxWidth: 480 }}>
            {multiSession.filenames.map((name, i) => (
              <li key={name} className="multi-file-item">
                <span className="multi-file-index">{i + 1}</span>
                <span className="multi-file-name">{name}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {isDone && multiResult && (
        <div className="main-content">
          <div className="multi-strip-sidebar">
            <div className="multi-sidebar-label">Fixed Strips</div>
            {multiSession.filenames.map((name, i) => (
              <div key={i} className="strip-tab-row">
                <button
                  className={`strip-tab ${i === currentStrip ? 'strip-tab--active' : ''}`}
                  onClick={() => setCurrentStrip(i)}
                  title={name}
                >
                  <span className="strip-tab-num">{i + 1}</span>
                  <span className="strip-tab-name">{name}</span>
                </button>
                <button
                  className="strip-open-btn"
                  title="Open in full analysis GUI"
                  onClick={() => handleOpenStripInMain(i)}
                >
                  Analyze →
                </button>
              </div>
            ))}
          </div>

          <div className="left-pane" style={{ flex: 1 }}>
            <MultiPlayer
              key={currentStrip}
              sessionId={multiSession.sessionId}
              stripIndex={currentStrip}
              nframes={multiSession.nframes}
              width={multiSession.width}
              height={multiSession.height}
              assignments={multiResult.assignments}
              stripNames={multiSession.filenames}
            />
          </div>
        </div>
      )}
    </div>
  )
}
