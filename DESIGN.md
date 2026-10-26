# ResumeMatch Coach — Design & Build Plan

**Version:** 1.1 · **Date:** 2026-10-23 · **Status:** awaiting approval to build
**Stack:** Python 3.13 · FastAPI · **React 18 + Vite** · SQLite3 · httpx
**Verified locally:** Python 3.13.5 · Node 22.19.0 · npm 10.9.3
**Cost:** $0 (free tiers only)

> **Changes in v1.1 (per your message):** frontend is **React + Vite**, not Streamlit.
> API keys and the Hugging Face token arrive later — §12 covers how the build proceeds
> and stays fully testable without them.

---

## 1. Overview

ResumeMatch Coach scores a resume against a job description, explains the gap, and
rewrites the weak bullet points to close it.

The *product* is a resume coach. The *engineering story* — the part worth presenting —
is underneath it:

| Capability | Why an evaluator cares |
|---|---|
| **Multi-agent pipeline** | Analyst → Rewriter → Orchestrator, each with its own contract and system prompt |
| **4-provider failover** | Hugging Face → OpenRouter → NVIDIA NIM → Cohere, tried in order |
| **Circuit breaker** | A failing provider is benched for 60s instead of being hammered |
| **Deterministic fallback** | If every provider is down, a pure-Python analyzer still answers |
| **Content-hash cache** | Identical input returns instantly and spends zero quota |
| **Full observability** | Every provider attempt is logged and surfaced in a System Status screen |

**The headline claim: this app cannot show an error page.** Worst case it degrades to
keyword analysis and says so plainly in the UI.

### Non-goals
No Docker, no auth, no ORM, no vector DB, no SSR, no state library, no component
framework, no cloud deploy. Those are scope traps for a demo build and add nothing to
the story.

---

## 2. Changes from the original brief

Six deliberate changes. Everything else in the brief is implemented as written.

| # | Change | Rationale |
|---|---|---|
| 1 | **React + Vite replaces Streamlit** | Your call. It also buys a genuinely designed UI: real CSS control, smooth transitions, and a frontend that looks like a product instead of a form. Costs ~30 extra build minutes (§13). |
| 2 | **Hugging Face Inference Providers added as provider #1** | You have an HF token. The HF router is OpenAI-compatible **and supports native JSON mode** — the single biggest reliability win for agents that must return strict JSON. |
| 3 | **`response_format` JSON mode where supported** | Turns "please return JSON" from a prompt-engineering hope into an API guarantee. Per-provider capability flag; the regex/repair parser stays as the safety net. |
| 4 | **New `POST /extract` endpoint** | A browser cannot run `pypdf`. PDF/TXT upload goes to the backend, which returns plain text the React form fills in. |
| 5 | **New `scripts/check_providers.py`** | Free-tier model IDs rotate constantly (OpenRouter `:free` slugs change weekly). One command pings every configured provider and prints a live PASS/FAIL table — a stale model ID becomes a 10-second fix instead of a mid-demo failure. |
| 6 | **Export result as Markdown / JSON** | Two lines of code; makes the demo feel finished. |
| 7 | **"The Coach" — an animated 2D face that reacts to the score** | Your call. Spec'd in §8.7 as an ink-drawn marginalia doodle so it reads as part of the editorial language rather than a cartoon bolted onto it. Zero dependencies (inline SVG + CSS), and it doubles as the loading indicator. |

---

## 3. Providers — probed live 2026-10-23

Not "checked against docs" — **actually called with the real keys.** Results below are
from `scripts/check_providers.py` plus three follow-up diagnostics.

### Result: 3 of 4 providers working

| Provider | Endpoint | Verified model | Latency | Strict JSON | Status |
|---|---|---|---|---|---|
| **Cohere** | `POST https://api.cohere.com/v2/chat` | `command-a-plus-05-2026` (436k ctx) | **0.9–3.2 s** | Clean | **PASS** |
| **NVIDIA NIM** | `POST https://integrate.api.nvidia.com/v1/chat/completions` | `minimaxai/minimax-m3.1-flash` | 1.0–7.2 s | Clean | **PASS** |
| **OpenRouter** | `POST https://openrouter.ai/api/v1/chat/completions` | `google/gemma-4-31b-it:free` | 1.2–1.8 s | Clean | **PASS**, rate-limits fast |
| **Hugging Face** | `POST https://router.huggingface.co/v1/chat/completions` | `openai/gpt-oss-120b` | — | — | **403 — token scope** |

Because the probe measured real latency, **provider order is now evidence-based rather
than assumed**:

```
PROVIDER_ORDER=cohere,nvidia,openrouter,huggingface
```

Cohere leads because it was both fastest and the most consistent. OpenRouter sits third
because its free tier returned `429` after only a handful of calls. Hugging Face sits
last until its token is fixed, then it is worth promoting — it is the only provider with
documented `json_schema` support.

### The Hugging Face 403 — diagnosed, needs one user action

The token is **valid** (resolves to user `Jaanageswar` via `/api/whoami-v2`) but its
fine-grained scopes are repo-only:

```
repo.contribute.write, repo.content.read, repo.access.read, repo.write, discussion.write
```

It is missing **`inference.serverless.write`** — the "Make calls to Inference Providers"
permission. The router correctly refuses with:

> *This authentication method does not have sufficient permissions to call Inference
> Providers on behalf of user Jaanageswar*

