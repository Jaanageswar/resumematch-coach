# ResumeMatch Coach — Build Phases

**Companion to [DESIGN.md](./DESIGN.md)** · Created 2026-10-23
**Status: ALL PHASES COMPLETE.** Scope changed mid-build at the user's request:
the React/Vite frontend was replaced with a single static HTML page served by
FastAPI — one process, one port, no build step, no Node. The Coach survived the
simplification.
**Platform:** Windows 11 · Python 3.13.5 · Node 22.19.0

This is the working tracker. Every phase has **deliverables**, a **checklist**, and an
**exit test** that must pass before the next phase starts. Tick boxes as we go.

---

## Progress at a glance

| Phase | Name | Est. | Status |
|:--:|---|:--:|:--:|
| 0 | Scaffold & dependencies | 5 min | ☑ **Done** |

> **Already done ahead of Phase 0** (while handling the keys): `.gitignore`, `.env`,
> `.env.example`, and `scripts/check_providers.py` — which has been **run against the
> real keys**. Result: Cohere, NVIDIA and OpenRouter PASS; Hugging Face returns 403
> because its token is repo-scoped and lacks the "Inference Providers" permission.
> Verified model IDs are already written into `.env`. See DESIGN.md §3.
| 1 | Config & database | 6 min | ☑ **Done** |
| 2 | Providers & failover router | 15 min | ☑ **Done** |
| 3 | Agents, parsing & orchestrator | 14 min | ☑ **Done** |
| 4 | FastAPI routes | 10 min | ☑ **Done** |
| 5 | Single-page UI (replaced React/Vite) | 20 min | ☑ **Done** |
| 5b | **The Coach** — reactive 2D face | 10 min | ☑ **Done** |
| 6 | History, Compare & Status (one page) | 18 min | ☑ **Done** |
| 7 | Tests | 8 min | ☑ **Done** |
| 8 | README & final verification | 7 min | ☑ **Done** |

**Legend:** ☐ not started · ◐ in progress · ☑ done · ⚠ done with a noted issue

---

## Phase 0 — Scaffold & dependencies · 5 min

Create the skeleton so nothing later has to stop and make a folder.

### Deliverables
- Full directory tree from DESIGN.md §9 (empty files where appropriate)
- `requirements.txt`, `.env.example`, `.gitignore`
- `sample_data/resume.txt` and `sample_data/job_description.txt`
- Python virtual environment with deps installed

### Checklist
- [ ] Create directory tree: `backend/{agents,providers,fallback}`, `frontend`, `scripts`, `sample_data`, `tests`, `data`
- [ ] Add `__init__.py` to every Python package directory
- [ ] Write `requirements.txt` — `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `python-dotenv`, `pypdf`, `python-multipart`, `pytest`
- [ ] Write `.gitignore` — `.env`, `data/`, `*.db`, `__pycache__/`, `.venv/`, `node_modules/`, `dist/`
- [ ] Write `.env.example` with every variable from DESIGN.md §14, commented, **all values blank**
- [ ] Write a realistic `sample_data/resume.txt` (mid-level backend engineer, ~400 words)
- [ ] Write a matching `sample_data/job_description.txt` with a deliberate skills gap — so the demo always produces interesting missing-skills output
- [ ] `python -m venv .venv` and install deps

### Exit test
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt    # clean, no errors
.\.venv\Scripts\python.exe -c "import fastapi, httpx, pypdf, pydantic; print('ok')"
```

### Risk
Low. If a wheel fails to build on 3.13, swap the package — every chosen dep is
pure-Python specifically to avoid this.

---

## Phase 1 — Config & database · 6 min

### Deliverables
- `backend/config.py` — env loading, typed settings, provider-order parsing
- `backend/db.py` — connection management, schema DDL, query helpers
- `backend/cache.py` — input hashing and cache read/write

### Checklist
- [ ] `config.py`: load `.env` via `python-dotenv`; expose a `Settings` object with
      `PROVIDER_ORDER` (list), per-provider key + model, `REQUEST_TIMEOUT_SECONDS`,
      `MAX_INPUT_CHARS`, breaker thresholds
- [ ] `config.py`: `__repr__` that **masks every key** — so an accidental print is safe
- [ ] `db.py`: `get_conn()` with `check_same_thread=False`, a module-level lock,
      `row_factory = sqlite3.Row`, and `PRAGMA foreign_keys = ON`
