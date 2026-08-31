# HospitalityOS

A hospitality platform for small hotels. **AI Receptionist** is the first module;
more get added only when work on them actually starts.

The shape of the repo follows from that: one FastAPI app and one Next.js app,
with shared hotel/room/user concepts in a `platform` layer and everything
receptionist-specific in a module beside it.

```
backend/app/core/        config, db session, model_router  (shared by everything)
backend/app/platform/    hotels, users, room_types, rooms, availability, guests
backend/app/modules/     one folder per module — today just receptionist/
```

Dependencies point one way — **modules → platform → core**. Platform code must
never import from a module; the moment it does, modules stop being separable.
See `FOLDER_STRUCTURE.txt` for the full tree and `docs/01-platform/` for design docs.

## Quick start

```bash
make dev
```

Copies `.env.example` to `.env` on first run, then brings up Postgres+pgvector,
Redis, the backend and the frontend via `infra/docker/docker-compose.yml`.

| Service  | URL                            |
|----------|--------------------------------|
| Frontend | http://localhost:3000          |
| API      | http://localhost:8000          |
| API docs | http://localhost:8000/docs     |

Health endpoints:

- `GET /health` — liveness, process is up
- `GET /health/ready` — readiness, actually pings Postgres and Redis (503 if either is down)
- `GET /api/receptionist/status` — confirms the receptionist module is mounted

## Knowledge base (Slice B)

Text is chunked, embedded and stored in pgvector, then retrieved by meaning
rather than keyword. Embeddings run on a local ONNX model by default, so no
API key is needed and nothing is billed per call.

```
POST   /api/receptionist/knowledge/documents       ingest raw text
GET    /api/receptionist/knowledge/documents       list for a hotel
DELETE /api/receptionist/knowledge/documents/{id}  remove doc + chunks
POST   /api/receptionist/knowledge/sync-policies   pull Slice A setup data in
POST   /api/receptionist/knowledge/search          ask a question, see ranked chunks
```

`sync-policies` pulls in **policies and room types** — everything staff
entered at `/setup`, and it now runs automatically on every save. Before
that, a staff member could correct a policy, see "Saved", and have guests
told the old wording indefinitely; nothing in the UI hinted at a second
step. It fires through `app/platform/hooks.py` rather than a direct call,
because platform must never import from a module — the dependency arrow is
what keeps modules separable, and there is a test guarding it. Room types were originally left out, which meant a
rate card was visible in the form and invisible to guests: the assistant
refused to quote a price the hotel had already given it. Syncing from the
same rows keeps `/setup` the single source of truth, so editing a rate and
re-syncing changes what guests are told. Re-running replaces rather than
duplicates.

It also writes a derived **rates overview** naming the cheapest and largest
room. That looks redundant next to the per-room documents and is not:
retrieval matches passages and will not rank across them, so with only
per-room documents "What is the cheapest room you have?" was measured as a
refusal with every rate sitting in the knowledge base. Stating the
comparison beats loosening the grounding instruction.

Saving at `/setup` re-syncs automatically — see **Hybrid retrieval** below
for why that had to go through an event rather than a direct call.

## Hybrid retrieval

Vector search alone is poor at rare literal tokens. Measured on the Rupakot
corpus, before and after:

| Query | Before | After | Top hit before |
|---|---|---|---|
| `Do you take eSewa?` | 0.177 | **0.542** | "How long to allow for each experience" |
| `Do you take Visa?` | 0.240 | **0.603** | "Begnas Lake and Rupa Lake" |
| `Do you take Fonepay?` | — | **0.585** | |
| `Do you take cash?` | 0.397 | 0.699 | Payment policy (already correct) |
| `Do you take Khalti?` | 0.260 | 0.260 | *unchanged — not in the corpus* |

`knowledge_chunks.search_vector` is a **generated** `tsvector` (migration
0009), so Postgres maintains it, no ingestion path can forget it, and the
existing chunks backfilled without re-embedding.

Three decisions worth knowing:

