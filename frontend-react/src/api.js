/**
 * Thin API client. Its one real job is turning any failure into a sentence a
 * person can act on - the UI never shows a status code or a stack trace.
 */

/** Thrown when `fetch` itself failed, i.e. the backend is not answering. */
export class OfflineError extends Error {
  constructor() {
    super('Can’t reach the backend on port 8000 - it may have stopped.')
    this.offline = true
  }
}

async function call(path, options) {
  let res
  try {
    res = await fetch(path, options)
  } catch {
    // Tagged rather than a plain Error so the UI can show one persistent
    // "backend is down" banner instead of only failing whatever you clicked.
    throw new OfflineError()
  }

  let body = null
  try {
    body = await res.json()
  } catch {
    /* 204 No Content, or a non-JSON error page */
  }

  if (!res.ok) {
    const d = body && body.detail
    if (typeof d === 'string') throw new Error(d)
    // FastAPI validation errors arrive as a list of {loc, msg, type}
    if (Array.isArray(d) && d[0] && d[0].msg) throw new Error(d[0].msg)
    throw new Error(`The server returned an error (HTTP ${res.status}).`)
  }
  return body
}

const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => call('/health'),
  sample: () => call('/sample'),
  analyze: (resume_text, job_text, extra = {}) =>
    call('/analyze', json({ resume_text, job_text, ...extra })),
  history: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null)
    )
    return call(`/history?${q}`)
  },
  analysis: (id) => call(`/analysis/${id}`),
  remove: (id) => call(`/analysis/${id}`, { method: 'DELETE' }),
  compare: (a, b) => call(`/compare?a=${a}&b=${b}`),
  providers: () => call('/providers/status'),
  logs: (limit = 20) => call(`/providers/logs?limit=${limit}`),
  /**
   * The tailored resume PDF. Fetched rather than linked, so a 422 ("nothing
   * would change") arrives as a sentence instead of navigating the user to a
   * page of raw JSON.
   */
  resumePdf: async (id) => {
    const res = await fetch(`/analysis/${id}/resume.pdf`)
    if (!res.ok) {
      let detail = `The server returned an error (HTTP ${res.status}).`
      try {
        const body = await res.json()
        if (body && typeof body.detail === 'string') detail = body.detail
      } catch { /* not JSON */ }
      throw new Error(detail)
    }
    return {
      blob: await res.blob(),
      filename: (res.headers.get('content-disposition') || '')
        .match(/filename="?([^"]+)"?/)?.[1] || 'resume-tailored.pdf',
      applied: Number(res.headers.get('x-rewrites-applied') || 0),
      skipped: Number(res.headers.get('x-rewrites-skipped') || 0),
    }
  },

  chat: (messages, { analysisId = null, resumeText = null } = {}) =>
    call('/chat', json({
      messages,
      analysis_id: analysisId,
      resume_text: resumeText,
    })),

  jobs: ({ analysisId = null, resumeText = null, refresh = false } = {}) =>
    call('/jobs', json({
      analysis_id: analysisId,
      resume_text: resumeText,
      refresh,
    })),

  extract: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return call('/extract', { method: 'POST', body: fd })
  },
}

export const MAX_CHARS = 15000

export const ACCEPTED_FILES = ['.pdf', '.txt', '.md']
export const isAcceptedFile = (file) =>
  !!file && ACCEPTED_FILES.some((ext) => file.name.toLowerCase().endsWith(ext))

export const band = (s) =>
  s >= 70 ? 'var(--sage)' : s >= 40 ? 'var(--amber)' : 'var(--rust)'

export const verdict = (s) =>
  s >= 85 ? 'Excellent match' : s >= 70 ? 'Strong match'
    : s >= 40 ? 'Partial match' : 'Weak match'
