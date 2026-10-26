import { useEffect, useRef, useState } from 'react'

/**
 * Page-wide file drop.
 *
 * Counting dragenter/dragleave is the reliable way to know whether a drag is
 * still over the window: every child element fires its own pair, so a plain
 * dragleave toggle flickers as the cursor crosses the page.
 */
export function useWindowFileDrop(onDrop) {
  const [dragging, setDragging] = useState(false)
  const depth = useRef(0)
  const cb = useRef(onDrop)
  cb.current = onDrop

  useEffect(() => {
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')

    const enter = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      depth.current += 1
      setDragging(true)
    }
    const over = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()               // required, or the browser refuses the drop
      e.dataTransfer.dropEffect = 'copy'
    }
    const leave = (e) => {
      if (!hasFiles(e)) return
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setDragging(false)
    }
    const drop = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()               // stop the browser opening the file
      depth.current = 0
      setDragging(false)
      const file = e.dataTransfer.files && e.dataTransfer.files[0]
      if (file) cb.current(file, { x: e.clientX, y: e.clientY })
    }

    window.addEventListener('dragenter', enter)
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragenter', enter)
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [])

  return dragging
}

export function DropOverlay({ active }) {
  if (!active) return null
  return (
    <div className="drop-overlay" aria-hidden="true">
      <div className="drop-target">
        <svg viewBox="0 0 48 48" width="46" height="46" fill="none" stroke="currentColor"
             strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 6h14l10 10v24a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z" />
          <path d="M28 6v10h10" /><path d="M24 34V22" /><path d="M19 27l5-5 5 5" />
        </svg>
        <b>Drop it - Pip will catch it</b>
        <span>PDF or TXT, up to 5 MB</span>
      </div>
    </div>
  )
}

/**
 * The resume sheet flying from where it was dropped into Pip's hands.
 * Pure CSS animation between two points passed in as custom properties.
 */
export function PaperFlight({ flight, onDone }) {
  if (!flight) return null
  return (
    <div
      className="paper-flight"
      onAnimationEnd={onDone}
      style={{
        '--fx': `${flight.from.x}px`, '--fy': `${flight.from.y}px`,
        '--tx': `${flight.to.x}px`, '--ty': `${flight.to.y}px`,
      }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 30 38" width="34" height="43">
        <rect x="1.5" y="1.5" width="27" height="35" rx="2.5" fill="#fff"
              stroke="var(--ink)" strokeWidth="2" />
        <rect x="6" y="6" width="12" height="3.5" rx="1" fill="var(--brass)" />
        <g stroke="var(--ink)" strokeWidth="1.5" strokeLinecap="round" opacity=".4">
          <path d="M6 14h18" /><path d="M6 19h18" /><path d="M6 24h14" /><path d="M6 29h18" />
        </g>
      </svg>
    </div>
  )
}
