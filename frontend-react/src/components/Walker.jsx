import { useEffect, useRef, useState } from 'react'
import PipFigure from './PipFigure'

/**
 * Pip, walking the page.
 *
 * She is told *where to be* and *how to look* through a single `cue` object,
 * and works out how to get there herself:
 *
 *   cue.anchor  a CSS selector. She walks to it and stands on its top edge, so
 *               she is literally standing on the card she is talking about.
 *               null = patrol the floor along the bottom of the viewport.
 *   cue.side    where on the element: 'top-left' | 'top' | 'top-right'
 *   cue.mood    facial expression (see PipFigure EXPRESSIONS)
 *   cue.pose    body pose: walk, ponder, catch, read, wave, cheer, point,
 *               slump, shrug
 *   cue.line    what her speech bubble says (null = no bubble)
 *   cue.actions [{label, onClick}] - buttons inside the bubble
 *   cue.speed   1 = stroll, 2 = brisk, 4 = run
 *   cue.holding 'clipboard' | 'paper'
 *   cue.wander  when patrolling, occasionally go and stand on a [data-perch]
 *
 * Position is written straight to the DOM every animation frame. Putting it in
 * React state would re-render the whole figure 60 times a second for nothing;
 * state is only touched when something visible changes (walking vs still,
 * facing, airborne).
 */

const BASE_SPEED = 115          // px per second at speed 1
const FLOOR_GAP = 14            // her feet sit this far above the viewport bottom
const MIN_HEAD_ROOM = 205       // room needed above her feet for body + bubble
const EDGE = 64

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false
}

/** Where she should stand for an anchor, or null if it cannot be resolved. */
function anchorPoint(selector, side) {
  const el = selector ? document.querySelector(selector) : null
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width === 0 && r.height === 0) return null

  const W = window.innerWidth
  const H = window.innerHeight
  const floor = H - FLOOR_GAP
  let x =
    side === 'top-left' ? r.left + 58 :
    side === 'top-right' ? r.right - 58 :
    r.left + r.width / 2
  x = Math.max(EDGE, Math.min(W - EDGE, x))

  // Standing on the element only works if its top edge is on screen with room
  // for her above it. Otherwise she waits on the floor right underneath it.
  let y = r.top
  if (r.top < MIN_HEAD_ROOM || r.top > floor) y = floor
  return { x, y }
}

