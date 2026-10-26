import { useEffect, useState } from 'react'
import { band, verdict } from '../api'

const R = 54
const CIRC = 2 * Math.PI * R
const SWEEP = 0.75          // a 270-degree dial, not a full circle

/** Animated score dial. Counts up from 0 so the number feels earned. */
export default function ScoreRing({ score, size = 140 }) {
  const [shown, setShown] = useState(0)

  useEffect(() => {
    const reduced = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduced) { setShown(score); return undefined }

    let raf
    const start = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - start) / 750)
      // ease-out cubic
      setShown(Math.round(score * (1 - Math.pow(1 - t, 3))))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [score])

  const colour = band(score)
  const filled = (shown / 100) * CIRC * SWEEP

  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox="0 0 128 128">
        <g transform="rotate(135 64 64)">
          <circle cx="64" cy="64" r={R} fill="none" stroke="var(--rule)"
                  strokeWidth="11" strokeLinecap="round"
                  strokeDasharray={`${CIRC * SWEEP} ${CIRC}`} />
          <circle cx="64" cy="64" r={R} fill="none" stroke={colour}
                  strokeWidth="11" strokeLinecap="round"
                  strokeDasharray={`${filled} ${CIRC}`} />
        </g>
      </svg>
      <div className="ring-num">
        <b style={{ color: colour }}>{shown}</b>
        <span>out of 100</span>
      </div>
      <div className="ring-verdict" style={{ color: colour }}>{verdict(score)}</div>
    </div>
  )
}
