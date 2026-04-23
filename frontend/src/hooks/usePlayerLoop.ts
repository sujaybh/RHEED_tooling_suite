import { useEffect } from 'react'

/**
 * rAF-based playback engine. Advances currentFrame at the given fps
 * without setInterval drift.
 */
export function usePlayerLoop(
  isPlaying: boolean,
  fps: number,
  nframes: number,
  setCurrentFrame: (updater: (prev: number) => number) => void,
  onEnd?: () => void,
) {
  useEffect(() => {
    if (!isPlaying || nframes === 0) return

    const intervalMs = 1000 / fps
    let lastTime = 0
    let rafId = 0

    const tick = (time: number) => {
      if (time - lastTime >= intervalMs) {
        lastTime = time
        setCurrentFrame((prev) => {
          const next = prev + 1
          if (next >= nframes) {
            onEnd?.()
            return prev // stop at last frame
          }
          return next
        })
      }
      rafId = requestAnimationFrame(tick)
    }

    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [isPlaying, fps, nframes, setCurrentFrame, onEnd])
}
