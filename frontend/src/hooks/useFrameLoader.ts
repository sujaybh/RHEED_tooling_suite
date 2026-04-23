import { useCallback } from 'react'
import { frameUrl } from '../api'

const PREFETCH_RADIUS = 3

/**
 * Manages an HTMLImageElement cache for video frames.
 * The cache Map is stored in a ref (stable reference) and a version counter
 * is used to trigger re-renders when a new frame finishes loading.
 */
export function useFrameLoader(
  sessionId: string | null,
  nframes: number,
  frameCache: Map<number, HTMLImageElement>,
  loadingSet: Set<number>,
  onFrameReady: (index: number, img: HTMLImageElement) => void,
) {
  const loadFrame = useCallback(
    (index: number) => {
      if (!sessionId) return
      if (index < 0 || index >= nframes) return
      if (frameCache.has(index) || loadingSet.has(index)) return

      loadingSet.add(index)
      const img = new Image()
      img.src = frameUrl(sessionId, index)

      img.decode()
        .then(() => {
          loadingSet.delete(index)
          onFrameReady(index, img)
        })
        .catch(() => {
          loadingSet.delete(index)
        })
    },
    [sessionId, nframes, frameCache, loadingSet, onFrameReady],
  )

  const prefetchAround = useCallback(
    (center: number) => {
      for (let d = -PREFETCH_RADIUS; d <= PREFETCH_RADIUS; d++) {
        loadFrame(center + d)
      }
    },
    [loadFrame],
  )

  return { loadFrame, prefetchAround }
}
