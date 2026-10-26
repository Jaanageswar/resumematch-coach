/**
 * Pip's body: a pure SVG drawing driven entirely by props.
 *
 * Movement lives in Walker.jsx; this file only answers "what does she look
 * like right now". Keeping the two apart is what lets the chat panel reuse her
 * face as an avatar (`headOnly`) without dragging the walking logic along.
 *
 * Chibi proportions - head about 40% of her height - are the single biggest
 * reason she reads as cute rather than as a diagram of a person.
 */

export const EXPRESSIONS = {
  idle:       { color: 'var(--ink)',    eyes: 'open',   brow: 'neutral', mouth: 'smile-sm' },
  thinking:   { color: 'var(--brass)',  eyes: 'squint', brow: 'raised',  mouth: 'hmm' },
  reading:    { color: 'var(--brass)',  eyes: 'down',   brow: 'neutral', mouth: 'hmm' },
  excited:    { color: 'var(--brass)',  eyes: 'wide',   brow: 'raised',  mouth: 'open' },
  talking:    { color: 'var(--ink)',    eyes: 'open',   brow: 'raised',  mouth: 'talk' },
  wincing:    { color: 'var(--rust)',   eyes: 'shut',   brow: 'worried', mouth: 'frown' },
  hopeful:    { color: 'var(--amber)',  eyes: 'open',   brow: 'one-up',  mouth: 'smile-sm' },
  pleased:    { color: 'var(--sage)',   eyes: 'happy',  brow: 'neutral', mouth: 'smile' },
  delighted:  { color: 'var(--sage)',   eyes: 'star',   brow: 'raised',  mouth: 'grin' },
  apologetic: { color: 'var(--amber)',  eyes: 'flat',   brow: 'worried', mouth: 'wobble' },
}

const MOUTHS = {
  'smile-sm': 'M 42 46 Q 50 51 58 46',
  smile:      'M 41 44 Q 50 54 59 44',
  grin:       'M 40 43 Q 50 57 60 43',
  hmm:        'M 46 47 Q 50 45.5 54 47',
  frown:      'M 42 50 Q 50 42 58 50',
  wobble:     'M 43 48 Q 46.5 45 50 48 T 57 48',
}

const L = 40
const R = 60
const Y = 37

function Eyes({ kind, look }) {
  const line = { fill: 'none', strokeWidth: 2.2, strokeLinecap: 'round' }
  switch (kind) {
    case 'shut':
      return <g {...line}><path d={`M${L - 5} ${Y - 1} q5 4.5 10 0`} /><path d={`M${R - 5} ${Y - 1} q5 4.5 10 0`} /></g>
    case 'happy':
      return <g {...line}><path d={`M${L - 5} ${Y + 2} q5 -6 10 0`} /><path d={`M${R - 5} ${Y + 2} q5 -6 10 0`} /></g>
    case 'flat':
      return <g strokeWidth="2.2" strokeLinecap="round"><path d={`M${L - 4} ${Y} h8`} /><path d={`M${R - 4} ${Y} h8`} /></g>
    case 'squint':
      return <g {...line}><path d={`M${L - 5} ${Y} q5 3 10 0`} /><path d={`M${R - 5} ${Y} q5 3 10 0`} /></g>
    case 'star':
      return (
        <g fill="currentColor" stroke="none">
          {[L, R].map((cx) => (
            <polygon key={cx}
              points={`${cx},${Y - 6} ${cx + 1.9},${Y - 1.5} ${cx + 6.5},${Y} ${cx + 1.9},${Y + 1.5} ${cx},${Y + 6} ${cx - 1.9},${Y + 1.5} ${cx - 6.5},${Y} ${cx - 1.9},${Y - 1.5}`} />
          ))}
        </g>
      )
    case 'down':
      // Reading: lids half down, pupils scanning a line of text.
      return (
        <g>
          {[L, R].map((cx) => (
            <g key={cx}>
              <circle cx={cx} cy={Y} r="4.8" fill="var(--card)" strokeWidth="2" />
              <g className="pip-scan">
                <circle cx={cx} cy={Y + 1.8} r="2.3" fill="currentColor" stroke="none" />
              </g>
              <path d={`M${cx - 5.2} ${Y - 0.6} q5.2 -4.2 10.4 0`} fill="var(--card)" strokeWidth="2" />
            </g>
          ))}
        </g>
      )
    case 'wide':
      return (
        <g className="pip-eyes">
          {[L, R].map((cx) => (
            <g key={cx}>
              <circle cx={cx} cy={Y} r="5.6" fill="var(--card)" strokeWidth="2" />
              <circle cx={cx + look.x * 0.6} cy={Y + look.y * 0.6} r="3.1" fill="currentColor" stroke="none" />
              <circle cx={cx + 1.4} cy={Y - 1.5} r="1.1" fill="var(--card)" stroke="none" />
            </g>
          ))}
        </g>
      )
    default:
      return (
        <g className="pip-eyes">
          {[L, R].map((cx) => (
            <g key={cx}>
              <circle cx={cx} cy={Y} r="4.8" fill="var(--card)" strokeWidth="2" />
              <circle cx={cx + look.x} cy={Y + look.y} r="2.5" fill="currentColor" stroke="none" />
              <circle cx={cx + look.x + 1} cy={Y + look.y - 1} r=".8" fill="var(--card)" stroke="none" />
            </g>
          ))}
        </g>
      )
  }
}

