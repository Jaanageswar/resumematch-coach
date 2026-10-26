import { useState } from 'react'
import { api, band } from '../api'
import ScoreRing from './ScoreRing'

function Metric({ label, value }) {
  return (
    <div className="met">
      <b style={{ color: band(value) }}>{value}</b>
      <span>{label}</span>
      <div className="track">
        <i style={{ width: `${value}%`, background: band(value) }} />
      </div>
    </div>
  )
}

function Pills({ items, kind }) {
  if (!items.length) return <span className="muted">none</span>
  return (
    <div className="pills">
      {items.map((s) => <span key={s} className={`pill ${kind}`}>{s}</span>)}
    </div>
  )
}

function BulletCard({ bullet, index }) {
  return (
    <div className="bul" style={{ animationDelay: `${index * 70}ms` }}>
      <div className="row"><div className="k b">Before</div>
        <div className="v old">{bullet.original}</div></div>
      <div className="row"><div className="k a">After</div>
        <div className="v">{bullet.improved}</div></div>
      <div className="row"><div className="k w">Why</div>
        <div className="v why">{bullet.reason}</div></div>
    </div>
  )
}

function toMarkdown(r) {
  const list = (a) => (a || []).map((s) => `- ${s}`).join('\n')
  return [
    '# ResumeMatch Coach',
    '',
    `**Score: ${r.score}/100**`,
    '',
    `Skills ${r.score_breakdown.skills} | Experience ${r.score_breakdown.experience} | Keywords ${r.score_breakdown.keywords}`,
    '',
    r.summary,
    '',
    '## Matched skills', list(r.matched_skills),
    '',
    '## Missing skills', list(r.missing_skills),
    '',
    '## Rewritten bullets',
    ...(r.rewritten_bullets || []).map(
      (b) => `\n**Before:** ${b.original}\n\n**After:** ${b.improved}\n\n*${b.reason}*`
    ),
    '',
    '## Suggestions',
    (r.suggestions || []).map((s, i) => `${i + 1}. ${s}`).join('\n'),
    '',
    '---',
    `Analyzed by ${r.source === 'local_fallback'
      ? 'local keyword analyzer' : `${r.provider_used} / ${r.model_used}`}`,
  ].join('\n')
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

export default function Result({ result, onFindJobs }) {
  const [copied, setCopied] = useState(false)
  const [pdf, setPdf] = useState({ busy: false, note: '', error: '' })
  const r = result
  const bullets = r.rewritten_bullets || []

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(bullets.map((b) => `• ${b.improved}`).join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  const downloadPdf = async () => {
    setPdf({ busy: true, note: '', error: '' })
    try {
      const out = await api.resumePdf(r.id)
      const url = URL.createObjectURL(out.blob)
      const a = document.createElement('a')
      a.href = url
      a.download = out.filename
      a.click()
      URL.revokeObjectURL(url)
      setPdf({
        busy: false,
        error: '',
        // Say plainly how much changed, including anything we could not place.
        note: `Downloaded ${out.filename} - ${out.applied} bullet${
          out.applied === 1 ? '' : 's'} rewritten${
          out.skipped ? `, ${out.skipped} could not be matched and were left as-is` : ''}.`,
      })
    } catch (e) {
      setPdf({ busy: false, note: '', error: e.message })
    }
  }

  const missing = r.missing_skills || []
  const matched = r.matched_skills || []

  return (
    <section className="result">
      {r.source === 'local_fallback' && (
        <div className="banner warn">
          <b>AI providers unavailable.</b> This is a basic keyword comparison with
          no rewritten bullets. The Status tab shows what failed.
        </div>
      )}
      {r.partial && r.source !== 'local_fallback' && (
        <div className="banner warn">
          <b>Partial result.</b> Scoring succeeded but the Rewriter agent failed,
          so bullet rewrites are missing.
        </div>
      )}

      <div className="card score-card" data-pip="score">
        <div className="hero">
          <ScoreRing score={r.score} />
          <div className="hero-copy">
            <div className="mets">
              <Metric label="skills" value={r.score_breakdown.skills} />
              <Metric label="experience" value={r.score_breakdown.experience} />
              <Metric label="keywords" value={r.score_breakdown.keywords} />
            </div>
            {r.summary && <p className="summary">{r.summary}</p>}
            <div className="meta">
              {[
                r.source === 'local_fallback'
                  ? 'local keyword analyzer'
                  : `${r.provider_used} / ${r.model_used}`,
                r.cached ? 'cached' : 'fresh',
                `${(r.latency_ms || 0).toLocaleString()} ms`,
                r.source === 'local_fallback' ? 'no AI' : 'analyst + rewriter',
              ].join('   ·   ')}
            </div>
          </div>
        </div>
      </div>

      <div className="two-col">
        <div className="card" data-perch>
          <h2 className="card-title">Skills</h2>
          <div className="subhd">Matched · {matched.length}</div>
          <Pills items={matched} kind="y" />
          <div className="subhd" style={{ marginTop: 18 }}>Missing · {missing.length}</div>
          <Pills items={missing} kind="n" />
        </div>

        <div className="card" data-perch>
          <h2 className="card-title">What to do next</h2>
          <ol className="sug">
            {(r.suggestions || []).map((s, i) => <li key={i}>{s}</li>)}
          </ol>
        </div>
      </div>

      <div className="card" data-perch>
        <div className="card-title-row">
          <h2 className="card-title">Rewritten bullets</h2>
          {bullets.length > 0 && <span className="chip">{bullets.length} improved</span>}
        </div>
        {bullets.length ? (
          <>
            {bullets.map((b, i) => <BulletCard key={i} bullet={b} index={i} />)}
            <div className="bar">
              <button className="primary" onClick={downloadPdf} disabled={pdf.busy || !r.id}>
                {pdf.busy
                  ? <><span className="spin" />Building PDF…</>
                  : 'Download tailored resume (.pdf)'}
              </button>
              <button onClick={copy}>{copied ? 'Copied ✓' : 'Copy improved bullets'}</button>
              <button onClick={() => download('resumematch.md', toMarkdown(r), 'text/markdown')}>
                .md
              </button>
              <button onClick={() => download('resumematch.json',
                JSON.stringify(r, null, 2), 'application/json')}>
                .json
              </button>
            </div>
            {pdf.note && <p className="pdf-note">{pdf.note}</p>}
            {pdf.error && <div className="banner err">{pdf.error}</div>}
            <p className="muted-line">
              The PDF is your own resume with these bullets swapped in - every other
              line is left exactly as you wrote it. Replace any [bracketed] figure
              with a real number before you send it.
            </p>
          </>
        ) : (
          <p className="muted-line">No rewrites available for this result.</p>
        )}
      </div>

      <div className="card jobs-teaser" data-perch>
        <div>
          <h2 className="card-title">Jobs that fit this resume</h2>
          <p className="muted-line">
            Pip matches you to roles and opens live searches on LinkedIn, Indeed,
            Naukri and Google Jobs - real listings only.
          </p>
        </div>
        <button className="primary" onClick={onFindJobs}>Find matching jobs →</button>
      </div>
    </section>
  )
}