- [ ] `db.py`: `init_db()` running the exact DDL from DESIGN.md §7.2 (all 4 tables + 5 indexes)
- [ ] `db.py`: helpers — `insert_resume`, `insert_job`, `insert_analysis`,
      `get_analysis`, `list_analyses`, `delete_analysis`, `log_provider_attempt`,
      `provider_stats` — **parameterised queries only**
- [ ] `cache.py`: `normalise(text)` (strip, collapse whitespace, lowercase) and
      `input_hash(resume, jd)` → SHA-256 with the `v1` suffix
- [ ] `cache.py`: `get_cached(hash)` / cache write via `UPSERT` on `input_hash`

### Exit test
```powershell
.\.venv\Scripts\python.exe -c "from backend.db import init_db, get_conn; init_db(); print([r[0] for r in get_conn().execute(\"select name from sqlite_master where type='table'\")])"
# expect: ['resumes', 'job_descriptions', 'analyses', 'provider_logs']
```
Plus: `data/resumematch.db` exists; `init_db()` run twice does not error (idempotent).

---

## Phase 2 — Providers & failover router · 15 min

**The core phase.** This is the part that makes the project worth presenting.

### Deliverables
- `backend/providers/base.py` — `Provider` ABC
- Four provider implementations
- `backend/providers/router.py` — ordered failover + circuit breaker + logging
- `scripts/check_providers.py` — live PASS/FAIL table

### Checklist
- [ ] `base.py`: `Provider` ABC with `name`, `model`, `supports_json_mode`,
      `is_configured()`, `generate(system_prompt, user_prompt) -> str`
- [ ] `base.py`: a shared `_post_openai_compatible()` helper — HF, OpenRouter and NVIDIA
      differ only in URL, key and model, so only Cohere needs bespoke code
- [ ] `base.py`: exception taxonomy — `RateLimited`, `PaymentRequired`, `AuthFailed`,
      `ModelNotFound`, `ServerError`, `Timeout`, `EmptyResponse`, mapped from status codes
- [ ] `huggingface.py` — `https://router.huggingface.co/v1/chat/completions`,
      `supports_json_mode = True`, sends `response_format: {"type": "json_object"}`
- [ ] `openrouter.py` — `https://openrouter.ai/api/v1/chat/completions`, plus
      `OPENROUTER_FALLBACK_MODELS` tried in order **within** the provider before failing out
- [ ] `nvidia.py` — `https://integrate.api.nvidia.com/v1/chat/completions`
- [ ] `cohere.py` — `https://api.cohere.com/v2/chat`. **Do NOT index `content[0]`** —
      verified live, `command-a-plus-05-2026` returns
      `[{type:"thinking"},{type:"text"}]`, so `[0]` is the chain of thought.
      Filter by `type == "text"` (DESIGN.md §3, finding 1)
- [ ] All OpenAI-compatible providers: treat `message.content` of `null`/`""` as
      `EmptyResponse` → fail over. **Verified real**, not theoretical: NVIDIA
      reasoning models return HTTP 200 with `content: null` when the token budget
      goes to hidden reasoning (DESIGN.md §3, finding 2)
- [ ] Send `max_tokens = MAX_OUTPUT_TOKENS` (2000). 600 produced empty responses
- [ ] Ignore `reasoning_content` entirely — it is chain of thought, never the answer
- [ ] Support `*_FALLBACK_MODELS` **within** each provider, tried before failing over
      to the next provider
- [ ] `router.py`: `CircuitBreaker` — `{provider: (failures, cooling_until)}`,
      threshold 2, cooldown 60s, success resets
- [ ] `router.py`: `ProviderRouter.generate()` implementing the DESIGN.md §6.2 loop —
      skip unconfigured, skip cooling, **max 1 retry**, honour the §6.3 error table
- [ ] `router.py`: every attempt writes a `provider_logs` row (provider, model, success,
      latency_ms, error) — including skips
- [ ] `router.py`: `status()` for the `/providers/status` endpoint
- [ ] `scripts/check_providers.py`: send each configured provider a trivial prompt
      ("reply with the word OK"), print a table of
      provider / model / result / latency / error, and **never print a key**

### Exit test
```powershell
.\.venv\Scripts\python.exe scripts\check_providers.py
```
With no keys yet, the expected (and correct) output is a clean table showing
`no key — skipped` for all four, with **no crash and no traceback**. That is itself a
meaningful result: it proves the skip path works.

