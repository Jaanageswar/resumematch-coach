import { useCallback, useEffect, useRef, useState } from 'react'
import { MAX_CHARS, api, isAcceptedFile } from './api'
import Chat from './components/Chat'
import { DropOverlay, PaperFlight, useWindowFileDrop } from './components/Drop'
import Jobs from './components/Jobs'
import { Compare, History, SystemStatus } from './components/Panels'
import PipFigure from './components/PipFigure'
import Result from './components/Result'
import Walker from './components/Walker'

const TABS = [
  ['analyze', 'Analyze'], ['jobs', 'Jobs'], ['history', 'History'],
  ['compare', 'Compare'], ['status', 'Status'],
]

const STAGES = ['Reading your resume…', 'Scoring the match…', 'Rewriting your bullets…']

const TOUR = [
  { anchor: '[data-tour="drop"]', side: 'top-right',
    text: 'Start here: drop your resume - PDF or text. You can drag it anywhere on the page and I’ll catch it.' },
  { anchor: '[data-tour="job"]', side: 'top-right',
    text: 'Paste the job you’re aiming for. I need it for a match score - but not for job hunting.' },
  { anchor: '[data-tour="analyze"]', side: 'top-right',
    text: 'Press Analyze. Two AI agents score your fit and rewrite your weakest bullets.' },
  { anchor: '[data-tour="tabs"]', side: 'top', scrollTop: true,
    text: 'Up top: Jobs finds roles that fit you with live searches on real job boards. History and Compare track your progress.' },
  { anchor: '[data-tour="chat"]', side: 'top', scrollTop: true,
    text: 'And I’m a chatbot too - click me, or "Ask Pip", whenever you want advice.' },
]

const RESULT_POSE = {
  wincing: 'slump', hopeful: 'point', pleased: 'wave', delighted: 'cheer', apologetic: 'shrug',
}

function resultMood(r) {
  if (!r) return 'idle'
  if (r.source === 'local_fallback') return 'apologetic'
  if (r.score >= 85) return 'delighted'
  if (r.score >= 70) return 'pleased'
  if (r.score >= 40) return 'hopeful'
  return 'wincing'
}

function resultLine(r) {
  const mood = resultMood(r)
  const n = (r.missing_skills || []).length
  return {
    wincing: 'Ouch - a big gap. The rewrites below are where we start.',
    hopeful: `Close! ${n} skill${n === 1 ? '' : 's'} short. Let’s close the gap.`,
    pleased: 'Strong match! Tighten those bullets and send it.',
    delighted: 'This one’s ready to go. Nice work!',
    apologetic: 'AI’s offline, so that was a keyword pass only.',
  }[mood]
}

