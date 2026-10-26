# ResumeMatch Coach

A multi-agent GenAI application that scores a resume against a job description,
explains the gap, and rewrites the weak bullet points to close it.

**The engineering claim: this app cannot show an error page.** Four LLM providers
are tried in order; if all four are unreachable it falls back to a deterministic
keyword analyzer and says so plainly in the UI.

```
┌──────────────────────────────────────────────────────────────────────┐
│  React 18 + Vite SPA  ·  the Coach reacts to your score              │
│  (built to dist/ and served by FastAPI - or `npm run dev` on :5173)  │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ fetch()
┌────────────────────────────────▼─────────────────────────────────────┐
│  FastAPI  127.0.0.1:8000                                             │
│  /analyze  /history  /analysis/{id}  /compare  /extract              │
│  /providers/status  /providers/logs  /health  /sample                │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
      ┌──────────────────┐               ┌──────────────────┐
      │  CACHE           │   hit ──────▶ │  SQLite          │
      │  SHA-256 of      │   instant     │                  │
      │  (resume+job)    │               │  resumes         │
      └────────┬─────────┘               │  job_descriptions│
               │ miss                    │  analyses        │
               ▼                          │  provider_logs   │
      ┌──────────────────┐  writes ─────▶ └──────────────────┘
      │  ORCHESTRATOR    │
      │  1. Analyst      │
      │  2. Rewriter     │
      │  3. merge+save   │
      └────────┬─────────┘
       ┌───────┴───────┐
       ▼               ▼
  ┌─────────┐    ┌──────────┐    each: system prompt + JSON contract
  │ ANALYST │    │ REWRITER │          + Pydantic validation
  │  AGENT  │    │  AGENT   │
  └────┬────┘    └────┬─────┘
       └───────┬───────┘
               ▼
  ┌─────────────────────────────────────────────────────┐
  │  PROVIDER ROUTER                                    │
  │  ordered failover + circuit breaker + logging       │
  └──┬──────────┬──────────┬───────────┬────────────────┘
     ▼          ▼          ▼           ▼
  ┌──────┐  ┌──────┐  ┌──────────┐  ┌────────────┐
  │NVIDIA│─▶│Cohere│─▶│OpenRouter│─▶│HuggingFace │
  └──────┘  └──────┘  └──────────┘  └─────┬──────┘
                                           │ all four fail
                                           ▼
                        ┌──────────────────────────────────┐
                        │  LOCAL ANALYZER (no AI, no net)   │
                        │  keyword overlap → score + gaps   │
                        │  source = "local_fallback"        │
                        └──────────────────────────────────┘
```

---

## Quick start

```powershell
# Windows
.\run.ps1
```
```bash
# macOS / Linux
./run.sh
```

Then open **http://127.0.0.1:8000**.

The script creates the virtualenv, installs dependencies, and copies
`.env.example` to `.env` on first run. **It works with zero API keys** — you
just get keyword analysis instead of AI, clearly labelled.

Manual setup, if you prefer:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
cp .env.example .env                                      # add keys
.venv/Scripts/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

---

## Features

- **Drop a resume anywhere on the page.** Pip runs over, catches it, reads it,
  and tells you what to do next. PDF or TXT, extracted server-side.
- **Match score 0–100** with a skills / experience / keywords breakdown
- **Matched and missing skills**, ranked by position in the posting (i.e. by how
  much the employer actually cares) rather than alphabetically
- **Rewritten bullet points**, before / after / why, that never invent metrics
- **Tailored resume PDF** — your own resume with the improved bullets swapped in
- **Job matches** — roles your resume fits, each with live searches on
  LinkedIn, Indeed, Naukri (India) and Google Jobs. Works from the resume alone;
  no job description needed. **Pip never shows a fabricated posting** — every
  link is a search, so every result is a real listing.
- **Chat with Pip** — a resume coach grounded in *your* resume, score and gaps.
  Ask what to fix, how to phrase a bullet, or which jobs fit. Answers offline
  from your analysis when every AI provider is down.
- **Pip walks the page** — she patrols the floor, wanders over to stand on
  cards, walks to your score when it arrives, gives a guided tour, and stands
  on the chat panel while you talk. Seven expressions, nine poses.
- **History, Compare and System status** — score deltas between resume
  versions, and per-provider health with the raw attempt log
- **Markdown / JSON export** of any result

## Getting the free API keys

Every provider is optional, and any provider without a key is skipped cleanly.