### Risk
Medium — the highest-risk phase. Mitigations: provider classes are unit-testable with
mocked `httpx`, the retry cap protects the free quota, and `check_providers.py` turns a
stale model ID into a 10-second diagnosis.

---

## Phase 3 — Agents, parsing & orchestrator · 14 min

### Deliverables
- `backend/models.py` — all Pydantic schemas
- `backend/agents/{base,parsing,analyst,rewriter,orchestrator}.py`
- `backend/fallback/local_analyzer.py`

### Checklist
- [ ] `models.py`: `AnalystOutput`, `RewriterOutput`, `RewrittenBullet`, `ScoreBreakdown`,
      `AnalysisResult`, `AnalyzeRequest`, `HistoryItem`, `CompareResult`, `ProviderStatus`
- [ ] `models.py`: validators — scores are ints clamped 0–100, `rewritten_bullets`
      capped at 5, skill lists de-duplicated
- [ ] `parsing.py`: the 5-stage ladder from DESIGN.md §5.5 — raw → de-fence →
      **balanced-brace extraction** → light repair → stricter retry
- [ ] `parsing.py`: `extract_json(text) -> dict` raising `ParseError`; brace counting,
      **not** a regex (nested objects break regexes)
- [ ] `base.py`: `Agent` ABC — assemble prompt, call router, parse, validate, one
      stricter retry on validation failure, then `AgentError`
- [ ] `analyst.py`: system prompt per DESIGN.md §5.2 — evidence-only, never invent a
      skill, JSON only, no fences
- [ ] `rewriter.py`: system prompt per §5.3 — max 5 bullets, `original` verbatim,
      **never fabricate numbers/employers/credentials**
- [ ] `local_analyzer.py`: tokenise → stopwords → unigrams **+ bigrams** → top ~40 JD
      terms → intersect a ~150-term skill vocabulary → score → templated summary →
      `source="local_fallback"`
- [ ] `local_analyzer.py`: fully deterministic — no `random`, no dict-order dependence
- [ ] `orchestrator.py`: cache check → Analyst → Rewriter → merge → persist, with the
      two degradation paths (`partial=true`; `source="local_fallback"`)
- [ ] `orchestrator.py`: Analyst failure falls through to `local_analyzer`, never raises

### Exit test
```powershell
.\.venv\Scripts\python.exe -c "from backend.agents.orchestrator import analyze; from pathlib import Path; r=analyze(Path('sample_data/resume.txt').read_text(), Path('sample_data/job_description.txt').read_text()); print(r.source, r.score, len(r.missing_skills))"
```
With no keys: expect `local_fallback <score> <n>` with a plausible score and a non-empty
missing-skills list. Run it twice — the second call must report `cached=true`.

---

## Phase 4 — FastAPI routes · 10 min

### Deliverables
- `backend/main.py` — app, all 8 routes, CORS, startup hook, global error handler
- `backend/extract.py` — PDF/TXT text extraction

### Checklist
- [ ] `main.py`: app with `title`/`version`; `init_db()` on the startup hook
- [ ] CORS allowing `http://localhost:5173` and `http://127.0.0.1:5173`
- [ ] `GET /health` → `{status, db, providers_configured, version}`
- [ ] `POST /analyze` → orchestrator; validate non-empty and ≤ 15,000 chars (422 with a
      readable message)
- [ ] `POST /extract` → multipart; `pypdf` for PDF, decode for TXT; 5 MB cap; reject
      other types; **in-memory only, nothing written to disk**
- [ ] `GET /history` with `limit`, `job_title`, `min_score`, `source` filters
- [ ] `GET /analysis/{id}` → 404 when missing
- [ ] `GET /compare?a=&b=` → delta, `newly_matched`, `still_missing`, `newly_missing`
- [ ] `GET /providers/status` → router status + success rate and avg latency from
      `provider_logs`; **booleans only, never a key**
- [ ] `DELETE /analysis/{id}` → 204, or 404 when missing
- [ ] Global exception handler → `{detail: "..."}` JSON; **no HTML traceback ever**

### Exit test
```powershell
# terminal 1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
# terminal 2
curl http://localhost:8000/health
curl -X POST http://localhost:8000/analyze -H "Content-Type: application/json" -d "{\"resume_text\":\"Python FastAPI engineer\",\"job_text\":\"Need Python and Kubernetes\"}"
```
Both return JSON; `/docs` renders all 8 routes.