function Brows({ kind }) {
  const p = { fill: 'none', strokeWidth: 1.9, strokeLinecap: 'round' }
  if (kind === 'worried') return <g {...p}><path d="M35 28 q5 3.5 10 1" /><path d="M55 29 q5 -2.5 10 -1" /></g>
  if (kind === 'raised')  return <g {...p}><path d="M35 25.5 q5 -2.5 10 0" /><path d="M55 25.5 q5 -2.5 10 0" /></g>
  if (kind === 'one-up')  return <g {...p}><path d="M35 25 q5 -2.5 10 1" /><path d="M55 29 q5 -2 10 0" /></g>
  return <g {...p}><path d="M35 28 q5 -2 10 0" /><path d="M55 28 q5 -2 10 0" /></g>
}

function Mouth({ kind }) {
  if (kind === 'open') {
    return <ellipse cx="50" cy="48.5" rx="3.6" ry="4.4" fill="currentColor" stroke="none" opacity=".85" />
  }
  if (kind === 'talk') {
    return (
      <g className="pip-talk">
        <ellipse cx="50" cy="48" rx="4.2" ry="3.6" fill="currentColor" stroke="none" opacity=".85" />
      </g>
    )
  }
  return <path className="pip-mouth" d={MOUTHS[kind] || MOUTHS['smile-sm']} fill="none"
               strokeWidth="2.4" strokeLinecap="round" />
}

function Head({ expression, look, mood }) {
  return (
    <g className="head">
      <path
        d="M50 8 C68 8 80 20 80.5 36 C81 53 67 64 50 64 C33 64 20 53 20 37 C20 20 32 8.5 50 8 Z"
        fill="var(--card)" strokeWidth="2.7" strokeLinejoin="round"
      />
      {/* hair: a soft fringe plus a tuft, tinted with her current colour */}
      <path d="M24 28 q8 -14 26 -16 q18 2 26 16 q-9 -7 -26 -7 q-17 0 -26 7 Z"
            fill="currentColor" stroke="none" opacity=".2" />
      <path d="M50 8 q5 -7 11 -4 q-4 1 -5 5" fill="currentColor" stroke="none" opacity=".85" />

      <Brows kind={expression.brow} />
      <Eyes kind={expression.eyes} look={look} />
      <Mouth kind={expression.mouth} />

      {/* rosy cheeks */}
      <g stroke="none" fill="var(--blush)"
         opacity={mood === 'pleased' || mood === 'delighted' || mood === 'excited' ? 0.75 : 0.45}>
        <ellipse cx="30.5" cy="46.5" rx="5" ry="3.2" />
        <ellipse cx="69.5" cy="46.5" rx="5" ry="3.2" />
      </g>

      {mood === 'wincing' && (
        <path className="pip-sweat" d="M76 22 q3.5 6 0 8.5 q-3.5 -2.5 0 -8.5 Z"
              fill="var(--sky)" stroke="none" />
      )}
      {mood === 'thinking' && (
        <g stroke="none" fill="currentColor">
          <circle className="dot d1" cx="84" cy="18" r="2.4" />
          <circle className="dot d2" cx="91" cy="12" r="1.8" />
          <circle className="dot d3" cx="96" cy="7" r="1.3" />
        </g>
      )}
    </g>
  )
}