| Provider | Where | Notes |
|---|---|---|
| **NVIDIA NIM** | [build.nvidia.com](https://build.nvidia.com) | Join the Developer Program; keys start `nvapi-`. Fastest in our testing. |
| **Cohere** | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) | Trial keys include free monthly credits. |
| **OpenRouter** | [openrouter.ai/keys](https://openrouter.ai/keys) | Use a model ID ending in `:free`. Tight rate limits. |
| **Hugging Face** | [Create a token](https://huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained) | **Must be fine-grained with "Make calls to Inference Providers"** — a repo-scoped token returns 403. See [HUGGINGFACE.md](./HUGGINGFACE.md). |

Verify whatever you have:

```bash
python scripts/check_providers.py            # PASS/FAIL table per provider
python scripts/check_providers.py --discover  # also list OpenRouter free models
```

It never prints a key. Free-tier model IDs rotate constantly, so run this first
whenever something stops working.

---

## The failover design

This is the part worth reading.

### Provider order is evidence-based

`PROVIDER_ORDER` was set by **benchmarking the real agent prompt**, not by
preference:

| Provider | Model | Latency | Outcome |
|---|---|---|---|
| NVIDIA | `minimaxai/minimax-m3.1-flash` | **3.1 s** | clean JSON, first try |
| Cohere | `command-r-08-2024` | 7.0 s | clean JSON |
| OpenRouter | `google/gemma-4-31b-it:free` | 1.8 s | works, but 429s quickly |
| Hugging Face | `openai/gpt-oss-120b` | — | 403 until the token is re-scoped |

### Error taxonomy

The router distinguishes "this will never work" from "try once more", because
retrying the wrong one burns a daily quota:

| Condition | Action | Why |
|---|---|---|
| `429` rate limit | next provider, **no retry** | retrying spends the quota we are protecting |
| `401` / `403` / `402` | next provider, no retry | configuration, not weather |
| `404` / `410` bad model | next **model** on the same provider | one stale ID should not cost a whole provider |
| `400` / `422` | degrade the payload and retry | drop optional params, then JSON mode |
| `5xx`, timeout, network | 1 retry, then next provider | often transient |
| **HTTP 200, empty content** | 1 retry, then next provider | real: reasoning models burn the budget before answering |
| unparseable JSON | 1 stricter retry, then next | handled in the agent layer |

### Circuit breaker

Two consecutive failures bench a provider for 60 s, so one provider having a bad
minute does not slow every later request. In-memory on purpose: a restart gives
everyone a fresh chance.

### Caching

`input_hash = sha256(normalised_resume + normalised_job + "v1")`, which is also
the `UNIQUE` key on `analyses` — so the cache and the dedupe rule are the same
mechanism and cannot disagree. Repeat analyses return in ~4 ms and spend no
quota. "Force re-analyze" bypasses it.

### Why job suggestions never show a posting

Asked for "jobs for this resume", a model will happily write *"Senior Engineer
at Acme — apply here"*. A candidate who applies to a company that does not
exist has been harmed, not helped. So the Jobs agent's contract is narrower:
name realistic **role titles** and why they fit. The backend then builds live
search URLs on real job boards for each role. A test asserts that a
model-supplied URL can never reach the user, and another that every link's
hostname is a known board.

### Three real bugs this design caught

Found by probing the live APIs before writing the provider classes:

1. **Cohere's `content[0]` is not the answer.** `command-a-plus-05-2026` returns
   `[{type:"thinking"}, {type:"text"}]`, so the docs' `content[0].text` example
   silently parses the chain of thought. Filter by `type == "text"`.
2. **Reasoning models return `content: null`.** NVIDIA's nemotron returned
   HTTP 200 with no content at `max_tokens=600`, having spent 976 tokens
   reasoning. Hence `MAX_OUTPUT_TOKENS=3000` and the `EmptyResponse` class.
3. **A model catalog is not an entitlement list.** NVIDIA's `/v1/models` lists
   84 models; only 3 of 33 plausible ones actually ran on the free tier, and
   `llama-3.1-8b` is hard-410 end-of-life.

---

## The agents

| Agent | Contract | Key rule |
|---|---|---|
| **Analyst** | `overall_score`, `score_breakdown`, `matched_skills`, `missing_skills`, `summary` | Never credit a skill the resume does not mention |
| **Rewriter** | `rewritten_bullets[{original, improved, reason}]`, `suggestions` | **Never fabricate numbers, employers or credentials.** Unknown metrics become `[X]%` placeholders for you to fill in |
| **Jobs** | `location`, `roles[{title, seniority, match, why, keywords}]` | Names *roles*, never postings. Links are built by the backend — any URL the model emits is discarded |
| **Pip (chat)** | plain text | Grounded in the resume and analysis, which are passed as *data, not instructions*. Refuses to name "companies hiring now" |

Both return strict JSON, validated with Pydantic. Malformed output goes through
a five-stage parsing ladder (raw → de-fence → balanced-brace extraction →
repair → one stricter retry) before the router moves on.

Degradation is independent at each stage:

- **Rewriter fails** → `partial: true`. Score and gaps still shown.
- **Analyst fails** → `source: "local_fallback"`. Keyword analysis, labelled.

---

## Testing

```bash
.venv/Scripts/python -m pytest -q     # 108 tests
```

All providers are mocked. **No test makes a network call or spends API quota.**
Coverage includes failover order, non-retry of terminal errors, exactly-one
retry of transient ones, in-provider model fallback, circuit-breaker bench and
expiry, total-failure degradation, the parsing ladder (nested braces, braces
inside strings, escaped quotes, smart quotes, trailing commas), analyzer
determinism, and the full API surface.

---

## Security and local-only operation

- Keys live **only** in `.env`, which is gitignored. Never in code, logs,
  `provider_logs`, or any API response. `/providers/status` returns
  `configured: true|false` and nothing else.
- `Settings.__repr__` masks every secret, so `print(settings)` is safe.
- Binds to **127.0.0.1 only**. Not `0.0.0.0` — that would expose your analysis
  history to every device on your network.
- Outbound traffic goes only to the provider APIs you configured.
- Parameterised SQL throughout. Uploads are parsed in memory, never written to
  disk.
- No Docker, no CI config, no deploy target. Nothing here can publish itself.

Full detail in [DESIGN.md §17](./DESIGN.md).

---

## Known limitations

1. **Keyword-weighted scoring.** Even with an LLM, matching leans on
   terminology. A strong candidate who words things differently can score low.
2. **Free-tier models disagree.** The same resume/job pair scored 38, 50 and 65
   on three different providers. Treat the score as directional, not absolute.
3. **Free-tier limits are real.** ~20 req/min with tight daily caps. The cache
   and circuit breaker exist because of this.
4. **Model IDs drift.** Free slugs get deprecated without notice;
   `check_providers.py` is the mitigation, not a cure.
5. **PDF extraction is text-only.** Multi-column and scanned resumes extract
   poorly; there is no OCR. Paste the text instead.
6. **In-memory circuit breaker.** Single-process only; state resets on restart.
7. **No auth, single user, local only.** Anyone with the URL sees all history.
8. **Rewrites need human review.** The prompt forbids fabrication and uses
   `[X]` placeholders, but LLMs drift. Verify before sending.

---

## Project layout

```
backend/
  main.py              FastAPI app, 15 routes, serves the UI
  config.py            env loading, secret masking
  db.py                SQLite schema + queries (parameterised only)
  cache.py             SHA-256 content cache
  extract.py           PDF/TXT → text (in memory)
  models.py            Pydantic contracts
  agents/
    base.py            prompt → router → parse → validate → 1 retry
    parsing.py         five-stage JSON extraction ladder
    analyst.py         scoring agent + system prompt
    rewriter.py        rewriting agent + system prompt
    orchestrator.py    pipeline, degradation, persistence
  providers/
    base.py            Provider ABC + error taxonomy
    nvidia.py  cohere.py  openrouter.py  huggingface.py
    router.py          failover + circuit breaker + logging
  fallback/
    local_analyzer.py  deterministic keyword scoring
frontend-react/        React 18 + Vite UI
  src/App.jsx          tabs, state, the analyze flow
  src/api.js           fetch client; errors become readable sentences
  src/components/
    Coach.jsx          the 2D character - 7 moods, driven by one moodFor()
    ScoreRing.jsx      animated 270-degree dial that counts up
    Result.jsx         score, metrics, skills, bullets, exports
    Panels.jsx         History / Compare / System status
  src/styles.css       design tokens + the Coach's keyframes
frontend/index.html    dependency-free fallback UI (no Node required)
scripts/check_providers.py
tests/                 108 tests, zero network
DESIGN.md  PHASES.md  HUGGINGFACE.md
```

Stack: FastAPI · SQLite3 (stdlib, no ORM) · httpx · pypdf · Pydantic ·
React 18 + Vite. Seven Python dependencies (all pure-Python wheels) and four
npm packages. No Docker, no ORM, no state library, no CSS framework.