- **Not `plainto_tsquery`.** It ANDs the terms, so `take & esewa` scores
  *zero* against a policy that says "accept". Terms are scored
  independently and summed instead.
- **IDF, not `ts_rank`.** `ts_rank_cd` scores "esewa" the same as "room".
  In this corpus `esewa` is in 1 chunk of 37 and `room` in 8, so terms are
  weighted `ln(1 + N/df)` from the hotel's own corpus.
- **Not normalised per query.** Min-max normalising would make every
  query's best hit 1.0, and `chat_min_score` is an *absolute* threshold —
  it would stop filtering and start admitting the best of a bad set.

```
lexical  = Σ idf(matched terms) / Σ idf(scoring terms)     → [0,1]
combined = 1 − (1 − vector) · (1 − CHAT_LEXICAL_WEIGHT × lexical)
```

The invariant that let this ship without recalibrating anything: **when
`lexical = 0`, `combined = vector` exactly.** Every query the lexical side
cannot help behaves bit-for-bit as before. Lexical evidence only ever
raises a score.

`/knowledge/search` returns `vector_score` and `lexical_score` alongside
`score`, so a wrong answer can be traced to which half caused it.

**What it does not fix:** a brand the hotel never mentioned. `Khalti` has
nothing to match and stays on the weak vector path — correctly refused
rather than answered from a policy that does not list it.

Set `CHAT_HYBRID_RETRIEVAL=false` to fall back to pure vector search; the
scores are compatible, so the floor needs no adjustment either way.

`/search` returns each hit with a similarity score and its source document, so a
wrong answer can be traced back to the chunk that caused it. Try it from
http://localhost:8000/docs.

To switch to hosted Gemini embeddings, set `AI_EMBED_PROVIDER=gemini` and
`GEMINI_API_KEY` in `.env`. Note that Gemini vectors are 3072-dim against the
schema's 384, so that switch needs a new migration and a full re-embed.

## Guest chat (Slice C)

A mobile-first chat widget at http://localhost:3000/widget, backed by a
retrieval-grounded conversation loop.

```
POST /api/receptionist/chat                              send a message
GET  /api/receptionist/conversations/{id}?hotel_id=      full transcript
POST /api/receptionist/conversations/{id}/request-human  escalate to staff
```

How a turn works: retrieve top-k chunks scoped to the hotel, apply a
similarity floor, and only then call a model. A question that clears nothing
gets a fixed refusal and **no model is called at all**. Every turn writes a
`messages` pair plus an `ai_requests` row carrying model, latency, cost and
the chunk ids that produced the answer (NFR-7 traceability).

`AI_CHAT_PROVIDER` selects the generator:

- `extractive` (default) — answers straight from retrieved passages. No key,
  no cost, cannot invent a fact. Blunt, but correct and always available.
- `gemini` / `anthropic` / `openai` — hosted models, needs the matching key.

If a hosted provider fails (rate limit, outage), the turn degrades to
extractive rather than showing the guest an error; `ai_requests.model_used`
records which one actually served. Set `AI_CHAT_FALLBACK=off` to surface
failures as 503 instead.

### Intent routing

Every message is classified BEFORE retrieval, because the answer decides
whether retrieval should happen at all:

- **smalltalk** - greetings, thanks, "how are you", "what is your name".
  The classifier writes the reply itself in the same call. No pgvector query.
- **booking_request** - slot filling (see below).
- **hotel_query** - retrieval, similarity floor, grounded answer.
- **escalation** - the guest wants a human, either by asking directly
  ("contact real person") or by answering yes to a handoff offer. Flips the
  conversation to `escalated` and confirms, exactly as the widget button
  does.

A bare "yes" is read from what the assistant last asked: yes to a handoff
offer escalates, yes to a booking question continues the booking, and yes
after an ordinary answer does neither. Short replies are treated as
continuations, never as new subjects.