function Held({ holding }) {
  if (holding === 'paper') {
    // The user's resume, caught and held: a sheet with a name bar and lines.
    return (
      <g className="held paper">
        <rect x="63" y="88" width="23" height="30" rx="2" fill="#fff" strokeWidth="2" />
        <rect x="67" y="92" width="11" height="3.2" rx="1" fill="var(--brass)" stroke="none" />
        <g strokeWidth="1.4" strokeLinecap="round" opacity=".42">
          <path d="M67 99 h15" /><path d="M67 103 h15" /><path d="M67 107 h12" />
          <path d="M67 111 h15" /><path d="M67 115 h9" />
        </g>
      </g>
    )
  }
  return (
    <g className="held clipboard">
      <rect x="66" y="94" width="20" height="25" rx="2.5" fill="var(--card)" strokeWidth="2.2" />
      <rect x="72" y="91" width="8" height="5" rx="1.5" fill="currentColor" stroke="none" opacity=".75" />
      <g strokeWidth="1.6" strokeLinecap="round" opacity=".45">
        <path d="M70 101 h12" /><path d="M70 106 h12" /><path d="M70 111 h8" />
      </g>
    </g>
  )
}

export default function PipFigure({
  mood = 'idle',
  holding = 'clipboard',
  look = { x: 0, y: 0 },
  height = 132,
  headOnly = false,
  className = '',
}) {
  const expression = EXPRESSIONS[mood] || EXPRESSIONS.idle

  if (headOnly) {
    return (
      <svg viewBox="16 2 70 66" height={height} className={`pip-head-only ${className}`}
           stroke="currentColor" style={{ color: expression.color }} aria-hidden="true">
        <Head expression={expression} look={look} mood={mood} />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 100 150" height={height} className={`pip-svg ${className}`}
         stroke="currentColor" aria-hidden="true">
      <g className="arm arm-back">
        <path d="M34 78 q-9 10 -7 21" fill="none" strokeWidth="5.5" strokeLinecap="round" />
        <circle cx="27" cy="100" r="3.4" fill="var(--skin)" strokeWidth="1.8" />
      </g>

      <g className="leg leg-back">
        <path d="M44 104 v22" fill="none" strokeWidth="6" strokeLinecap="round" />
        <path d="M37 130 q6 -6 13 0 Z" fill="var(--shoe)" strokeWidth="2" strokeLinejoin="round" />
      </g>
      <g className="leg leg-front">
        <path d="M56 104 v22" fill="none" strokeWidth="6" strokeLinecap="round" />
        <path d="M51 130 q6 -6 13 0 Z" fill="var(--shoe)" strokeWidth="2" strokeLinejoin="round" />
      </g>

      <g className="torso">
        <path
          d="M50 62 c11 0 17 6 17 15 l0 16 c0 7 -7 11 -17 11 c-10 0 -17 -4 -17 -11 l0 -16 c0 -9 6 -15 17 -15 Z"
          fill="var(--jacket)" strokeWidth="2.6" strokeLinejoin="round"
        />
        <path d="M43 65 l7 7 l7 -7" fill="var(--card)" strokeWidth="2" strokeLinejoin="round" />
        <path d="M50 72 v22" strokeWidth="1.4" opacity=".35" />
      </g>

      <g className="arm arm-front">
        <path d="M66 78 q10 9 8 20" fill="none" strokeWidth="5.5" strokeLinecap="round" />
        <Held holding={holding} />
        <circle cx="74" cy="99" r="3.4" fill="var(--skin)" strokeWidth="1.8" />
      </g>

      <Head expression={expression} look={look} mood={mood} />

      {mood === 'delighted' && (
        <g className="sparkles" stroke="none" fill="var(--brass)">
          <polygon points="12,20 14,25 19,27 14,29 12,34 10,29 5,27 10,25" />
          <polygon points="90,44 91.5,48 95.5,49.5 91.5,51 90,55 88.5,51 84.5,49.5 88.5,48" opacity=".7" />
        </g>
      )}
      {mood === 'excited' && (
        <g className="sparkles" stroke="none" fill="var(--brass)">
          <polygon points="10,40 11.5,44 15.5,45.5 11.5,47 10,51 8.5,47 4.5,45.5 8.5,44" opacity=".8" />
        </g>
      )}
    </svg>
  )
}