const GREETING = {
  role: 'assistant',
  local: true,
  content: "Hi! I'm **Pip**, your resume coach. Drop your resume on the page, then ask me anything - what to fix first, how to phrase a bullet, or which jobs fit you.",
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** A boolean that switches itself off after `ms`. */
function useFlash() {
  const [on, setOn] = useState(false)
  const timer = useRef(0)
  const flash = useCallback((ms) => {
    clearTimeout(timer.current)
    setOn(true)
    timer.current = setTimeout(() => setOn(false), ms)
  }, [])
  const off = useCallback(() => { clearTimeout(timer.current); setOn(false) }, [])
  return [on, flash, off]
}

function Steps({ resume, job, result }) {
  const steps = [
    ['Resume', resume.trim().length >= 30],
    ['Target job', job.trim().length >= 30],
    ['Analyze', !!result],
  ]
  const current = steps.findIndex(([, done]) => !done)
  return (
    <ol className="steps">
      {steps.map(([label, done], i) => (
        <li key={label} className={done ? 'done' : i === current ? 'current' : ''}>
          <span>{done ? '✓' : i + 1}</span>{label}
        </li>
      ))}
    </ol>
  )
}

export default function App() {
  const [tab, setTab] = useState('analyze')
  const [resume, setResume] = useState('')
  const [resumeFile, setResumeFile] = useState(null)
  const [pasteMode, setPasteMode] = useState(false)
  const [job, setJob] = useState('')
  const [force, setForce] = useState(false)
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState(0)
  const [result, setResult] = useState(null)
  // The resume text a result was computed from. null = loaded from history,
  // where the analysis id itself pins the text.
  const [resultResume, setResultResume] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const [rows, setRows] = useState(null)
  const [filters, setFilters] = useState({ job_title: '', min_score: '', source: '' })
  const [providers, setProviders] = useState(null)
  const [logs, setLogs] = useState(null)
  const [offline, setOffline] = useState(false)

  const [showPip, setShowPip] = useState(() => localStorage.getItem('pip') !== 'off')
  const [pipEvent, setPipEvent] = useState(null)
  const pipEventId = useRef(0)
  const [flight, setFlight] = useState(null)
  const [tourStep, setTourStep] = useState(null)
  const [tourDone, setTourDone] = useState(() => localStorage.getItem('pip-tour') === 'done')

  const [chatOpen, setChatOpen] = useState(false)
  const [chatMessages, setChatMessages] = useState([GREETING])
  const [chatBusy, setChatBusy] = useState(false)

  const [jobsState, setJobsState] = useState({ status: 'idle', data: null, error: '', key: null })

  const [introHint, flashIntro] = useFlash()
  const [resultFresh, flashResult] = useFlash()
  const [errorFresh, flashError] = useFlash()
  const [jobsFresh, flashJobs] = useFlash()
  const [talking, flashTalking] = useFlash()

  const fileInput = useRef(null)

  // --------------------------------------------------------------- loaders
  const loadHistory = useCallback(() => {
    api.history({ limit: 50, ...filters }).then(setRows).catch(() => setRows([]))
  }, [filters])

  // The status poll doubles as a liveness check. It used to swallow its errors,
  // which meant a stopped backend stayed invisible until you clicked something.
  const loadStatus = useCallback(() => {
    api.providers()
      .then((p) => { setProviders(p); setOffline(false) })
      .catch((e) => { if (e.offline) setOffline(true) })
    api.logs(20).then(setLogs).catch(() => {})
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])
  useEffect(() => {
    loadStatus()
    const id = setInterval(loadStatus, 30000)
    return () => clearInterval(id)
  }, [loadStatus])
  useEffect(() => { flashIntro(11000) }, [flashIntro])

  useEffect(() => {
    if (!busy) { setStage(0); return undefined }
    const id = setInterval(() => setStage((s) => Math.min(s + 1, 2)), 3500)
    return () => clearInterval(id)
  }, [busy])

  // Use the analysis id only while it still describes the resume on screen.
  const useAnalysis = !!result && (resultResume === null || resultResume === resume.trim())
  const hasResume = resume.trim().length >= 30 || useAnalysis

  // ------------------------------------------------------------ pip events
  const pip = useCallback((event, ms) => {
    const id = ++pipEventId.current
    setPipEvent(event ? { ...event, id } : null)
    if (event && ms) {
      setTimeout(() => {
        if (pipEventId.current === id) setPipEvent(null)
      }, ms)
    }
  }, [])

  // ---------------------------------------------------------------- analyze
  async function analyze() {
    const r = resume.trim(), j = job.trim()
    if (r.length < 30 || j.length < 30) {
      setError(r.length < 30
        ? 'Drop or paste your resume first.'
        : 'Paste the job description you are aiming for - I need it for a match score.')
      flashError(6000)
      return
    }
    if (r.length > MAX_CHARS || j.length > MAX_CHARS) {
      setError(`Each input is capped at ${MAX_CHARS.toLocaleString()} characters.`)
      flashError(6000)
      return
    }
    setError(''); setNotice(''); setBusy(true); setStage(0); pip(null)
    setTourStep(null)
    try {
      const out = await api.analyze(r, j, { force })
      setResult(out)
      setResultResume(r)
      flashResult(11000)
      loadHistory(); loadStatus()
      setTimeout(() => {
        const el = document.querySelector('[data-pip="score"]')
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 80)
    } catch (e) {
      if (e.offline) setOffline(true)
      setResult(null)
      setError(e.message)
      flashError(7000)
    } finally {
      setBusy(false)
    }
  }

  // ------------------------------------------------------------ file intake
  async function handleFile(file, point) {
    if (!isAcceptedFile(file)) {
      pip({ kind: 'bad-file', name: file?.name }, 5500)
      return
    }
    setTab('analyze')
    setError('')
    setTourStep(null)

    // The paper flies from where it was dropped into Pip's hands.
    const pipEl = document.querySelector('.pip-btn')
    if (point && pipEl && showPip) {
      const r = pipEl.getBoundingClientRect()
      setFlight({ from: point, to: { x: r.left + r.width * 0.55, y: r.top + r.height * 0.6 } })
      pip({ kind: 'catching', name: file.name })
      await sleep(700)
    }

    pip({ kind: 'reading', name: file.name })
    const started = Date.now()
    try {
      const out = await api.extract(file)
      // Let the reading animation actually be seen on a fast extraction.
      const elapsed = Date.now() - started
      if (elapsed < 1500) await sleep(1500 - elapsed)
      setResume(out.text)
      setResumeFile({ name: file.name, chars: out.chars, pages: out.pages })
      setPasteMode(false)
      setResult(null)
      setJobsState({ status: 'idle', data: null, error: '', key: null })
      pip({ kind: 'got', name: file.name, chars: out.chars, pages: out.pages }, 12000)
    } catch (e) {
      pip({ kind: 'read-failed', text: e.message }, 7000)
    }
  }

  const dragging = useWindowFileDrop(handleFile)

  async function loadSample() {
    try {
      const s = await api.sample()
      setResume(s.resume); setJob(s.job); setError(''); setNotice('')
      setResumeFile({ name: 'sample resume', chars: s.resume.length })
      setResult(null)
      setJobsState({ status: 'idle', data: null, error: '', key: null })
      pip({ kind: 'sample' }, 7000)
    } catch (e) { setError(e.message) }
  }

  function clearAll() {
    setResume(''); setJob(''); setResumeFile(null); setResult(null); setError('')
    setPasteMode(false)
    setJobsState({ status: 'idle', data: null, error: '', key: null })
  }

  // ------------------------------------------------------------------ jobs
  const jobsKey = useAnalysis ? `a${result.id}` : resume.trim()

  const fetchJobs = useCallback(async (refresh = false) => {
    setJobsState((s) => ({ ...s, status: 'loading', error: '' }))
    try {
      const data = await api.jobs(useAnalysis
        ? { analysisId: result.id, refresh }
        : { resumeText: resume.trim(), refresh })
      setJobsState({ status: 'done', data, error: '', key: jobsKey })
      flashJobs(9000)
      loadStatus()
      return data
    } catch (e) {
      setJobsState({ status: 'error', data: null, error: e.message, key: null })
      return null
    }
  }, [useAnalysis, result, resume, jobsKey, flashJobs, loadStatus])

  // Opening the Jobs tab with a resume nobody has matched yet starts the match.
  useEffect(() => {
    if (tab === 'jobs' && hasResume && jobsState.status !== 'loading'
        && jobsState.key !== jobsKey) {
      fetchJobs(false)
    }
  }, [tab, hasResume, jobsKey, jobsState.key, jobsState.status, fetchJobs])

  function goJobs() { setTab('jobs'); setChatOpen(false); window.scrollTo({ top: 0, behavior: 'smooth' }) }

  // ------------------------------------------------------------------ chat
  const historyFor = (msgs) => msgs
    .filter((m) => !m.local)
    .map((m) => ({
      role: m.role,
      content: m.kind === 'jobs'
        ? `Matched roles: ${m.roles.map((r) => `${r.title} (${r.match}% fit)`).join(', ')}`
        : m.content,
    }))

  async function sendChat(text) {
    const next = [...chatMessages, { role: 'user', content: text }]
    setChatMessages(next)
    setChatBusy(true)
    try {
      const out = await api.chat(historyFor(next), useAnalysis
        ? { analysisId: result.id }
        : { resumeText: resume.trim() || null })
      setChatMessages((m) => [...m, {
        role: 'assistant',
        content: out.reply,
        meta: out.source === 'local_fallback' ? 'offline answer - AI unavailable' : null,
      }])
      flashTalking(2600)
    } catch (e) {
      if (e.offline) setOffline(true)
      setChatMessages((m) => [...m, { role: 'assistant', local: true, content: `Sorry - ${e.message}` }])
    } finally {
      setChatBusy(false)
    }
  }

  async function findJobsFromChat() {
    setChatMessages((m) => [...m, { role: 'user', content: 'Find jobs for me' }])
    if (!hasResume) {
      setChatMessages((m) => [...m, {
        role: 'assistant', local: true,
        content: 'Drop your resume anywhere on the page first - then I can match you to roles.',
      }])
      return
    }
    setChatBusy(true)
    const data = jobsState.key === jobsKey && jobsState.data ? jobsState.data : await fetchJobs(false)
    setChatBusy(false)
    if (data && data.roles.length) {
      setChatMessages((m) => [...m, {
        role: 'assistant', kind: 'jobs', roles: data.roles.slice(0, 3), location: data.location,
      }])
      flashTalking(2200)
    } else {
      setChatMessages((m) => [...m, {
        role: 'assistant', local: true,
        content: 'I couldn’t match roles just now - try the Jobs tab in a moment.',
      }])
    }
  }

  // ------------------------------------------------------------------ tour
  function startTour() {
    setTab('analyze')
    setChatOpen(false)
    setTourStep(0)
  }
  function endTour() {
    setTourStep(null)
    setTourDone(true)
    localStorage.setItem('pip-tour', 'done')
  }
  useEffect(() => {
    if (tourStep === null) return
    const step = TOUR[tourStep]
    if (step.scrollTop) window.scrollTo({ top: 0, behavior: 'smooth' })
    else {
      const el = document.querySelector(step.anchor)
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [tourStep])

  // --------------------------------------------------------- pip director
  // One place decides where Pip is and what she is doing. Highest priority
  // first, so a drop always interrupts whatever she was up to.
  function pipCue() {
    if (tourStep !== null) {
      const last = tourStep === TOUR.length - 1
      return {
        anchor: TOUR[tourStep].anchor, side: TOUR[tourStep].side,
        mood: 'talking', pose: 'point', speed: 3, talking: true,
        line: TOUR[tourStep].text,
        actions: [
          { label: last ? 'Let’s go!' : 'Next →', primary: true,
            onClick: () => (last ? endTour() : setTourStep(tourStep + 1)) },
          ...(last ? [] : [{ label: 'Skip', onClick: endTour }]),
        ],
      }
    }

    if (dragging) {
      return { anchor: '.drop-target', side: 'top', mood: 'excited', pose: 'catch',
               speed: 4.5, line: 'Ooh! Drop it here - I’ll catch it!' }
    }

    if (pipEvent) {
      const e = pipEvent
      switch (e.kind) {
        case 'catching':
          return { hold: true, mood: 'excited', pose: 'catch', holding: 'paper' }
        case 'reading':
          return { anchor: '[data-tour="drop"]', side: 'top-right', mood: 'reading',
                   pose: 'read', holding: 'paper', speed: 2.5,
                   line: `Reading ${e.name}…` }
        case 'got': {
          const ready = job.trim().length >= 30
          return {
            anchor: '[data-tour="drop"]', side: 'top-right', mood: 'pleased', pose: 'wave',
            holding: 'paper', speed: 2,
            line: `Got it - ${e.chars.toLocaleString()} characters${e.pages ? ` across ${e.pages} page${e.pages > 1 ? 's' : ''}` : ''}! `
              + (ready ? 'Shall I analyze it?' : 'Paste a job post to score it, or I can find jobs that fit.'),
            actions: ready
              ? [{ label: 'Analyze now', primary: true, onClick: analyze }]
              : [{ label: 'Find jobs', primary: true, onClick: goJobs },
                 { label: 'Chat', onClick: () => { pip(null); setChatOpen(true) } }],
          }
        }
        case 'sample':
          return { anchor: '[data-tour="analyze"]', side: 'top-right', mood: 'pleased',
                   pose: 'point', speed: 2.5,
                   line: 'Sample loaded. Press Analyze and watch me work!' }
        case 'bad-file':
          return { mood: 'apologetic', pose: 'shrug', wander: false,
                   line: 'I can only read PDF or TXT files, sorry!' }
        case 'read-failed':
          return { anchor: '[data-tour="drop"]', side: 'top-right', mood: 'apologetic',
                   pose: 'shrug', line: e.text }
        default:
          break
      }
    }

    if (chatOpen) {
      return {
        anchor: '.chat-panel', side: 'top-left', speed: 2.6,
        mood: chatBusy ? 'thinking' : talking ? 'talking' : 'idle',
        pose: chatBusy ? 'ponder' : talking ? 'wave' : 'walk',
        talking,
      }
    }

    if (busy) {
      return { mood: 'thinking', pose: 'ponder', speed: 2, line: STAGES[stage] }
    }

    if (errorFresh && error) {
      return { mood: 'apologetic', pose: 'shrug', line: error }
    }

    if (tab === 'jobs' && jobsState.status === 'loading') {
      return { anchor: '[data-pip="jobs"]', side: 'top-right', mood: 'thinking',
               pose: 'ponder', speed: 2, line: 'Matching you to roles…' }
    }
    if (tab === 'jobs' && jobsFresh && jobsState.data?.roles?.length) {
      const top = jobsState.data.roles[0]
      return { anchor: '.job-card', side: 'top-right', mood: 'pleased', pose: 'wave',
               speed: 2.5,
               line: `Found ${jobsState.data.roles.length} roles - ${top.title} is a ${top.match}% fit!` }
    }

    if (tab === 'analyze' && result) {
      const mood = resultMood(result)
      if (resultFresh) {
        return { anchor: '[data-pip="score"]', side: 'top-right', mood,
                 pose: RESULT_POSE[mood], speed: 2.5, line: resultLine(result) }
      }
      return { mood, pose: RESULT_POSE[mood], wander: true }
    }

    if (!tourDone) {
      return {
        anchor: '[data-tour="drop"]', side: 'top-right', mood: 'idle', pose: 'wave',
        line: 'Hi, I’m Pip! Want a 20-second tour?',
        actions: [
          { label: 'Show me', primary: true, onClick: startTour },
          { label: 'No thanks', onClick: endTour },
        ],
      }
    }

    return {
      mood: 'idle', wander: true,
      line: introHint ? 'Drag your resume anywhere on the page - I’ll catch it!' : null,
    }
  }

  const dotClass = (p) => (!p.configured ? 'off' : p.cooling_down ? 'cool' : 'on')
  const togglePip = () => {
    const next = !showPip
    setShowPip(next)
    localStorage.setItem('pip', next ? 'on' : 'off')
  }

  // ---------------------------------------------------------------- render
  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-in">
          <button type="button" className="brand" onClick={() => setTab('analyze')}>
            <span className="brand-mark"><PipFigure headOnly height={30} /></span>
            <span className="brand-name">ResumeMatch <em>Coach</em></span>
          </button>

          <nav className="tabs" data-tour="tabs" aria-label="Sections">
            {TABS.map(([key, label]) => (
              <button key={key} type="button" className={tab === key ? 'active' : ''}
                      onClick={() => setTab(key)}>
                {label}
                {key === 'jobs' && jobsState.data?.roles?.length > 0 && (
                  <span className="badge">{jobsState.data.roles.length}</span>
                )}
              </button>
            ))}
          </nav>

          <div className="appbar-tools">
            <span className="dots" title="AI providers">
              {(providers || []).map((p) => (
                <span key={p.provider} className={`pdot ${dotClass(p)}`}
                      title={`${p.provider}: ${!p.configured ? 'no key'
                        : p.cooling_down ? `cooling ${p.cooling_seconds_left}s` : 'ready'}`}>
                  <i />
                </span>
              ))}
            </span>
            <button type="button" className="ghost sm" onClick={startTour}>Tour</button>
            <button type="button" className="ghost sm" onClick={togglePip}
                    title={showPip ? 'Hide Pip' : 'Show Pip'}>
              {showPip ? 'Hide Pip' : 'Show Pip'}
            </button>
            <button type="button" className="primary sm" data-tour="chat"
                    onClick={() => { pip(null); setTourStep(null); setChatOpen((o) => !o) }}>
              💬 Ask Pip
            </button>
          </div>
        </div>
      </header>

      <main className="wrap">
        {offline && (
          <div className="banner err offline-banner" role="alert">
            <span>
              <b>Can&rsquo;t reach the backend.</b> The server on port 8000 has
              stopped, so nothing will work until it is running again. Start it
              with <code>.\run.ps1</code> in the project folder.
            </span>
            <button type="button" className="sm" onClick={loadStatus}>Retry</button>
          </div>
        )}
        {notice && !error && <div className="banner info">{notice}</div>}
        {error && !errorFresh && tab === 'analyze' && (
          <div className="banner err"><b>Something went wrong.</b> {error}</div>
        )}

        {tab === 'analyze' && (
          <>
            <section className="intro" data-tour="intro">
              <p className="eyebrow">Multi-agent resume coach</p>
              <h1>Your resume, <span className="accent">matched</span> to the job.</h1>
              <p className="lede">
                Drop your resume, paste the job you want, and Pip&rsquo;s agents will
                score the fit, rewrite your weakest bullets and find roles that suit you.
              </p>
              <Steps resume={resume} job={job} result={result} />
            </section>

            <div className="inputs">
              <div className={`card input-card ${dragging ? 'is-target' : ''}`}
                   data-tour="drop" data-perch>
                <div className="card-head">
                  <span className="card-label"><b>1</b> Your resume</span>
                  <span className="card-tools">
                    {resumeFile && (
                      <span className="chip file" title={resumeFile.name}>
                        📄 {resumeFile.name}
                      </span>
                    )}
                    <button type="button" className="link" onClick={() => fileInput.current.click()}>
                      {resume ? 'Replace' : 'Browse'}
                    </button>
                    <input ref={fileInput} type="file" accept=".pdf,.txt,.md" hidden
                           onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; if (f) handleFile(f, null) }} />
                  </span>
                </div>

                {!resume && !pasteMode ? (
                  <button type="button" className="dropzone" onClick={() => fileInput.current.click()}>
                    <svg viewBox="0 0 48 48" width="40" height="40" fill="none" stroke="currentColor"
                         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="M14 6h14l10 10v24a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z" />
                      <path d="M28 6v10h10" /><path d="M24 34V22" /><path d="M19 27l5-5 5 5" />
                    </svg>
                    <b>Drop your resume here</b>
                    <span>PDF or TXT &middot; or click to browse</span>
                  </button>
                ) : (
                  <>
                    <textarea
                      value={resume}
                      onChange={(e) => setResume(e.target.value)}
                      placeholder="Paste your resume text here."
                      autoFocus={pasteMode && !resume}
                    />
                    <div className={`count ${resume.trim().length > MAX_CHARS ? 'over' : ''}`}>
                      {resume.trim().length.toLocaleString()} / 15,000
                    </div>
                  </>
                )}
                {!resume && !pasteMode && (
                  <button type="button" className="link center" onClick={() => setPasteMode(true)}>
                    or paste the text instead
                  </button>
                )}
              </div>

              <div className="card input-card" data-tour="job" data-perch>
                <div className="card-head">
                  <span className="card-label"><b>2</b> Target job</span>
                  <span className="card-tools">
                    <button type="button" className="link" onClick={loadSample} disabled={busy}>
                      Load sample
                    </button>
                  </span>
                </div>
                <textarea
                  value={job}
                  onChange={(e) => setJob(e.target.value)}
                  placeholder="Paste the job posting you’re aiming for."
                />
                <div className={`count ${job.trim().length > MAX_CHARS ? 'over' : ''}`}>
                  {job.trim().length.toLocaleString()} / 15,000
                </div>
              </div>
            </div>

            <div className="actionbar" data-tour="analyze">
              <span className="card-label"><b>3</b> Analyze</span>
              <label className="chk">
                <input type="checkbox" checked={force} disabled={busy}
                       onChange={(e) => setForce(e.target.checked)} />
                Force re-analyze
              </label>
              <button type="button" className="ghost" onClick={clearAll} disabled={busy}>Clear</button>
              <button type="button" className="ghost" onClick={goJobs} disabled={busy || !hasResume}>
                Find jobs
              </button>
              <button type="button" className="primary go" onClick={analyze} disabled={busy}>
                {busy ? <><span className="spin" />Analyzing…</> : 'Analyze match'}
              </button>
            </div>

            {busy && (
              <div className="card skeleton-card" aria-busy="true">
                <div className="sk-row">
                  <i className="sk ring" />
                  <div className="sk-lines"><i className="sk w60" /><i className="sk w90" /><i className="sk w80" /></div>
                </div>
                <p className="muted-line">{STAGES[stage]} The Analyst scores first, then the Rewriter drafts your bullets.</p>
              </div>
            )}

            {!busy && result && <Result result={result} onFindJobs={goJobs} />}
          </>
        )}

        {tab === 'jobs' && (
          <Jobs state={jobsState} hasResume={hasResume} onFetch={fetchJobs}
                onDropHint={() => setTab('analyze')} />
        )}

        {tab === 'history' && (
          <section className="card panel" data-perch>
            <h2 className="card-title">History</h2>
            <History rows={rows} filters={filters} onFilter={setFilters}
                     onView={async (id) => {
                       try {
                         setResult(await api.analysis(id)); setResultResume(null)
                         setTab('analyze'); flashResult(9000)
                       } catch (e) { setError(e.message) }
                     }}
                     onDelete={async (id) => {
                       try {
                         await api.remove(id)
                         if (result && result.id === id) setResult(null)
                         loadHistory()
                       } catch (e) { setError(e.message) }
                     }} />
          </section>
        )}

        {tab === 'compare' && (
          <section className="card panel" data-perch>
            <h2 className="card-title">Compare two versions</h2>
            <Compare rows={rows} onError={setError} />
          </section>
        )}

        {tab === 'status' && (
          <section className="card panel" data-perch>
            <div className="card-title-row">
              <h2 className="card-title">System status</h2>
              <button type="button" className="ghost sm" onClick={loadStatus}>Refresh</button>
            </div>
            <SystemStatus providers={providers} logs={logs} />
          </section>
        )}

        <footer className="foot">
          <details className="how">
            <summary>How it works</summary>
            <pre>{`Analyst agent  ─┐
Rewriter agent ─┼─> ProviderRouter ─> NVIDIA ─> Cohere ─> OpenRouter ─> HuggingFace
Jobs agent     ─┤                                          │ all fail
Pip (chat)     ─┘                                          ▼
                                             local keyword fallback

Every agent returns validated output. A 429 or 401 moves on immediately; a 5xx
or timeout gets one retry; two failures bench a provider for 60s. Identical
input is served from cache. Job links are live searches on real boards - Pip
never invents a posting.`}</pre>
          </details>
          <p>Runs locally on your machine. Your resume never leaves it except to the AI providers you configured.</p>
        </footer>
      </main>

      <Chat
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        messages={chatMessages}
        busy={chatBusy}
        onSend={sendChat}
        onFindJobs={findJobsFromChat}
        onOpenJobs={goJobs}
        hasResume={hasResume}
        hasAnalysis={useAnalysis}
      />

      <DropOverlay active={dragging} />
      <PaperFlight flight={flight} onDone={() => setFlight(null)} />

      <Walker
        enabled={showPip}
        cue={pipCue()}
        onPipClick={() => { pip(null); setTourStep(null); setChatOpen((o) => !o) }}
      />
    </div>
  )
}