**Once escalated, the AI stands down.** Later guest messages are recorded
for staff and acknowledged, but not answered - no retrieval, no model call -
so the AI cannot contradict the human who is about to reply. Normal
behaviour resumes if staff move the conversation back to active or resolved.

The escalation confirmation is fixed text emitted only *after* the status
actually changes. That is deliberate: before this, answering "yes" produced
a model-written "Sure, I'll forward your request to our team" while nothing
was forwarded and the conversation stayed active.

Classification runs on the fast tier (~450-900ms). If the provider is down
or no key is set it falls back to a deterministic phrase list, so a greeting
still gets a greeting rather than an error. Failure defaults to
`hotel_query`: misrouting small talk into retrieval is awkward, misrouting a
real question into small talk is an ungrounded answer.

`intent` in the response is `smalltalk` / `answer` / `refusal` / `booking`.

## Nepali support (Slice D)

The widget accepts English, Romanized Nepali and Devanagari with no language
picker. A Nepali turn runs:

```
detect language  -> Devanagari by Unicode range (free), Latin by Nepali
                    function-word scoring (free), FAST_MODEL only if unsure
translate        -> FAST_MODEL renders the question in English
retrieve         -> search runs on the English text
generate         -> CHAT_MODEL answers directly in the guest's language
```

Translating before retrieval is not cosmetic. Slice B measured a native
Devanagari question scoring 0.17 with its answer outside the top three;
through translation the same question scores 0.52 and answers correctly.

`search_text` in the response shows the English text actually searched, so a
bad answer can be traced to a bad translation rather than guessed at.

Nepali needs `AI_FAST_PROVIDER=gemini`. Without a hosted model the guest
still gets the retrieved information, prefixed by a short Nepali note saying
a staff member can help in Nepali.

**Important caveat.** The similarity floor does not cleanly separate
answerable from unanswerable questions — see the measurement in
`app/core/config.py`. It filters obvious noise; the grounding prompt is what
has to stop plausible-but-unanswerable questions. That second defence is
**unverified** — run `tests/llm_eval/test_chat_gemini.py` to check it.

## Booking requests (Slice E)

Guests describe a trip in their own words; the assistant fills in check-in,
check-out, guest count and room preference across as many turns as it takes,
then saves a `booking_inquiries` row (US-2 - no form).

```
GET /api/receptionist/booking-inquiries?hotel_id=1
```

Slots are re-extracted from the whole transcript every turn rather than
accumulated, so "actually make it the 6th" corrects the record for free.

**The model proposes dates; it does not get the final say.** Extracted values
are validated deterministically before anything is stored: nothing in the
past, check-out after check-in, plausible party size, and a weekday check
that catches "next Friday" resolving to a Wednesday. A rejected value is
cleared and re-asked, never guessed. `raw_request` keeps the guest's own
wording so staff can sanity-check the parse.

Measured extraction on a labelled set: 6/6 check-in dates, 6/6 check-out
dates, 7/7 guest counts, including a mid-conversation correction.

## Staff dashboard (Slice G)

http://localhost:3000/staff — the queue, transcripts, and the handoff.

```
GET   /api/receptionist/staff/metrics?hotel_id=
GET   /api/receptionist/staff/conversations?hotel_id=&status=
PATCH /api/receptionist/staff/conversations/{id}            set status
POST  /api/receptionist/staff/conversations/{id}/messages   staff reply
GET   /api/receptionist/staff/booking-inquiries?hotel_id=&status=
PATCH /api/receptionist/staff/booking-inquiries/{id}        set status
```

Escalated conversations are pinned to the top and marked **waiting for
staff** until a human actually replies. Staff can reply in the thread
(`sender='staff'`); the guest widget polls and shows it as a staff message,
so the handoff is visible on both sides. Marking a conversation resolved
records `resolved_at`; reopening clears it.

Both the dashboard and the widget poll every 5s and **pause while the tab is
hidden**. This deviates from `PROJECT_MANAGEMENT.txt`, which specifies
WebSocket — polling was chosen deliberately for pilot scale.

