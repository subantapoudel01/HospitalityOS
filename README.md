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
POST   /api/receptionist/knowledge/sync-policies   pull Slice A policies in
POST   /api/receptionist/knowledge/search          ask a question, see ranked chunks
```

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

Every `/staff/*` endpoint requires an `X-Staff-Token` header matching
`STAFF_API_TOKEN`. **This is a deployment gate, not authentication** — no
per-user identity, no audit trail, and anyone with the token has everything.
It exists because these endpoints expose complete guest transcripts. It
fails closed: an unset token disables the staff API (503) rather than
opening it. Real auth (users, sessions, the UI plan's login screen) is still
outstanding and should replace this before the pilot.

`GET /api/receptionist/conversations/{id}` stays **ungated** because the
guest widget polls its own transcript to see staff replies. That is
capability-based (you need the conversation id) rather than authenticated.

Note: `GET /booking-inquiries` moved under `/staff/` and now requires the
token — a breaking change from Slice E.

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
- **Slice F — WhatsApp channel** is next.

Still outstanding for a complete handoff: automatic escalation (US-4 wants
it triggered by detected frustration or complexity within 1-2 turns; today
it is guest-initiated only), inquiry export (US-8), and real staff accounts.

Now running on Groq (`openai/gpt-oss-120b` for chat, `openai/gpt-oss-20b`
for the fast tier). Measured 440-900ms per call against the Gemini free
tier's 1.9-18.8s, which brings NFR-1's 3s target within reach for the first
time - though a full Nepali turn spends two calls, so measure before
claiming it.
