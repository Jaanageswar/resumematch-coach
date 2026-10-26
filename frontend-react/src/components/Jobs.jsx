import { band } from '../api'

function SkeletonCard() {
  return (
    <div className="job-card skeleton" aria-hidden="true">
      <i className="sk w60" /><i className="sk w30" /><i className="sk w90" /><i className="sk w80" />
    </div>
  )
}

/**
 * Roles that fit the resume, each with live searches on real job boards.
 *
 * The honesty line under the header is not decoration: Pip matches *roles*,
 * never postings, and the user should know that every link is a live search.
 */
export default function Jobs({ state, hasResume, onFetch, onDropHint }) {
  if (!hasResume) {
    return (
      <div className="empty-state" data-perch>
        <h3>Drop your resume and Pip will find roles that fit</h3>
        <p>
          You do not need a job description for this. Drag a PDF or text file
          anywhere on the page, or paste it on the Analyze tab.
        </p>
        <button type="button" className="primary" onClick={onDropHint}>Go to Analyze</button>
      </div>
    )
  }

  const { status, data, error } = state

  return (
    <section className="jobs" data-pip="jobs">
      <div className="section-head">
        <div>
          <h2>Roles that fit your resume</h2>
          <p className="muted-line">
            Pip matches <b>roles</b>, never postings. Each link opens a live search
            on that board, so every result you see is a real, current listing.
          </p>
        </div>
        <div className="section-actions">
          {data?.location && <span className="chip loc">📍 {data.location}</span>}
          <button type="button" onClick={() => onFetch(true)} disabled={status === 'loading'}>
            {status === 'loading' ? 'Matching…' : data ? 'Refresh' : 'Find roles'}
          </button>
        </div>
      </div>

      {status === 'error' && <div className="banner err">{error}</div>}

      {status === 'loading' && (
        <div className="job-grid"><SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard /></div>
      )}

      {status !== 'loading' && data && (
        <>
          {data.source === 'local_fallback' && (
            <div className="banner warn">
              <b>AI providers unavailable.</b> These roles come from a keyword match
              against common role profiles, so treat the fit scores as rough.
            </div>
          )}
          <div className="job-grid">
            {data.roles.map((r, i) => (
              <article key={r.title} className="job-card" data-perch
                       style={{ animationDelay: `${i * 60}ms` }}>
                <div className="job-top">
                  <div>
                    <h3>{r.title}</h3>
                    {r.seniority && <span className="chip">{r.seniority}</span>}
                  </div>
                  <div className="job-match" style={{ color: band(r.match) }}>
                    <b>{r.match}%</b><span>fit</span>
                  </div>
                </div>
                <div className="job-bar">
                  <i style={{ width: `${r.match}%`, background: band(r.match) }} />
                </div>
                {r.why && <p className="job-why">{r.why}</p>}
                {r.keywords?.length > 0 && (
                  <div className="pills">
                    {r.keywords.map((k) => <span key={k} className="pill neutral">{k}</span>)}
                  </div>
                )}
                <div className="job-links">
                  {r.links.map((l) => (
                    <a key={l.board} href={l.url} target="_blank" rel="noopener noreferrer">
                      {l.board} <span aria-hidden="true">↗</span>
                    </a>
                  ))}
                </div>
              </article>
            ))}
          </div>
          <p className="meta">
            {data.source === 'local_fallback'
              ? 'local keyword matcher'
              : `${data.provider_used} / ${data.model_used}`}
            {'   ·   '}{data.cached ? 'cached' : 'fresh'}
            {'   ·   '}{(data.latency_ms || 0).toLocaleString()} ms
          </p>
        </>
      )}

      {status === 'idle' && !data && (
        <div className="empty-state slim">
          <p>Ready when you are.</p>
          <button type="button" className="primary" onClick={() => onFetch(false)}>
            Find roles for my resume
          </button>
        </div>
      )}
    </section>
  )
}