---

## Phase 5 — React scaffold & Analyze tab · 20 min

### Deliverables
- Vite + React project with the proxy wired
- Design tokens and base stylesheet
- `App.jsx` shell with tabs, header and provider dots
- A fully working Analyze tab

### Checklist
- [ ] `npm create vite@latest frontend -- --template react`, then `npm install`
- [ ] Pin `react@18.3.1`, `react-dom@18.3.1`; add `recharts`
- [ ] `vite.config.js`: proxy `/api` → `http://localhost:8000`
- [ ] `index.html`: title, Google Fonts (Playfair Display, Inter, JetBrains Mono)
- [ ] `styles/tokens.css`: every token from DESIGN.md §8.3
- [ ] `styles/app.css`: page shell, card, tab bar, hairline rules, pill, table
- [ ] `api.js`: `fetch` wrapper that normalises errors into
      `{message, detail}` — never surfaces a raw stack
- [ ] `hooks/useAsync.js`: `{data, loading, error, run}`
- [ ] `App.jsx`: header with wordmark + tab bar + provider dots polling
      `/providers/status` every 30s
- [ ] `App.jsx`: "How it works" expander with the compact pipeline diagram
- [ ] `components/ScoreRing.jsx`: SVG arc, `stroke-dasharray` animation from 0,
      colour band (<40 rust / 40–69 amber / 70+ sage), respects `prefers-reduced-motion`
- [ ] `components/MetricBar.jsx`, `SkillPills.jsx`, `BulletDiff.jsx`, `Banner.jsx`,
      `CharCount.jsx`, `ErrorCard.jsx`, `EmptyState.jsx`, `FileDrop.jsx`
- [ ] `tabs/AnalyzeTab.jsx`: two textareas, drag-drop upload → `POST /extract`,
      "Load sample", "Force re-analyze", staged spinner copy, full results render
- [ ] Download result as `.md` and `.json`
- [ ] Amber banner on `source="local_fallback"`; amber note on `partial=true`

### Exit test
```powershell
# terminal 1: uvicorn on :8000   |   terminal 2:
cd frontend; npm run dev
```
Open `http://localhost:5173`, click **Load sample**, click **Analyze**: the score ring
animates, metrics and pills render, the amber fallback banner shows (no keys yet), and
the browser console is clean.

### Risk
Medium — the longest phase. Mitigation: build the Analyze tab completely before touching
the other three, so there is always a demonstrable app.

---

## Phase 5b — The Coach · 10 min

The reactive 2D face. Fully self-contained: nothing else depends on it, so it can be
deferred to last or dropped without touching another file. Spec in **DESIGN.md §8.7**.

### Deliverables
- `components/CoachFace.jsx` — inline SVG, 7 moods
- `components/CoachBubble.jsx` — one deterministic line per mood
- `styles/coach.css` — keyframes and mood colours
- `moodFor()` wired into `AnalyzeTab`, the Analyze button, and the empty state

### Checklist
- [ ] `moodFor(state, score)` as a **pure function** — the single source of truth for
      face, colour and bubble line. Put it in `CoachFace.jsx` and export it.
- [ ] Head as a *slightly irregular* `<path>`, not a `<circle>` — the hand-drawn wobble
      is what makes it read as marginalia. `--ink` stroke 2.5px, `stroke-linecap: round`,
      `--card` fill
- [ ] Mouth: one `<path>` per mood, **identical command structure**
      (`M 34 60 Q 48 {y} 62 60`) so `transition: d 320ms` interpolates
- [ ] Eyes: cross-fade `opacity` between `<g>` groups (not path morphing — works everywhere)
- [ ] All 7 moods: `resting`, `thinking`, `wincing`, `hopeful`, `pleased`, `delighted`,
      `apologetic`
- [ ] Blink loop: `scaleY(0.1)` 90ms on ~5s, **staggered per eye** so it is not
      mechanically symmetrical
- [ ] `thinking`: half-lidded eyes, pupils drift L↔R, 3 dots orbit above the head
- [ ] `wincing`: one sweat bead drips **once**
- [ ] `delighted`: sparkle eyes + 5 confetti ticks, **one-shot**
- [ ] One-shot effects unmount on `onAnimationEnd` — no forever-loops, no leaked timers
- [ ] Entry animation: scale `0.7` → `1` with overshoot, `420ms cubic-bezier(.34,1.56,.64,1)`
- [ ] `CoachBubble.jsx`: the exact line table from DESIGN.md §8.7 —
      **deterministic, no random rotation**
