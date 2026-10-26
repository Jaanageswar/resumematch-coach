import { useEffect, useRef, useState } from 'react'
import PipFigure from './PipFigure'

/**
 * Tiny, safe rich-text renderer for Pip's replies: "- " bullets and **bold**.
 *
 * Built from React nodes, never innerHTML, so nothing a model returns can
 * inject markup into the page.
 */
function Rich({ text }) {
  const blocks = []
  let list = null
  const inline = (s, key) =>
    s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
      part.startsWith('**') && part.endsWith('**') && part.length > 4
        ? <strong key={`${key}-${i}`}>{part.slice(2, -2)}</strong>
        : <span key={`${key}-${i}`}>{part}</span>)

  String(text || '').split('\n').forEach((raw, i) => {
    const line = raw.trimEnd()
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
    if (bullet) {
      if (!list) { list = []; blocks.push({ type: 'ul', items: list }) }
      list.push(inline(bullet[1], `li${i}`))
    } else {
      list = null
      if (line.trim()) blocks.push({ type: 'p', content: inline(line, `p${i}`) })
    }
  })

  return blocks.map((b, i) =>
    b.type === 'ul'
      ? <ul key={i}>{b.items.map((it, j) => <li key={j}>{it}</li>)}</ul>
      : <p key={i}>{b.content}</p>)
}

function JobsMessage({ message, onOpenJobs }) {
  return (
    <div className="chat-jobs">
      <p>
        Here are the roles your resume fits best
        {message.location ? <> around <strong>{message.location}</strong></> : null}.
        Each link opens a <em>live search</em>, so every result is a real listing.
      </p>
      {message.roles.map((r) => (
        <div key={r.title} className="chat-job">
          <div className="chat-job-top">
            <b>{r.title}</b>
            <span className="match">{r.match}% fit</span>
          </div>
          <div className="chat-job-links">
            {r.links.slice(0, 4).map((l) => (
              <a key={l.board} href={l.url} target="_blank" rel="noopener noreferrer">
                {l.board} ↗
              </a>
            ))}
          </div>
        </div>
      ))}
      <button type="button" className="ghost sm" onClick={onOpenJobs}>
        See all roles in the Jobs tab →
      </button>
    </div>
  )
}

export default function Chat({
  open, onClose, messages, busy, onSend, onFindJobs, onOpenJobs,
  hasResume, hasAnalysis,
}) {
  const [input, setInput] = useState('')
  const bodyRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current && inputRef.current.focus(), 60)
  }, [open])

  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, busy, open])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const chips = hasAnalysis
    ? ['What should I fix first?', 'Explain my score', 'Which skill should I learn first?',
       'Write me a stronger summary']
    : hasResume
      ? ['What stands out in my resume?', 'Write me a stronger summary',
         'How do I quantify my bullets?']
      : ['What can you do?', 'How do I write a strong bullet?', 'What do recruiters look for?']

  const send = (text) => {
    const t = (text ?? input).trim()
    if (!t || busy) return
    setInput('')
    onSend(t)
  }

  return (
    <section className="chat-panel" role="dialog" aria-label="Chat with Pip">
      <header className="chat-head">
        <span className="chat-avatar">
          <PipFigure headOnly height={36} mood={busy ? 'thinking' : 'idle'} />
        </span>
        <div className="chat-title">
          <b>Pip</b>
          <span>
            <i className={hasResume ? 'on' : ''} />
            {hasAnalysis ? 'grounded in your analysis' : hasResume ? 'reading your resume' : 'no resume yet'}
          </span>
        </div>
        <button type="button" className="icon" onClick={onClose} aria-label="Close chat">×</button>
      </header>

      <div className="chat-body" ref={bodyRef} aria-live="polite">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.kind === 'jobs'
              ? <JobsMessage message={m} onOpenJobs={onOpenJobs} />
              : <Rich text={m.content} />}
            {m.meta && <span className="msg-meta">{m.meta}</span>}
          </div>
        ))}
        {busy && (
          <div className="msg assistant typing" aria-label="Pip is typing">
            <i /><i /><i />
          </div>
        )}
      </div>

      <div className="chat-foot">
        <div className="chips">
          {chips.map((c) => (
            <button key={c} type="button" onClick={() => send(c)} disabled={busy}>{c}</button>
          ))}
          <button type="button" className="jobs" onClick={onFindJobs} disabled={busy}>
            Find jobs for me
          </button>
        </div>
        <form
          className="chat-input"
          onSubmit={(e) => { e.preventDefault(); send() }}
        >
          <input
            ref={inputRef}
            value={input}
            maxLength={2000}
            onChange={(e) => setInput(e.target.value)}
            placeholder={hasResume ? 'Ask Pip about your resume…' : 'Ask Pip anything about resumes…'}
            aria-label="Message Pip"
          />
          <button type="submit" className="send" disabled={busy || !input.trim()} aria-label="Send">
            ↑
          </button>
        </form>
      </div>
    </section>
  )
}