### Access

Real staff accounts. Sign in at http://localhost:3000/staff/login.

```
POST /api/auth/login    email + password -> JWT (also set as a cookie)
POST /api/auth/logout   clears the cookie
GET  /api/auth/me       who the server thinks you are
```

Passwords are bcrypt (12 rounds, salted per hash). The session is an HS256
JWT carrying `user_id`, `role` and **`hotel_id`** — which is what finally
makes NFR-3 enforceable: a staff member requesting another property's
conversations gets a 404, not their data.

```bash
make jwt-secret     # a signing secret for .env
```

```bash
make seed-admin     # the first account; prints its password once
```

Both **fail closed**. An unset `JWT_SECRET` disables login (503) rather
than falling back to a built-in default, because a shipped default signing
key is forgeable by anyone who can read this repo. There is no default
password anywhere in the seed script either.

`/staff/*` routes are also guarded by `frontend/middleware.ts`, which
redirects a signed-out visitor to the login page. That is a **redirect, not
the security boundary** — it checks the cookie's expiry without verifying
the signature, because that would mean putting `JWT_SECRET` in the frontend
container. The API verifies on every request.

`STAFF_API_TOKEN` still works and is **superseded**. It has no identity and
grants cross-tenant access, so a JWT always wins when both are present.
Leave it empty in production.

`GET /api/receptionist/conversations/{id}` stays **ungated** because the
guest widget polls its own transcript to see staff replies. That is
capability-based (you need the conversation id) rather than authenticated.

Honest limits: the session cookie is readable by JavaScript (the UI and API
are separate origins in dev), and logout clears the cookie but cannot
invalidate a token already copied elsewhere — rotate `JWT_SECRET` to cut
every session at once. Both are documented at the top of
`backend/app/platform/api/auth_routes.py`.

Note: `GET /booking-inquiries` moved under `/staff/` and now requires the
token — a breaking change from Slice E.

### Export (US-8)

```
GET /api/receptionist/staff/booking-inquiries.csv?hotel_id=1&status=new
```

Honours the same status filter as the list, so "export what I am looking
at" does. UTF-8 **with a BOM**, because without it Excel on Windows renders
Devanagari as mojibake — and Windows Excel is where these files are opened.
Includes the guest's own wording, so a misparsed date is visible to whoever
acts on it; a message starting `=` is prefixed so Excel treats it as text
rather than a formula.

### Automatic escalation (US-4)

Two independent triggers, both deterministic — no model call, because the
guest whose questions are failing during a rate limit is exactly the guest
who needs a person:

- **Stated frustration** — "this is useless", "you keep saying". Fires that
  turn. Matched as *phrases*, never bare words: "the umbrella was useless"
  is not a complaint about the assistant.
- **Dead end** — three consecutive refusals, counting the one about to be
  sent, so the guest is handed over *instead of* being refused a third
  time. Catches the polite guest who would otherwise just close the tab.

Why three and not two: two unanswerable questions in a row is unremarkable.
Every false escalation interrupts a real person, and staff pulled into
three non-conversations stop trusting the queue.

`conversations.escalation_trigger` / `escalation_reason` record why, and
the dashboard shows it in the queue — "the guest asked for a person" and
"the AI decided it was failing" need different opening lines. NULL means
the guest asked. Turn it off with `CHAT_AUTO_ESCALATE=false`, or disable
just the dead-end arm with `CHAT_DEAD_END_TURNS=0`.

**This required a fix to the grounding prompt.** The model was told to
decline *in its own words*, so with Groq generating, a refusal was reported
as `intent=answer` and matched nothing. Measured live: three unanswerable
questions produced `refusal, answer, answer` and never escalated. The same
gap silently broke "yes" → escalate, which also matches the fixed strings.
The prompt now pins the exact refusal wording.

## When the AI provider fails

Every model call has a deterministic fallback, so the system keeps answering
rather than erroring. That is deliberate, and it used to be **invisible**:
booking requests quietly became refusals and Nepali questions were searched
untranslated, with nothing anywhere saying so.

