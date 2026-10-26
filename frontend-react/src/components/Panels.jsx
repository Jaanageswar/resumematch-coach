import { useEffect, useState } from 'react'
import { api, band } from '../api'

/** Recent analyses, with view and delete. */
export function History({ rows, onView, onDelete, onFilter, filters }) {
  if (!rows) return <p className="muted">Loading…</p>

  return (
    <>
      <div className="bar filters">
        <input
          placeholder="Filter by job title"
          value={filters.job_title}
          onChange={(e) => onFilter({ ...filters, job_title: e.target.value })}
        />
        <label className="chk">
          Min score
          <input
            type="number" min="0" max="100" style={{ width: 66 }}
            value={filters.min_score}
            onChange={(e) => onFilter({ ...filters, min_score: e.target.value })}
          />
        </label>
        <select value={filters.source}
                onChange={(e) => onFilter({ ...filters, source: e.target.value })}>
          <option value="">Any source</option>
          <option value="ai">AI</option>
          <option value="local_fallback">Local fallback</option>
        </select>
      </div>

      {rows.length === 0 ? (
        <p className="muted">No analyses match. Run one above.</p>
      ) : (
        <table>
          <thead>
            <tr><th>When</th><th>Resume</th><th>Job</th><th>Score</th>
              <th>Served by</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{(r.created_at || '').slice(5, 16)}</td>
                <td className="t">{r.resume_label}</td>
                <td className="t">{r.job_title}</td>
                <td>
                  <span className="sc" style={{ color: band(r.score) }}>{r.score}</span>
                </td>
                <td>
                  {r.source === 'local_fallback' ? 'local fallback' : r.provider_used || '-'}
                  {r.partial && <b className="amber"> partial</b>}
                </td>
                <td className="actions">
                  <button onClick={() => onView(r.id)}>view</button>
                  <button onClick={() => onDelete(r.id)}>delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

/** Two analyses side by side, with the score delta and the skill diff. */
export function Compare({ rows, onError }) {
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!a || !b || a === b) { setData(null); return }
    api.compare(a, b).then(setData).catch((e) => { setData(null); onError(e.message) })
  }, [a, b, onError])

  if (!rows || rows.length < 2) {
    return <p className="muted">Run at least two analyses to compare them.</p>
  }

  const options = rows.map((r) => (
    <option key={r.id} value={r.id}>
      #{r.id} · {r.score} · {r.resume_label}
    </option>
  ))
  const max = data ? Math.max(...data.trend.map((t) => t.score), 1) : 1

  return (
    <>
      <div className="bar filters">
        <label className="chk">Older
          <select value={a} onChange={(e) => setA(e.target.value)}>
            <option value="">choose…</option>{options}
          </select>
        </label>
        <label className="chk">Newer
          <select value={b} onChange={(e) => setB(e.target.value)}>
            <option value="">choose…</option>{options}
          </select>
        </label>
      </div>

      {a && b && a === b && <p className="muted">Pick two different analyses.</p>}

      {data && (
        <>
          <div className="delta">
            <span style={{ color: band(data.a.score) }}>{data.a.score}</span>
            <small>→</small>
            <span style={{ color: band(data.b.score) }}>{data.b.score}</span>
            <em className={data.score_delta >= 0 ? 'up' : 'down'}>
              {data.score_delta >= 0 ? '▲' : '▼'} {Math.abs(data.score_delta)}
            </em>
          </div>

          <div className="half three">
            <div>
              <div className="subhd">Newly matched</div>
              <div className="pills">
                {data.skills.newly_matched.length
                  ? data.skills.newly_matched.map((s) =>
                      <span key={s} className="pill y">{s}</span>)
                  : <span className="muted">none</span>}
              </div>
            </div>
            <div>
              <div className="subhd">Still missing</div>
              <div className="pills">
                {data.skills.still_missing.length
                  ? data.skills.still_missing.map((s) =>
                      <span key={s} className="pill n">{s}</span>)
                  : <span className="muted">none</span>}
              </div>
            </div>
            <div>
              <div className="subhd">Newly missing</div>
              <div className="pills">
                {data.skills.newly_missing.length
                  ? data.skills.newly_missing.map((s) =>
                      <span key={s} className="pill n">{s}</span>)
                  : <span className="muted">none</span>}
              </div>
            </div>
          </div>

          {data.trend.length > 1 && (
            <>
              <div className="subhd" style={{ marginTop: 22 }}>
                Score over time for this job
              </div>
              <div className="spark">
                {data.trend.map((t) => (
                  <div key={t.id} className="spark-col" title={`#${t.id} — ${t.score}`}>
                    <i style={{ height: `${(t.score / max) * 100}%`,
                                background: band(t.score) }} />
                    <span>{t.score}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </>
  )
}

/** Provider health and the raw attempt log - the failover made visible. */
export function SystemStatus({ providers, logs }) {
  if (!providers) return <p className="muted">Loading…</p>
  return (
    <>
      <table>
        <thead>
          <tr><th>Provider</th><th>Model</th><th>Key</th><th>State</th>
            <th>Attempts</th><th>Success</th><th>Avg</th></tr>
        </thead>
        <tbody>
          {providers.map((p) => (
            <tr key={p.provider}>
              <td className="t"><b>{p.provider}</b></td>
              <td>{p.model || '-'}</td>
              <td>{p.configured ? 'yes' : <span className="rust">missing</span>}</td>
              <td>
                {p.cooling_down
                  ? <span className="amber">cooling {p.cooling_seconds_left}s</span>
                  : <span className="sage">ready</span>}
              </td>
              <td>{p.attempts}</td>
              <td>{p.success_rate == null ? '-' : `${Math.round(p.success_rate * 100)}%`}</td>
              <td>{p.avg_latency_ms == null ? '-' : `${p.avg_latency_ms}ms`}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="subhd" style={{ marginTop: 20 }}>
        Last {(logs || []).length} provider attempts
      </div>
      <table>
        <thead>
          <tr><th>Time</th><th>Provider</th><th>Model</th><th>ms</th><th>Result</th></tr>
        </thead>
        <tbody>
          {(logs || []).map((l) => (
            <tr key={l.id} className={l.success ? '' : 'bad'}>
              <td>{(l.created_at || '').slice(11, 19)}</td>
              <td>{l.provider}</td>
              <td>{l.model}</td>
              <td>{l.latency_ms ?? '-'}</td>
              <td>{l.success ? 'ok' : l.error}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}