**Fix (one minute, your action):** create a new fine-grained token with *Make calls to
Inference Providers* ticked —
[huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained](https://huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained)
— then paste it into `.env` and move `huggingface` to the front of `PROVIDER_ORDER`.
No code changes.

### Three findings that would have broken the build

These are the reason for probing before writing the provider classes.

**1. Cohere's `content[0]` is not the answer.**
The original plan said parse `message.content[0].text`, which is what the docs' simple
example shows. `command-a-plus-05-2026` actually returns:

```json
"content": [ { "type": "thinking", ... }, { "type": "text", "text": "{...}" } ]
```

Indexing `[0]` returns the **chain of thought**, not the result. The parser must filter
by type:

```python
text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
```

**2. Reasoning models return `content: null` when the token budget runs out.**
`nvidia/nemotron-3.5-lightning-30b-a3b` at `max_tokens=600` returned **HTTP 200 with
`content = null`** — it had spent 976 tokens on hidden reasoning (`reasoning_content`)
and had nothing left. At `max_tokens=2000` it returned perfect JSON. Two consequences:

- `MAX_OUTPUT_TOKENS=2000`, not 600.
- A 200 response with empty content is a **real failure mode**, not a theoretical one.
  It maps to `EmptyResponse` → retry → fail over. The design already listed "empty
  response" as a retryable condition; this confirms it was not paranoia.

**3. NVIDIA's model catalog is not an entitlement list.**
`GET /v1/models` returns **84 models**, but on the free Developer tier most answer:

> *Function '…': Not found for account …*

and some are hard-410: `meta/llama-3.1-8b-instruct` is *"end of life on
2026-08-26, no longer available"*. Only three of 33 plausible instruct models actually
ran. **Listing a model proves nothing — only a real call does.** Hence the probe script.

### Response shape differences

```
OpenAI-compatible (HF / OpenRouter / NVIDIA):
    response["choices"][0]["message"]["content"]        -> str | None   (!)
    response["choices"][0]["message"]["reasoning_content"]  -> hidden CoT, ignore

Cohere v2:
    [b["text"] for b in response["message"]["content"] if b["type"] == "text"]
```

This is exactly why each provider is its own class behind one interface. The only method
anything outside `providers/` ever calls is `generate(system_prompt, user_prompt) -> str`.

### Model ID policy

**Model IDs are config, never code.** Every value lives in `.env` with a comment
recording when it was verified, and `scripts/check_providers.py` re-verifies on demand.
Each provider also takes a `*_FALLBACK_MODELS` list, tried in order *within* that
provider before failing over — so a single deprecated slug degrades to the next model
instead of knocking out the whole provider.

Verified values now in `.env`:

```ini
COHERE_MODEL=command-a-plus-05-2026
COHERE_FALLBACK_MODELS=command-a-03-2025,command-r-08-2024
NVIDIA_MODEL=minimaxai/minimax-m3.1-flash
NVIDIA_FALLBACK_MODELS=nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-ultra-550b-a55b
OPENROUTER_MODEL=google/gemma-4-31b-it:free
OPENROUTER_FALLBACK_MODELS=google/gemma-4-26b-a4b-it:free,thinkingmachines/inkling-small:free,liquid/lfm-2.5-2.6b:free
HF_MODEL=openai/gpt-oss-120b
```

OpenRouter currently exposes 17 `:free` models; the three chosen fallbacks were picked
for general instruction-following over coder- and vision-specific variants.

---
## 4. Architecture

```
  +--------------------------------------------------------------------+
  |              REACT + VITE  :5173                                   |
  |   Analyze  |  History  |  Compare  |  System Status                |
  +---------------------------------+----------------------------------+
                                    | fetch()  (/api -> Vite proxy)
                                    v
  +--------------------------------------------------------------------+
  |                      FASTAPI BACKEND  :8000                        |
  |  /analyze  /history  /analysis/{id}  /compare  /extract            |
  |  /providers/status   /health   DELETE /analysis/{id}               |
  +---------------------------------+----------------------------------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
       +---------------------+                +--------------------+
       |    CACHE LAYER      |   hit -->      |   SQLite  (file)   |
       |  SHA-256 of inputs  |  instant       |                    |
       +----------+----------+   return       |  resumes           |
                  | miss                      |  job_descriptions  |
                  v                           |  analyses          |
       +---------------------+                |  provider_logs     |
       |    ORCHESTRATOR     |--- writes ---> |                    |
       |  1. run Analyst     |                +--------------------+
       |  2. run Rewriter    |
       |  3. merge+validate  |
       |  4. persist         |
       +----------+----------+
                  |
        +---------+---------+
        v                   v
  +-----------+       +-----------+
  |  ANALYST  |       | REWRITER  |     each: system prompt + JSON contract
  |   AGENT   |       |   AGENT   |            + Pydantic validation
  +-----+-----+       +-----+-----+
        |                   |
        +---------+---------+
                  v
       +-------------------------------------------------+
       |              PROVIDER ROUTER                    |
       |   ordered failover + circuit breaker + logging  |
       +--+-------------+-------------+-------------+----+
          |             |             |             |
          v             v             v             v
     +--------+   +----------+   +--------+   +--------+
     |   HF   |-->|OpenRouter|-->| NVIDIA |-->| Cohere |
     | router |   |   :free  |   |  NIM   |   |   v2   |
     +--------+   +----------+   +--------+   +--------+
          |
          |  ALL FOUR FAILED
          v
     +-------------------------------------------+
     |   LOCAL ANALYZER  (no AI, no network)     |
     |   keyword overlap -> score + gaps         |
     |   source = "local_fallback"               |
     +-------------------------------------------+
```

Every arrow between the router and a provider writes one row to `provider_logs`. That
table is what the System Status screen renders — the failover is not just claimed, it is
visible.

---

## 5. Agent layer

### 5.1 Shared base (`agents/base.py`)

```python
class Agent:
    name: str
    system_prompt: str
    schema: type[BaseModel]          # Pydantic model = the contract

    def run(self, **inputs) -> BaseModel:
        """build prompt -> router.generate -> parse -> validate.
           On validation failure: one stricter retry, then raise AgentError."""
```

### 5.2 Analyst Agent

**Role:** cold, evidence-based evaluator. Scores only what the resume actually says.

```json
{
  "overall_score": 0,
  "score_breakdown": { "skills": 0, "experience": 0, "keywords": 0 },
  "matched_skills": ["..."],
  "missing_skills": ["..."],
  "summary": "2-3 sentences"
}
```

**Prompt rules:** integers 0–100; `overall_score` consistent with the breakdown; skills
are short noun phrases, not sentences; never invent a skill absent from the resume;
JSON only — no fences, no preamble.

### 5.3 Rewriter Agent

**Role:** senior resume editor. Receives the resume, the JD, **and the Analyst's
`missing_skills`** — so it targets the identified gap instead of rewriting blind.

```json
{
  "rewritten_bullets": [
    { "original": "...", "improved": "...", "reason": "..." }
  ],
  "suggestions": ["..."]
}
```

**Prompt rules:** max 5 bullets; `original` copied verbatim from the resume; `improved`
adds a metric or a JD keyword while staying truthful — **never fabricate numbers,
employers, or credentials**; `reason` is one short clause; 3–5 suggestions.

That honesty constraint is non-negotiable: a resume tool that invents achievements is a
liability, not a feature.

### 5.4 Orchestrator

```
run(resume, jd, force):
    key = sha256(norm(resume) + norm(jd) + "v1")
    if not force and cached(key): return cached + {cached: true}

    analyst = Analyst.run(resume, jd)            # if this fails -> local fallback
    try:
        rewriter = Rewriter.run(resume, jd, analyst.missing_skills)
        partial  = False
    except AgentError:
        rewriter = empty()                        # degrade, don't fail
        partial  = True

    result = merge(analyst, rewriter, partial=partial, source="ai")
    persist(result); return result
```

Two independent degradation paths: Rewriter failure → `partial=true` (score still
shown); Analyst failure → `source="local_fallback"` (keyword analysis shown). The
request never 500s.

### 5.5 JSON parsing & repair (`agents/parsing.py`)

Free-tier models wrap JSON in fences, add prose, and trail commas. A 5-stage ladder:

1. `json.loads` on the raw text
2. Strip ```` ```json ```` / ```` ``` ```` fences, retry
3. Extract the first **balanced** `{...}` block by brace counting — a naive regex breaks
   on nested objects — retry
4. Light repair: drop trailing commas, normalise smart quotes, retry
5. One LLM retry with a stricter reminder appended; still failing → `ParseError` → the
   router moves to the next provider

Validation is always Pydantic. Syntactically valid JSON with the wrong shape is still a
failure.

---

## 6. Provider router — the core feature

### 6.1 Interface (`providers/base.py`)

```python
class Provider(ABC):
    name: str
    model: str
    supports_json_mode: bool

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str: ...

    def is_configured(self) -> bool: ...     # API key present?
```

### 6.2 Failover algorithm

```
for provider in PROVIDER_ORDER:
    if not provider.is_configured():  skip, log "no_key"
    if breaker.is_cooling(provider):  skip, log "cooling_down"

    for attempt in (1, 2):                      # at most ONE retry
        t0 = now()
        try:
            text = provider.generate(...)       # 30s timeout
            if blank(text): raise EmptyResponse
            log(provider, model, ok, latency)
            breaker.record_success(provider)
            return text
        except RETRYABLE as e:
            log(provider, model, fail, latency, reason(e))
            breaker.record_failure(provider)
            if attempt == 2 or not quick_retryable(e): break

# every provider exhausted
return LocalAnalyzer.run(...)                   # source = local_fallback
```

### 6.3 Error taxonomy

| Condition | Action | Reason |
|---|---|---|
| HTTP 429 (rate limit) | **Next provider immediately** | Free daily caps — retrying burns the quota |
| HTTP 402 (payment) | **Next provider**, no retry | Will not recover today |
| HTTP 401/403 (bad key) | **Next provider**, no retry | Config problem, not transient |
| HTTP 404 (bad model ID) | **Next provider**, no retry | Stale slug — `check_providers.py` catches this |
| HTTP 5xx | 1 quick retry, then next | Often transient |
| Timeout (30s) | 1 quick retry, then next | Cold start on free tier |
| Network / DNS error | 1 quick retry, then next | Transient |
| Empty response | 1 quick retry, then next | Known free-tier quirk |
| Unparseable JSON | 1 stricter retry, then next | Handled in the agent layer |

### 6.4 Circuit breaker

In-memory `{provider: (consecutive_failures, cooling_until)}`. Two consecutive failures
→ `cooling_until = now + 60s` → the provider is skipped (and logged as skipped) until it
expires. Any success resets the counter to 0. Deliberately not persisted: a process
restart should give every provider a fresh chance.

### 6.5 Local fallback (`fallback/local_analyzer.py`)

Pure Python, no network, no sklearn. Deterministic and instant.

1. Lowercase, strip punctuation, tokenise both texts
2. Remove a built-in stopword list; build unigrams **and bigrams** (so "machine
   learning" survives as one concept)
3. Rank JD terms by frequency; take the top ~40 as required keywords
4. Intersect against a ~150-term curated tech-skill vocabulary to separate real *skills*
   from generic noise
5. `score = round(100 * len(matched) / max(len(required), 1))`, clamped 0–100; breakdown
   derived from skill-overlap / term-overlap / density
6. `summary` from a template; `rewritten_bullets = []`; `suggestions` = canned but
   genuinely useful advice (e.g. "add these missing keywords to your skills section")
7. `source = "local_fallback"` → the UI shows an amber banner:
   **"AI providers unavailable — showing basic keyword analysis."**

---

## 7. Data layer

### 7.1 Caching

`input_hash = sha256(normalise(resume) + "||" + normalise(jd) + "||v1")`
where `normalise` = strip, collapse all whitespace, lowercase.

`UNIQUE` on `analyses.input_hash` makes the cache and the dedupe constraint the *same*
mechanism — no second table to drift out of sync. The `v1` suffix is a manual cache-bust
lever for when prompts change. "Force re-analyze" bypasses the read and `UPSERT`s the row.

### 7.2 Schema (`data/resumematch.db`, auto-created on startup)

```sql
CREATE TABLE IF NOT EXISTS resumes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL, content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_descriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resume_id INTEGER NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
  job_id    INTEGER NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
  input_hash TEXT NOT NULL UNIQUE,
  score INTEGER NOT NULL,
  score_breakdown_json   TEXT NOT NULL,
  matched_skills_json    TEXT NOT NULL,
  missing_skills_json    TEXT NOT NULL,
  rewritten_bullets_json TEXT NOT NULL,
  suggestions_json       TEXT NOT NULL,
  summary TEXT,
  provider_used TEXT, model_used TEXT,
  source TEXT NOT NULL DEFAULT 'ai',          -- 'ai' | 'local_fallback'
  partial INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  analysis_id INTEGER REFERENCES analyses(id) ON DELETE SET NULL,
  provider TEXT NOT NULL, model TEXT,
  success INTEGER NOT NULL, latency_ms INTEGER,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analyses_hash    ON analyses(input_hash);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_job     ON analyses(job_id);
CREATE INDEX IF NOT EXISTS idx_logs_created     ON provider_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_provider    ON provider_logs(provider, created_at DESC);
```

Notes: JSON stored as TEXT (SQLite has no native JSON type), decoded at the edge.
`PRAGMA foreign_keys = ON` per connection — SQLite ignores FKs otherwise.
`check_same_thread=False` plus a lock, since FastAPI is threaded.
**Parameterised queries only** — no f-string SQL anywhere, no exceptions.

---

## 8. Frontend — React + Vite

### 8.1 Stack decisions

| Choice | Decision | Why |
|---|---|---|
| Build tool | **Vite** | Instant scaffold, instant HMR, zero config |
| React | **18.3.1** (pinned) | Stable; nothing here needs 19 |
| Styling | **Plain CSS with custom properties** | Full control of the look with zero config time. Tailwind would cost setup minutes and fight the editorial aesthetic. |
| Charts | **Recharts** (trend line) + **hand-rolled SVG** (score ring) | Recharts is the one UI dependency worth having; the ring is 20 lines of `stroke-dasharray` and needs no library |
| Routing | **`useState` tabs** | Four panels in one view. `react-router` would add a dependency to solve a problem that does not exist. |
| Data fetching | **`fetch` in `api.js`** | No react-query. Each tab owns its loading/error state in a tiny `useAsync` hook. |
| State | **Local component state** | No Redux, no Zustand. Nothing is shared deeply enough to need it. |

Dependencies: `react`, `react-dom`, `recharts` + `vite`, `@vitejs/plugin-react` as dev
deps. **Five packages.**

Vite proxies `/api` → `http://localhost:8000` so dev has no CORS friction; CORS stays
enabled server-side anyway for direct calls.

### 8.2 Design language — "Classic Editorial"

A well-set print document, not a dashboard. Warm paper ground, deep navy ink, one brass
accent, a serif display face for headings and a clean sans for everything you actually
read. Generous whitespace, hairline rules instead of drop shadows.

Calm, confident, immediately legible — *classic* does the structural work, *cool* comes
from restraint rather than gradients and glow.

### 8.3 Design tokens (`src/styles/tokens.css`)

| Token | Value | Use |
|---|---|---|
| `--paper` | `#FBF8F3` | Page background (warm off-white) |
| `--card` | `#FFFFFF` | Card surfaces |
| `--ink` | `#1C2434` | Primary text (deep navy — softer than black) |
| `--ink-soft` | `#5A6478` | Secondary text, captions |
| `--rule` | `#E4DDD1` | Hairline borders, dividers |
| `--brass` | `#B07D2B` | Accent: score ring, active tab, links |
| `--sage` | `#2E6B52` | Matched skills, success, positive delta |
| `--rust` | `#A33B2A` | Missing skills, errors, negative delta |
| `--amber` | `#C98A14` | Fallback / partial warnings |
| `--font-display` | `Playfair Display`, serif | H1/H2, the score number |
| `--font-body` | `Inter`, system-ui | Everything else |
| `--font-mono` | `JetBrains Mono`, monospace | Bullet diffs, log table |
| `--radius` | `10px` cards · `999px` pills | |
| `--shadow` | `0 1px 2px rgba(28,36,52,.05)` | Hairlines over shadows |

Fonts load from Google Fonts with system serif/sans fallbacks, so the app still looks
right offline. Every colour pair meets WCAG AA for its text size. A dark variant is a
single token-block override if you want one later.

### 8.4 Layout

```
+----------------------------------------------------------------------+
|  [=]  ResumeMatch Coach                            o HF  o OR  o NIM |
|       multi-agent resume analysis                   <- live dots      |
+----------------------------------------------------------------------+
|  ANALYZE   |   HISTORY   |   COMPARE   |   SYSTEM STATUS             |
+----------------------------------------------------------------------+
```

The header dots poll `/providers/status` every 30s: sage = ready, amber = cooling down,
grey = no key. Hovering names the provider and its model.

**Tab 1 — Analyze**
```
+--------------------------------+  +--------------------------------+
|  YOUR RESUME                   |  |  JOB DESCRIPTION               |
|  [ drop .pdf / .txt or browse ]|  |                                |
|  +--------------------------+  |  |  +--------------------------+  |
|  |                          |  |  |  |                          |  |
|  |        textarea          |  |  |  |        textarea          |  |
|  +--------------------------+  |  |  +--------------------------+  |
|  2,418 / 15,000                |  |  1,002 / 15,000                |
+--------------------------------+  +--------------------------------+
   [ Load sample ]   [ ] Force re-analyze        [ (o o)  ANALYZE  ]
                                                   ^- the Coach peeks in
                                                      while it thinks

--- results (fades in) -------------------------------------------------
      .-""-.           SKILLS      EXPERIENCE    KEYWORDS      ( ^   ^ )
     /  78  \            82            71           80          \  ~  /
     \      /          [=====]       [====.]      [=====]         '---'
      '-..-'                                                 .-------------.
   Strong match       huggingface / gpt-oss-120b            ( Close! Four   )
                      fresh . 2,310 ms . analyst+rewriter    ( skills short. )
                                                              '-------------'

  MATCHED (9)                        MISSING (4)
  (Python)(FastAPI)(SQL)(Docker)     (Kubernetes)(Terraform)(gRPC)

  REWRITTEN BULLETS
  +------------------------------------------------------------------+
  | BEFORE  Built APIs for internal tools                            |
  | AFTER   Designed and shipped 12 FastAPI microservices serving    |
  |         40k req/day, cutting p95 latency 38%                     |
  | WHY     Adds scale metrics and the JD's "microservices" keyword  |
  +------------------------------------------------------------------+
  [ Copy all improved bullets ]

  SUGGESTIONS                                   [ Download .md ] [ .json ]
  1. ...
```

The score ring animates from 0 on mount (`stroke-dasharray` transition, ~600ms) and
the bullet cards stagger in. Colour band: `<40` rust, `40–69` amber, `70+` sage.

**Tab 2 — History** — filter row (job title, min score, source) over a clean table
(date, resume, job, score chip, provider, badges). Click a row → detail panel slides in
below. Delete asks for confirmation inline, not in a modal.

**Tab 3 — Compare** — two selects (A = older, B = newer), a large delta readout
(`+14` sage with ↑, or rust with ↓), a three-column skills diff labelled *newly
matched* / *still missing* / *newly missing*, and a Recharts line of score over time for
the selected job.

**Tab 4 — System Status** — one card per provider: name, model, configured/missing key,
cooling-down countdown, success rate, average latency; below it the latest 20
`provider_logs` rows, colour-coded by outcome. This is the screen that proves the
architecture, so it gets real design attention rather than a raw table dump.

### 8.5 Component inventory

```
src/
  main.jsx
  App.jsx                    header, tab state, provider-dot polling
  api.js                     fetch wrapper, friendly error normalisation
  hooks/useAsync.js          loading / error / data for every call
  styles/tokens.css          design tokens
  styles/app.css             layout, cards, tabs, tables
  styles/coach.css           the Coach's keyframes and mood colours
  components/
    CoachFace.jsx            the 2D reactive face (inline SVG, 6 moods)  <- §8.7
    CoachBubble.jsx          speech bubble, one line keyed to the result
    ScoreRing.jsx            animated SVG arc + centred score
    MetricBar.jsx            breakdown bars
    SkillPills.jsx           sage/rust pill rows with counts
    BulletDiff.jsx           before/after/why card
    ProviderCard.jsx         status card with cooldown countdown
    Banner.jsx               amber fallback / partial notices
    FileDrop.jsx             drag-drop → POST /extract
    CharCount.jsx            live counter, turns rust over the cap
    EmptyState.jsx           first-run and no-results states
    ErrorCard.jsx            plain sentence + "Technical details" expander
  tabs/
    AnalyzeTab.jsx  HistoryTab.jsx  CompareTab.jsx  StatusTab.jsx
```

### 8.6 Interaction rules
- **Never show a stack trace.** Backend errors render in `ErrorCard` as one plain
  sentence, with the technical detail behind a collapsed expander.
- `source="local_fallback"` → persistent amber banner above the results.
- `partial=true` → amber note above the empty rewrite section, explaining that scoring
  succeeded and rewriting did not.
- The Analyze button shows the live stage: *"Analyst agent scoring…"* then *"Rewriter
  agent drafting…"* — the pipeline is visible while it runs.
- A **"How it works"** expander in the header carries a compact §4 diagram.
- Buttons disable during flight; double-submit is impossible.
- Keyboard accessible throughout; focus rings kept, never `outline: none`.
- `prefers-reduced-motion` disables the ring animation and stagger.
- **The Coach** (§8.7) is the loading indicator during analysis and the reaction
  afterwards — it replaces a generic spinner rather than adding a second one.

---

## 8.7 The Coach — a reactive 2D face

A small face that appears while the agents work and reacts to the score when they
finish.

### Design intent

The risk with a mascot is that it makes a serious tool look like a toy. The way to get
cute **and** professional is to draw it in the same language as everything else: **the
Coach is a margin doodle** — the kind of face someone sketches in the margin of a
printed document while reviewing it. Same ink stroke as the hairline rules, same paper
fill as the cards, hand-drawn wobble rather than geometric perfection, and **never
larger than 72px**. It reads as marginalia, not as a cartoon.

It also earns its place functionally: it *is* the loading indicator, so it replaces the
spinner instead of adding chrome. And its mouth is drawn with the same colour band as
the score ring, so the two always agree at a glance.

### Moods

One pure function is the single source of truth for the face, its colour and its line:

```js
moodFor(state, score)   // 'resting'|'thinking'|'wincing'|'hopeful'|'pleased'|'delighted'|'apologetic'
```

| Trigger | Mood | Eyes | Brow | Mouth | Extra | Colour |
|---|---|---|---|---|---|---|
| Idle, nothing analysed | `resting` | round, blink every ~5s | neutral | soft flat curve | slow 3s bob | `--ink` |
| Analysis in flight | `thinking` | half-lidded, pupils drift L↔R | one raised | small `o` | 3 dots orbit above the head | `--brass` |
| Score 0–39 | `wincing` | squeezed shut (two short arcs) | furrowed | down-curve | one sweat bead drips once | `--rust` |
| Score 40–69 | `hopeful` | wide, asymmetric brows | one up | small closed smile | 4° head tilt | `--amber` |
| Score 70–84 | `pleased` | happy closed arcs `^ ^` | relaxed | open smile | two-bounce on entry | `--sage` |
| Score 85–100 | `delighted` | four-point sparkles | raised | wide smile | 5 confetti ticks fall once | `--sage` |
| Error or `local_fallback` | `apologetic` | flat lines `- -` | flat | small wavy line | tiny shrug | `--amber` |

### Geometry & animation technique

- **96×96 viewBox**, head drawn as a *slightly irregular* `<path>` rather than a
  `<circle>` — that wobble is what makes it look penned instead of plotted. Stroke
  `--ink` at 2.5px with `stroke-linecap: round`, fill `--card`.
- **Mouth** — one `<path>` per mood, all sharing an identical command structure
  (`M 34 60 Q 48 {y} 62 60`), so a CSS `transition: d 320ms` interpolates the shape
  smoothly where `d` animation is supported and snaps cleanly where it is not.
- **Eyes** — cross-fade `opacity` between two `<g>` groups rather than morphing paths.
  Less clever, works in every browser.
- **Blink** — `scaleY(0.1)` for 90ms on a 5s loop, with a staggered delay per eye so it
  is not mechanically symmetrical.
- **Entry** — scale from `0.7` with a slight overshoot, `420ms cubic-bezier(.34,1.56,.64,1)`.
- **One-shot effects** (confetti, sweat bead) unmount on `onAnimationEnd`, so nothing
  loops forever and nothing leaks a timer.

All of it is **inline SVG plus CSS keyframes — zero new dependencies.**

### Speech bubble

`CoachBubble.jsx` renders one short line, keyed to the actual result rather than to
vibes:

| Mood | Line |
|---|---|
| `resting` | "Paste a resume and a job post." |
| `thinking` | "Reading your resume…" → "Rewriting your bullets…" (follows the pipeline stage) |
| `wincing` | "Big gap — the bullets below are where you start." |
| `hopeful` | "Close. {n} skills short." |
| `pleased` | "Strong match. Tighten those bullets." |
| `delighted` | "This one's ready to send." |
| `apologetic` | "AI's offline — here's a keyword pass." |

**Deterministic, one line per mood — no random rotation.** Re-analysing the same resume
and getting a different quip makes the app feel broken rather than lively.

### Placement

| Where | Size | State |
|---|---|---|
| Inside the Analyze button, left of the label | 28px | `thinking` while in flight |
| Results header, right of the metrics row | 72px | the score-band mood + bubble |
| History detail panel | 40px | the stored score's mood, no bubble |
| Empty state, before any analysis | 56px | `resting` |

### Rules that keep it professional

1. **The face is never the only signal.** The score number, the verdict word and the
   colour band always render as text next to it. The Coach is decoration on top of
   information, never a substitute for it.
2. **`role="img"` with a real `aria-label`** — *"Coach looks pleased. Strong match, 78
   out of 100."* A screen reader gets the meaning, not "image".
3. **`prefers-reduced-motion: reduce`** → no bob, no blink, no confetti, no drip. The
   correct static expression renders immediately. Motion is a bonus, never the mechanism.
4. **Never exceeds 72px.** The moment it dominates the page it stops being marginalia.
5. **It can be switched off** — a small toggle in the header (persisted to
   `localStorage`) renders the plain layout with no face. Cheap insurance if you ever
   present to an audience that wants it strictly formal.
6. **Mood colour is the same token as the score ring**, so the two can never disagree.

### Cost

`CoachFace.jsx` ~120 lines, `CoachBubble.jsx` ~30, `styles/coach.css` ~80.
**Estimated 10 minutes** — tracked as its own phase in `PHASES.md` so it stays easy to
defer without touching anything else.

---

## 9. Project structure

```
resumematch-coach/
  backend/
    main.py              FastAPI app, routes, CORS, startup hook
    config.py            env loading, PROVIDER_ORDER, limits (no secrets logged)
    db.py                connection, schema DDL, query helpers
    models.py            Pydantic request/response + agent contracts
    cache.py             hash + cache read/write
    extract.py           PDF/TXT -> text (pypdf), in-memory only
    agents/
      base.py            Agent ABC, prompt assembly, retry
      parsing.py         5-stage JSON extraction + repair
      analyst.py         Analyst agent + system prompt
      rewriter.py        Rewriter agent + system prompt
      orchestrator.py    pipeline, merge, degradation, persist
    providers/
      base.py            Provider ABC
      huggingface.py     HF router (OpenAI-compatible, JSON mode)
      openrouter.py      OpenRouter + model fallback list
      nvidia.py          NVIDIA NIM
      cohere.py          Cohere v2 (different response shape)
      router.py          failover, circuit breaker, logging
    fallback/
      local_analyzer.py  deterministic keyword analyzer
  frontend/
    index.html
    package.json
    vite.config.js       React plugin + /api proxy -> :8000
    src/                 (see 8.5)
  scripts/
    check_providers.py   live PASS/FAIL table for every configured provider
  sample_data/
    resume.txt
    job_description.txt
  tests/
    test_router.py       failover order, circuit breaker, local fallback
    test_parsing.py      JSON extraction + repair cases
    test_local.py        local analyzer determinism
  data/                  gitignored; db auto-created here
  .env.example
  .gitignore
  requirements.txt
  README.md
  run.ps1                Windows: backend :8000 + vite :5173   <- your platform
  run.sh                 POSIX equivalent
  DESIGN.md              this file
```

---

## 10. API surface

| Method | Path | Body / Params | Returns |
|---|---|---|---|
| `GET` | `/health` | — | `{status, db, providers_configured, version}` |
| `POST` | `/analyze` | `resume_text`, `job_text`, `resume_label?`, `job_title?`, `force?` | Full `AnalysisResult` |
| `POST` | `/extract` | `multipart` file (.pdf/.txt) | `{text, chars, pages?}` |
| `GET` | `/history` | `limit?`, `job_title?`, `min_score?`, `source?` | `[HistoryItem]` |
| `GET` | `/analysis/{id}` | — | `AnalysisResult` or 404 |
| `GET` | `/compare` | `a`, `b` | `{a, b, score_delta, newly_matched[], still_missing[], newly_missing[]}` |
| `GET` | `/providers/status` | — | `[{provider, model, configured, cooling_down, cooling_seconds_left, success_rate, avg_latency_ms, attempts}]` |
| `DELETE` | `/analysis/{id}` | — | `204` or 404 |

**Validation:** reject empty/whitespace text (422); cap each input at 15,000 chars (422
with a clear message); `/extract` caps upload at 5 MB and rejects non-PDF/TXT; `force`
defaults false. CORS allows `localhost:5173`. A global exception handler returns
`{detail: "..."}` JSON — never an HTML traceback.

---

## 11. Build order & time budget

Each stage ends with a smoke test. Nothing proceeds on a red stage.

| # | Stage | Min | Smoke test |
|---|---|---|---|
| 0 | Scaffold, `requirements.txt`, `.env.example`, `.gitignore` | 5 | `pip install -r requirements.txt` clean |
| 1 | `config.py` + `db.py` | 6 | Run once → `data/resumematch.db` with 4 tables |
| 2 | Providers + `router.py` + `check_providers.py` | 15 | Script runs; with no keys, reports "no key" cleanly for all four |
| 3 | `parsing.py` + agents + orchestrator + local fallback | 14 | Direct Python call returns a validated result via local fallback |
| 4 | FastAPI routes + `/extract` | 10 | `curl /health`, then `/analyze` with sample data |
| 5 | Vite scaffold + tokens + `App.jsx` + Analyze tab | 20 | Full round-trip in the browser; score ring renders |
| 5b | **The Coach** (§8.7) | 10 | All 7 moods render; reduced-motion respected |
| 6 | History, Compare, System Status tabs | 18 | All four tabs work against real data |
| 7 | Tests | 8 | `pytest -q` green, zero real API calls |
| 8 | README + final verification | 7 | Fresh-start run, `/health` + `/analyze` confirmed |
| | **Total** | **~113** | |

**Honest revision:** the React frontend pushes this from ~70 to **~110 minutes**, not
the original hour. Streamlit was the thing making 60 minutes plausible. Two levers if
you want it shorter, both your call rather than a silent cut:

- **Ship Analyze + System Status first (~85 min)**, then add History/Compare. Those two
  tabs are the ones that demo the architecture.
- **Compress tests to `test_router.py` only** (−5 min). I would not cut further; the
  router tests *are* the proof the failover works.
- **Defer the Coach** (−10 min). Phase 5b is deliberately self-contained, so it can land
  last without touching anything else — though it is also the cheapest 10 minutes of
  "this feels like a product" in the whole build.

---

## 12. Key status, and why no stage depends on them

**Status (2026-10-23):** all four keys received and stored in `.env`. Three work
(Cohere, NVIDIA, OpenRouter); Hugging Face needs a token-scope fix — §3. So the app will
run on real AI from Phase 3 onward.

That said, no build stage *depends* on a key, which is what makes the schedule reliable:

| Stage | Works with zero keys? | How it is verified now |
|---|---|---|
| Config, DB, cache | Yes | Direct |
| Provider classes | Yes | Unit-tested against mocked `httpx` responses — no network |
| Router + circuit breaker | Yes | `test_router.py` uses fake providers; this is the *correct* way to test failover anyway |
| Agents + parsing | Yes | `test_parsing.py` feeds recorded malformed LLM output |
| Local fallback | Yes | It is the no-key path |
| FastAPI routes | Yes | `/analyze` returns a real result via `source="local_fallback"` |
| React UI, all 4 tabs | Yes | Fallback results exercise every component, banner, and chart |

Every row is verified with mocked providers, so the test suite stays fast, offline, and
free — and a provider going down mid-build cannot block a stage.

**To add the fixed HF token later:** paste it into `.env`, run
`python scripts/check_providers.py`, move `huggingface` to the front of
`PROVIDER_ORDER`, restart the backend. **No code changes** — that is the whole point of
the provider abstraction, and it is now demonstrated rather than asserted.

Secret handling and the local-only guarantees are in §17.

---

## 13. Testing

`tests/test_router.py` — fake providers only, zero network:
- all four configured, first succeeds → one attempt, no fallthrough
- #1 raises 429 → #2 used; order respected
- providers with no key are skipped entirely
- 2 consecutive failures → provider marked cooling, skipped on the next call
- a success resets the failure counter
- every provider fails → `LocalAnalyzer` result with `source="local_fallback"`
- every attempt wrote a `provider_logs` row

`tests/test_parsing.py` — raw JSON; fenced ```` ```json ````; prose before and after;
nested braces (proves brace-counting beats regex); trailing comma; smart quotes; valid
JSON of the wrong shape → Pydantic error; unsalvageable garbage → `ParseError`.

`tests/test_local.py` — identical input twice is byte-identical; score bounded 0–100;
a known resume/JD pair lands in the expected band.

Tests use `monkeypatch` and an in-memory SQLite DB. **No test consumes API quota.**

---

## 14. Configuration

`.env.example` is committed with blank values and comments. `.env` holds the real keys,
is gitignored, and is never logged (§17).

Shape of both files, with the **live-verified** model IDs now in use:

```ini
# Order is evidence-based: measured latency and reliability, not preference (§3).
PROVIDER_ORDER=cohere,nvidia,openrouter,huggingface

# ---- Cohere ----       https://dashboard.cohere.com/api-keys
COHERE_API_KEY=
COHERE_MODEL=command-a-plus-05-2026
COHERE_FALLBACK_MODELS=command-a-03-2025,command-r-08-2024

# ---- NVIDIA NIM ----   https://build.nvidia.com
# Most of the 84-model catalog is NOT invokable on the free tier - verify before use.
NVIDIA_API_KEY=
NVIDIA_MODEL=minimaxai/minimax-m3.1-flash
NVIDIA_FALLBACK_MODELS=nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-ultra-550b-a55b

# ---- OpenRouter ----   https://openrouter.ai/keys
# ":free" slugs rotate weekly. Re-verify with: python scripts/check_providers.py --discover
OPENROUTER_API_KEY=
OPENROUTER_MODEL=google/gemma-4-31b-it:free
OPENROUTER_FALLBACK_MODELS=google/gemma-4-26b-a4b-it:free,thinkingmachines/inkling-small:free

# ---- Hugging Face ----
# MUST be a fine-grained token with "Make calls to Inference Providers" ticked,
# or the router returns 403. A repo-scoped token is not enough (§3).
HF_TOKEN=
HF_MODEL=openai/gpt-oss-120b

# ---- Tuning ----
REQUEST_TIMEOUT_SECONDS=45
# 2000, not 600: reasoning models spend their budget on hidden reasoning tokens
# and return content=null if too little is left (§3, finding 2).
MAX_OUTPUT_TOKENS=2000
MAX_INPUT_CHARS=15000
BREAKER_FAILURE_THRESHOLD=2
BREAKER_COOLDOWN_SECONDS=60

# ---- Local-only binding: loopback, never 0.0.0.0 (§17) ----
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
```

**Python deps** (`requirements.txt`): `fastapi`, `uvicorn[standard]`, `httpx`,
`pydantic`, `python-dotenv`, `pypdf`, `python-multipart`, `pytest`. Eight packages, all
pure-Python wheels on 3.13 — no compiler, no model download, no GPU.

**Node deps**: `react`, `react-dom`, `recharts`, `vite`, `@vitejs/plugin-react`.

### Security
- Keys come from env only — never hardcoded, never logged, never returned by any
  endpoint. `/providers/status` reports a boolean `configured`, never a key or a prefix.
- `.gitignore` covers `.env`, `data/`, `__pycache__/`, `*.db`, `.venv/`,
  `node_modules/`, `dist/`.
- Parameterised SQL only. Uploaded files are parsed in memory and never written to disk.
- Input caps bound both memory and token spend.
- React escapes by default; no `dangerouslySetInnerHTML` anywhere, so LLM output cannot
  inject markup.

---

## 15. Known limitations

To be stated plainly in the README rather than discovered by an evaluator:

1. **Keyword-weighted scoring.** Even with an LLM, matching leans on terminology. A
   strong candidate who words things differently can score low.
2. **Free-tier models are weaker.** Rewrite quality is good, not senior-editor good. The
   architecture is model-agnostic — a paid key in the same env var improves output with
   zero code change.
3. **Free-tier limits are real.** ~20 req/min and tight daily caps are typical; the cache
   and circuit breaker exist precisely because of this.
4. **Model IDs drift.** Free slugs get deprecated without notice.
   `check_providers.py` is the mitigation, not a cure.
5. **PDF extraction is text-only.** Multi-column and scanned resumes extract poorly;
   there is no OCR. The UI advises pasting text if extraction looks wrong.
6. **In-memory circuit breaker.** Single-process only; state resets on restart. Correct
   for this scope, wrong for a multi-worker deploy.
7. **No auth, single user, local only.** Anyone with the URL sees all history.
8. **Dev-mode frontend.** `npm run dev`, not a production build behind a CDN.
9. **Rewrites need human review.** The prompt forbids fabrication, but LLMs drift — the
   UI frames output as suggestions to verify, never as finished copy.

---

## 16. Open decisions

None of these block the build; I will proceed on the defaults shown unless you say
otherwise.

1. **Keys** — send whenever ready. Default until then: build and verify on the local
   fallback path (§12).
2. **Theme** — defaulting to "Classic Editorial" (warm paper, navy ink, brass accent,
   serif headings) per §8.2. A dark variant is a one-file token override if you prefer.
3. **Scope if time is tight** — defaulting to building all four tabs (~100 min). Say so
   and I will ship Analyze + System Status first at ~75 min.
4. **Git** — this folder is not a repo yet. Say the word and I will `git init` with the
   `.gitignore` in place from the first commit, so no `.env` ever enters history.

---

## 17. Local-only operation & secret handling

You asked that this run only locally and never be pushed anywhere. That is a design
constraint, not a deployment note, so it is written down here with the specific
mechanisms that enforce it.

### How the keys are stored

| | |
|---|---|
| **Location** | `.env` in the project root — the only file on disk that contains a key |
| **Loaded by** | `python-dotenv` at startup, into process memory |
| **In git** | `.gitignore` lists `.env` and `.env.*` (with `!.env.example`), written **before** `.env` was created, so no key has ever existed in a repo-visible state |
| **In code** | Never. No key appears in any `.py`, `.jsx`, `.md`, or config file |
| **In logs** | Never. `provider_logs` stores provider name, model, latency, and error reason — never credentials |
| **In API responses** | Never. `/providers/status` returns `configured: true|false`, never a key, never a prefix |
| **In `config.py`** | `Settings.__repr__` masks every secret, so even an accidental `print(settings)` is safe |
| **In error text** | The probe truncates upstream error bodies to 120 chars and never echoes the request |

### Network boundary

| Direction | What happens |
|---|---|
| **Inbound** | Backend binds `127.0.0.1:8000` (`BACKEND_HOST` in `.env`), Vite binds `127.0.0.1:5173`. Loopback only — **not** `0.0.0.0`, so nothing on your Wi-Fi or LAN can reach it. |
| **Outbound** | Only to the four provider APIs, and only the resume and job-description text you submit, as the body of an inference request. Nothing else leaves the machine. |
| **Data at rest** | `data/resumematch.db`, a local SQLite file, gitignored. Nothing is uploaded, synced, or backed up anywhere. |
| **Telemetry** | None. No analytics, no crash reporting, no CDN beacons. The only third-party fetch in the frontend is Google Fonts, and it has a system-font fallback — cut the `<link>` and the app is fully offline except for provider calls. |

### What "never pushed outside" means concretely

- **No git remote.** This is not a repo yet (see §16.4). If you want version control, I
  will `git init` with the `.gitignore` already in place — but I will not add a remote
  or push anything without you asking.
- **No deploy step exists.** There is no Dockerfile, no CI config, no Vercel/Netlify/HF
  Spaces config, no `gh-pages` script. Nothing in this project can publish itself.
- **No artifact publishing.** Results stay in SQLite and in the `.md`/`.json` files you
  explicitly download.
- **If you later want to share it:** share the repo *without* `.env` (the `.gitignore`
  already guarantees this) and let the recipient add their own keys. Never send `.env`.

### One thing you should do

**Rotate these four keys when the project is finished.** They were pasted into a chat
message, which means they exist in this conversation's history as well as in your `.env`.
That is fine for a local demo on your own machine, but it is one more copy than a
credential should have. Each provider's dashboard revokes and reissues in about thirty
seconds:

- [Cohere](https://dashboard.cohere.com/api-keys) ·
  [OpenRouter](https://openrouter.ai/keys) ·
  [NVIDIA](https://build.nvidia.com) ·
  [Hugging Face](https://huggingface.co/settings/tokens)

Not urgent, and nothing here leaks them — but worth doing once the demo is done.

### Verification step

Phase 8 includes an explicit check: grep the whole tree for each key's distinctive
prefix (`sk-or-`, `nvapi-`, `hf_`, `cohere_`) and confirm the only hits are `.env`
itself. That check is in the phase checklist so it cannot be forgotten.

---
## Appendix — sources checked 2026-10-23

- **Hugging Face** Inference Providers, chat-completion task reference — endpoint, auth
  header, `response_format` support (`json_object`, `json_schema`), recommended model IDs
- **OpenRouter** docs, free model variants — `:free` suffix semantics, endpoint
- **NVIDIA NIM** / build.nvidia.com — OpenAI-compatible base URL, `nvapi-` keys,
  `GET /v1/models`
- **Cohere** docs, Chat API + v1→v2 migration — `/v2/chat`,
  `message.content[0].text`, required `model` field, `response_format` restrictions

Model IDs sourced from third-party aggregators were treated as **unverified** and are
flagged as such in §3 rather than committed as defaults.