Fallbacks now write `ai_requests.degraded_from` naming the provider that
failed, and the staff dashboard shows a banner:

```
GET /api/receptionist/staff/degradation?hotel_id=1&minutes=15
→ { degraded, events, last_at, providers, by_purpose }
```

Any single fallback in the window raises the banner. During light traffic
one degraded turn may be the only guest of the hour, and waiting for a rate
to look bad would hide exactly the case worth seeing.

**Deliberately running keyless is not degradation.** With
`AI_CHAT_PROVIDER=extractive` and no fast tier, the system is working as
configured, `degraded_from` stays NULL and no banner appears. A permanent
alarm is one staff would learn to ignore.

What the banner means in practice:

| Purpose | Effect on guests |
|---|---|
| `classification` | Routing falls back to keyword rules — booking requests can come back as refusals |
| `translation` | Nepali searched untranslated (Slice B measured 0.17 vs 0.52 retrieval) |
| `chat` | Replies are retrieved passages verbatim rather than written |

On the Groq free tier the usual cause is the 200,000 tokens/day cap. Rotate
`GROQ_API_KEY` and restart the backend, or wait for the daily reset.

## Other commands

```bash
make down
```

```bash
make test
```

Runs the retrieval eval (`backend/tests/llm_eval/`) against real Postgres and the
real embedding model — nothing is mocked, because a mocked embedding only proves
the SQL compiles. It reports recall@3 and top-1 accuracy across English,
Devanagari and Romanized Nepali, and prints any case listed in `KNOWN_GAPS`.

## Where things stand

Stage 3, vertical slices — see `PROJECT_MANAGEMENT.txt` for the full plan.

- **Slice A — Hotel setup** (done): staff enter the property profile, room types
  and rates, and policies at `/setup`. Stored in Postgres via `app/platform/`.
- **Slice B — Knowledge base** (done): text is chunked, embedded and retrieved
  from pgvector, with an eval harness covering English and Nepali.
- **Slice C — Chat (English)** (done): guest widget at `/widget`, grounded
  conversation loop, transcripts and per-call cost telemetry.
- **Slice D — Nepali support** (done): language detection, translate-before-
  retrieval, replies in the guest's language, plus deterministic small talk.
- **Slice E — Booking request collection** (done): natural-language slot
  filling into `booking_inquiries`, with deterministic date validation.
- **Human handoff + staff dashboard** (done): staff queue at `/staff`,
  transcripts, status controls, staff replies reaching the guest widget.
- **Staff accounts** (done): bcrypt passwords, JWT sessions scoped to one
  property, a login screen and route middleware. Replaces the shared token.
- **Automatic escalation, US-4** (done): stated frustration, or three dead
  ends in a row.
- **Inquiry export, US-8** (done): CSV from the dashboard.
- **Deployment** (ready, not deployed): production compose with Traefik and
  Let's Encrypt, non-root images, server bootstrap and backup scripts, and
  a runbook at `docs/01-platform/DEPLOYMENT.md`. Provisioning the VPS and
  pointing DNS need your accounts.
- **CI** (done): `.github/workflows/ci.yml` runs the suite against real
  Postgres+pgvector on every push, plus migration reversibility, schema
  drift and a committed-secret check. See `infra/ci/README.md`.
- **Slice F — WhatsApp channel** is next. Meta business verification takes
  1-3 weeks, so start that paperwork before writing the handler.

Still outstanding: the staff availability calendar (Milestone 5), which
needs a room-inventory model that does not exist yet, and per-role
permissions — `manager` and `staff` are currently identical to `admin`.

Now running on Groq (`openai/gpt-oss-120b` for chat, `openai/gpt-oss-20b`
for the fast tier). Measured 440-900ms per call against the Gemini free
tier's 1.9-18.8s, which brings NFR-1's 3s target within reach for the first
time - though a full Nepali turn spends two calls, so measure before
claiming it.
