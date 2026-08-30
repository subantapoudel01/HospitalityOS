# System Architecture
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable

---

## 1. High-Level Diagram

```
                         Guest
              (Website Widget)   (WhatsApp)
                     \              /
                      v            v
                 Channel Adapter Layer
                 (normalizes both channels
                  into one internal message
                  format)
                          |
                          v
                    FastAPI Backend
                          |
                          v
                   AI Orchestrator
              (system prompt + conversation
               state + tool router)
                 /        |         \
                v         v          v
           Model Router  RAG        Function-Calling
           (see Sec. 3)  Retrieval  Tools (availability,
                          |         booking-inquiry write)
                          v
                     pgvector (PostgreSQL)
                          |
                          v
                 Hotel Knowledge Base
                 (rooms, rates, policies,
                  FAQs, amenities — scoped
                  per hotel_id)

                          ^
                          |
                        Redis
             (session/conversation cache,
              rate limiting, job queue)

Escalation path:
   AI Orchestrator --(frustration/complexity detected)--> WebSocket -->
   Staff Dashboard (Next.js) --> Human takes over, full context preserved
```

## 2. Component Responsibilities

- **Channel Adapter Layer**: converts WhatsApp Business API webhooks and
  website widget WebSocket/HTTP messages into one internal `Message` shape.
  New channels (Instagram, Messenger) plug in here later without touching
  the orchestrator.
- **AI Orchestrator**: owns conversation state, decides when to call RAG,
  when to call a function/tool, when to escalate. Language detection runs
  here before generation.
- **Model Router** (see Section 3): the ONLY place a model string appears.
- **RAG Retrieval**: pgvector similarity search scoped by `hotel_id`,
  returns top-k chunks with source references for auditability (NFR-7).
- **Function-Calling Tools**: deterministic operations — check availability
  (reads manual inventory table), write booking-inquiry record. Never
  hallucinated; always a real DB read/write.
- **Staff Dashboard**: Next.js app, WebSocket-connected for live handoff,
  plus standard CRUD screens for knowledge base/rooms/inquiries.

## 3. Model Abstraction (critical — addresses NFR-5)

Given Google's observed Gemini deprecation cadence (multiple models
retired or scheduled for retirement within months of release through
2026), **no model string may be hardcoded anywhere except one router
module**: `backend/app/core/model_router.py`.

```python
# backend/app/core/model_router.py  (illustrative shape, not final code)
CHAT_MODEL = settings.AI_CHAT_MODEL       # env-configurable
FAST_MODEL = settings.AI_FAST_MODEL       # env-configurable
EMBED_MODEL = settings.AI_EMBED_MODEL     # env-configurable
```

Every other module calls `model_router.chat(...)`, `model_router.
embed(...)`, never the raw API client with a literal model name. A CI test
asserts no other file contains a `gemini-` string literal.

## 4. Recommended Models (as of Aug 2026 — re-verify at build time)

Check `ai.google.dev/gemini-api/docs/deprecations` before implementation;
treat the table below as a starting point, not a permanent choice.

| Purpose | Recommended model | Why |
|---|---|---|
| Main guest-facing conversation | `gemini-3.6-flash` (or current GA Flash) | Best balance of quality/latency/cost for multilingual conversational RAG; current stable GA tier as of Aug 2026 |
| High-volume subagent tasks (language detection, escalation-trigger classification, intent pre-routing) | `gemini-3.5-flash-lite` (or current GA Flash-Lite) | Lower latency/cost for narrow, high-frequency classification tasks — matches the original "dual-engine" idea from the investor summary, just with current model names |
| Embeddings for RAG | `gemini-embedding-001` | GA, text-only, supports 100+ languages (important for EN/Nepali/Devanagari), 3072-dim vectors truncatable via Matryoshka for storage efficiency; confirm still GA at build time — Google has signaled eventual migration toward multimodal `gemini-embedding-2` |
| Fallback / cost-emergency tier | keep `FAST_MODEL` config swappable to a Flash-Lite variant if the primary Flash tier's cost/latency ever regresses the NFR-4 margin target | |

Do not use `gemini-1.5-*` or `gemini-2.0-*` — both are shut down as of
mid-2026. Do not hardcode any of the above outside the router module.

## 5. Data Isolation

Every table with hotel-specific data carries a `hotel_id` foreign key.
MVP minimum: application-layer scoping on every query. Stretch goal for
MVP: PostgreSQL row-level security (RLS) policies as a defense-in-depth
layer, given this is genuinely multi-tenant SaaS from day one.

## 6. Deployment Topology (MVP)

Single VPS (or small managed cluster), Docker Compose for pilot scale —
no need for Kubernetes at 1-2 tenant pilot scale. Staging IS production
for the pilot hotels (see PROJECT_MANAGEMENT.txt, Stage 5). Revisit
topology only once paying-customer count justifies the operational
overhead of a more elaborate setup.