- [ ] `hopeful` line interpolates the real count: `"Close. {n} skills short."`
- [ ] `thinking` line follows the pipeline stage: "Reading your resume…" →
      "Rewriting your bullets…"
- [ ] Mood colour reads the **same band tokens as `ScoreRing`** — the two can never disagree
- [ ] Wire in at all 4 placements: 28px in the Analyze button, 72px in the results
      header, 40px in the History detail panel, 56px in the empty state
- [ ] `role="img"` + `aria-label` naming mood **and** verdict in words:
      *"Coach looks pleased. Strong match, 78 out of 100."*
- [ ] Score number, verdict word and colour band still render as text beside it — the
      face is **never the only signal**
- [ ] `@media (prefers-reduced-motion: reduce)`: no bob, no blink, no confetti, no drip —
      the correct static expression renders immediately
- [ ] Header toggle to hide the Coach, persisted to `localStorage`
- [ ] Never exceeds 72px anywhere

### Exit test
Walk all 7 moods without needing 7 real analyses — temporarily render
`<CoachFace state="result" score={s} />` for `s` in `[0, 30, 55, 78, 95]` plus
`state="thinking"` and `state="error"` in a scratch row, confirm each is distinct and
legible at 72px, then delete the scratch row. Then:
- [ ] Real run: face shows `thinking` in the button during analysis, then the correct
      score mood with its bubble
- [ ] With no keys, a fallback result shows `apologetic`, not a score mood
- [ ] Toggle OS "reduce motion" → animations stop, expressions still correct
- [ ] Header toggle off → clean layout, no gaps where the face was

### Risk
Low, and fully contained. The one thing to watch: CSS `d` interpolation is unsupported
in a few older engines, where the mouth will **snap** between shapes instead of morphing.
That degrades to "still correct, slightly less smooth" — acceptable, and noted rather
than worked around.

---

## Phase 6 — History, Compare & Status tabs · 18 min

### Deliverables
Three remaining tabs, fully wired.

### Checklist
- [ ] `tabs/HistoryTab.jsx`: filter row (job title, min score, source) → `GET /history`
- [ ] History table: date, resume, job, score chip, provider, `cached`/`partial`/
      `fallback` badges
- [ ] Row click → detail panel below via `GET /analysis/{id}`
- [ ] Delete with inline confirm → `DELETE /analysis/{id}` → list refresh
- [ ] Empty state for a first run with no history
- [ ] `tabs/CompareTab.jsx`: two selects (A = older, B = newer), guard against A === B
- [ ] Large delta readout — sage ↑ / rust ↓ / neutral for 0
- [ ] Three-column skills diff: newly matched / still missing / newly missing
- [ ] Recharts line of score over time for the selected job
- [ ] `tabs/StatusTab.jsx`: one `ProviderCard` per provider — name, model,
      configured, cooling-down countdown, success rate, avg latency
- [ ] Latest 20 `provider_logs` rows, colour-coded by outcome, mono font
- [ ] Manual refresh button + 15s auto-refresh on the Status tab

### Exit test
Run 3 analyses (two of the same resume against one job, one different). Then:
History lists all 3 and filters correctly; Compare shows a real delta and a 2-point
chart; Status shows all four providers with accurate configured/cooling state; deleting
one analysis removes it from History and from the Compare selects.

---

## Phase 7 — Tests · 8 min

### Deliverables
`tests/test_router.py`, `tests/test_parsing.py`, `tests/test_local.py` — **zero network,
zero API quota.**

### Checklist
- [ ] `conftest.py`: in-memory SQLite fixture, fake-provider factory
- [ ] `test_router.py`: first provider succeeds → exactly one attempt, no fallthrough
- [ ] `test_router.py`: provider #1 raises 429 → #2 used; **order respected**
- [ ] `test_router.py`: unconfigured providers skipped entirely
- [ ] `test_router.py`: 2 consecutive failures → cooling; skipped on the next call
- [ ] `test_router.py`: a success resets the failure counter
- [ ] `test_router.py`: all providers fail → `source="local_fallback"`
- [ ] `test_router.py`: every attempt wrote a `provider_logs` row
- [ ] `test_parsing.py`: raw JSON · fenced ```` ```json ```` · prose around it ·
      **nested braces** · trailing comma · smart quotes · wrong shape → Pydantic error ·
      garbage → `ParseError`
- [ ] `test_local.py`: identical input twice is byte-identical; score within 0–100;
      a known pair lands in the expected band
- [ ] Assert no test makes a real HTTP call (`monkeypatch` `httpx` at module level)

### Exit test
```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
All green. Any failure is fixed here, not deferred.