export default function Walker({ cue, enabled = true, onPipClick }) {
  const box = useRef(null)
  const pos = useRef(null)
  const dir = useRef(1)
  const last = useRef(0)
  const cueRef = useRef(cue)
  const wander = useRef({ el: null, until: 0, next: performance.now() + 14000 })

  const [moving, setMoving] = useState(true)
  const [facing, setFacing] = useState(1)
  const [airborne, setAirborne] = useState(false)
  const [edge, setEdge] = useState('center')
  const [look, setLook] = useState({ x: 0, y: 0 })

  cueRef.current = cue
  const flags = useRef({ moving: true, facing: 1, airborne: false, edge: 'center' })

  useEffect(() => {
    if (!enabled) return undefined
    const reduced = reducedMotion()
    let raf = 0

    const frame = (now) => {
      const dt = last.current ? Math.min(0.05, (now - last.current) / 1000) : 0
      last.current = now
      const c = cueRef.current || {}
      const W = window.innerWidth
      const H = window.innerHeight
      const floor = H - FLOOR_GAP

      if (!pos.current) pos.current = { x: Math.max(EDGE, W * 0.1), y: floor }
      const p = pos.current

      // ---- decide the target ------------------------------------------------
      // `hold` freezes her where she is - e.g. while a dropped file is still
      // in the air on its way to her hands.
      let target = c.hold ? { x: p.x, y: p.y }
        : c.anchor ? anchorPoint(c.anchor, c.side) : null

      // Idle wandering: now and then, go and stand on a card for a moment.
      const w = wander.current
      if (!c.anchor && !c.hold && c.wander) {
        if (!w.el && now > w.next) {
          const perches = [...document.querySelectorAll('[data-perch]')].filter((el) => {
            const r = el.getBoundingClientRect()
            return r.top > MIN_HEAD_ROOM && r.top < H - 160 && r.width > 120
          })
          if (perches.length) {
            w.el = perches[Math.floor(Math.random() * perches.length)]
            w.until = 0
          }
          w.next = now + 22000 + Math.random() * 12000
        }
        if (w.el) {
          const r = w.el.getBoundingClientRect()
          if (!document.body.contains(w.el) || r.top < MIN_HEAD_ROOM || r.top > H - 120) {
            w.el = null
          } else {
            target = { x: Math.max(EDGE, Math.min(W - EDGE, r.right - 70)), y: r.top }
          }
        }
      } else {
        w.el = null
      }

      if (!target) {
        // Patrol the floor, turning at the edges.
        const tx = dir.current > 0 ? W - EDGE : EDGE
        target = { x: tx, y: floor }
        if (Math.abs(p.x - tx) < 2) dir.current *= -1
      }

      // ---- move ------------------------------------------------------------
      const dx = target.x - p.x
      const dy = target.y - p.y
      const dist = Math.hypot(dx, dy)
      const step = BASE_SPEED * (c.speed || 1) * dt
      let isMoving = dist > 1.5

      if (reduced) {
        p.x = target.x
        p.y = target.y
        isMoving = false
      } else if (dist <= step || dist <= 1.5) {
        p.x = target.x
        p.y = target.y
        isMoving = false
      } else {
        p.x += (dx / dist) * step
        p.y += (dy / dist) * step
      }

      // Arrived at a wander perch: linger, then head back to the floor.
      if (w.el && !isMoving) {
        if (!w.until) w.until = now + 5500
        else if (now > w.until) w.el = null
      }

      if (box.current) {
        box.current.style.transform = `translate3d(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px, 0)`
      }

      // ---- sync the few things React needs to know ---------------------------
      const f = flags.current
      const face = Math.abs(dx) > 0.8 && isMoving ? Math.sign(dx) : f.facing
      const air = isMoving && Math.abs(dy) > Math.abs(dx) * 0.7 && Math.abs(dy) > 6
      const ed = p.x < 160 ? 'left' : p.x > W - 160 ? 'right' : 'center'
      if (f.moving !== isMoving) { f.moving = isMoving; setMoving(isMoving) }
      if (f.facing !== face) { f.facing = face; setFacing(face) }
      if (f.airborne !== air) { f.airborne = air; setAirborne(air) }
      if (f.edge !== ed) { f.edge = ed; setEdge(ed) }

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); last.current = 0 }
  }, [enabled])

  // Eyes wander idly; while thinking she glances side to side as if reading.
  const mood = cue?.mood || 'idle'
  useEffect(() => {
    if (!enabled || reducedMotion()) return undefined
    const thinking = mood === 'thinking'
    const id = setInterval(() => {
      setLook((l) => thinking
        ? { x: l.x > 0 ? -1.5 : 1.5, y: -0.5 }
        : { x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 1.3 })
    }, thinking ? 520 : 2400)
    return () => clearInterval(id)
  }, [mood, enabled])

  if (!enabled) return null

  const pose = cue?.pose || 'walk'
  const line = cue?.line
  const actions = cue?.actions || []

  return (
    <div className="pip-layer">
      <div className="pip-floor" />
      <div ref={box} className="pip-anchor">
        <div
          className={[
            'pip', `pose-${pose}`, `mood-${mood}`,
            moving ? 'is-walking' : 'is-still',
            airborne ? 'is-airborne' : '',
            cue?.talking ? 'is-talking' : '',
          ].join(' ')}
        >
          {line && (
            <div className={`pip-bubble edge-${edge}`} role="status">
              <span>{line}</span>
              {actions.length > 0 && (
                <span className="pip-actions">
                  {actions.map((a) => (
                    <button key={a.label} type="button"
                            className={a.primary ? 'primary' : ''} onClick={a.onClick}>
                      {a.label}
                    </button>
                  ))}
                </span>
              )}
            </div>
          )}

          <button
            type="button"
            className="pip-btn"
            onClick={onPipClick}
            title="Chat with Pip"
            aria-label={`Pip, your resume coach. She looks ${mood}. Click to chat.`}
          >
            <span className="pip-flip" style={{ transform: `scaleX(${facing})` }}>
              <PipFigure mood={mood} holding={cue?.holding || 'clipboard'} look={look} />
            </span>
          </button>
          <div className="pip-shadow" />
        </div>
      </div>
    </div>
  )
}
