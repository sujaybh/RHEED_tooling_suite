import { useApp } from '../App'

const FPS_OPTIONS = [1, 2, 5, 10, 15, 24, 30]

export default function PlayerControls() {
  const {
    session,
    currentFrame,
    isPlaying,
    fps,
    contrast,
    setCurrentFrame,
    setIsPlaying,
    setFps,
    setContrast,
  } = useApp()

  if (!session) return null
  const { nframes } = session

  const go = (f: number) => setCurrentFrame(Math.max(0, Math.min(nframes - 1, f)))

  return (
    <div className="player-controls">
      <div className="player-buttons">
        <button className="ctrl-btn" title="First frame" onClick={() => go(0)}>⏮</button>
        <button className="ctrl-btn" title="Previous frame" onClick={() => go(currentFrame - 1)}>⏪</button>
        <button
          className="ctrl-btn ctrl-btn--play"
          title={isPlaying ? 'Pause' : 'Play'}
          onClick={() => setIsPlaying(!isPlaying)}
        >
          {isPlaying ? '⏸' : '▶'}
        </button>
        <button className="ctrl-btn" title="Next frame" onClick={() => go(currentFrame + 1)}>⏩</button>
        <button className="ctrl-btn" title="Last frame" onClick={() => go(nframes - 1)}>⏭</button>
      </div>

      <input
        type="range"
        className="scrubbar"
        min={0}
        max={nframes - 1}
        value={currentFrame}
        onChange={(e) => go(parseInt(e.target.value))}
      />

      <div className="player-meta">
        <span className="frame-counter">
          Frame <strong>{currentFrame + 1}</strong> / {nframes}
        </span>

        <label className="ctrl-label">
          FPS
          <select
            className="ctrl-select"
            value={fps}
            onChange={(e) => setFps(parseInt(e.target.value))}
          >
            {FPS_OPTIONS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </label>

        <label className="ctrl-label">
          Brightness
          <input
            type="range"
            className="contrast-slider"
            min={0.5}
            max={8}
            step={0.1}
            value={contrast}
            onChange={(e) => setContrast(parseFloat(e.target.value))}
          />
          <span className="contrast-val">{contrast.toFixed(1)}×</span>
        </label>
      </div>
    </div>
  )
}