---

## Phase 8 — README & final verification · 7 min

### Deliverables
- `README.md` per the original brief §9
- `run.ps1` (Windows) and `run.sh` (POSIX)
- A clean-start verification run

### Checklist
- [ ] README: overview, screenshot placeholder, feature list
- [ ] README: the ASCII architecture diagram from DESIGN.md §4
- [ ] README: setup — venv, `pip install`, `npm install`, copy `.env.example` → `.env`
- [ ] README: how to get each free key, with links —
      [Hugging Face](https://huggingface.co/settings/tokens) (needs the **Inference
      Providers** permission), [OpenRouter](https://openrouter.ai/keys),
      [NVIDIA](https://build.nvidia.com),
      [Cohere](https://dashboard.cohere.com/api-keys)
- [ ] README: how to run (both terminals, or `run.ps1`)
- [ ] README: **failover design explained** — order, error taxonomy, circuit breaker,
      local fallback, caching. This is the section an evaluator actually reads.
- [ ] README: agent pipeline explanation
- [ ] README: known limitations, verbatim from DESIGN.md §15
- [ ] `run.ps1`: start uvicorn and `npm run dev` in parallel, print both URLs
- [ ] `run.sh`: POSIX equivalent with trap-based cleanup
- [ ] **Final verification:** fresh shell → `run.ps1` → `/health` → `/analyze` with
      sample data → open all 4 tabs → confirm no key appears in any log or response
- [ ] **Secret leak check** (DESIGN.md §17) — confirm no file git tracks contains a
      key. It reads the keys from `.env` at run time, so this published file never
      contains any part of a real key (an earlier version hard-coded key prefixes
      here, which is exactly the leak it was meant to catch):
      ```powershell
      $keys = Get-Content .env |
        Where-Object { $_ -match '^\w+_(API_KEY|TOKEN)=.+' } |
        ForEach-Object { ($_ -split '=', 2)[1].Trim() }
      git ls-files | ForEach-Object {
        $text = Get-Content $_ -Raw -ErrorAction SilentlyContinue
        foreach ($k in $keys) { if ($text -and $text.Contains($k)) { "LEAK: $_" } }
      }
      ```
- [ ] Report exactly what works, what degraded, and why

### Exit test
A single `run.ps1` brings up both servers; the browser flow works end to end; `pytest -q`
is green; `grep` for each key name finds them **only** in `.env` and `.env.example`.

---

## Phase 9 (optional) — When the keys arrive

No code changes. Four steps:

- [ ] Paste keys into `.env` (never into code, never into git)
- [ ] `python scripts/check_providers.py` → confirm each model ID resolves
- [ ] Fix any `404 model not found` by updating **only** the `*_MODEL` env value
- [ ] Restart the backend; re-run `/analyze` → expect `source="ai"` and the real
      provider name in the results caption

If a model ID is stale, the check script names it in seconds — that is precisely why
Phase 2 ships it.

---

## Cut lines if time runs short

Stated up front so the trade is yours, not a silent reduction:

| If you need | Cut | New total | What is lost |
|---|---|---|---|
| ~85 min | Phase 6 → Status tab only | ~85 min | History/Compare; **failover demo intact** |
| ~75 min | Phases 6 and 7 trimmed to `test_router.py` | ~75 min | Parsing/local tests |
| ~60 min | Phases 0–5 only | ~62 min | Analyze tab only; architecture still provable via `/providers/status` |

**Never cut:** Phase 2 (the router), the local fallback in Phase 3, or `test_router.py`.
Those three *are* the project's engineering claim.

---

## Standing rules for every phase

1. **Run the exit test before moving on.** A red stage is fixed, not carried forward.
2. **No keys in code, logs, or git.** Ever.
3. **Parameterised SQL only.**
4. **No raw stack traces reach the UI.**
5. **The app degrades; it does not crash.**
6. Report honestly at each phase: what works, what does not, and what was skipped.
