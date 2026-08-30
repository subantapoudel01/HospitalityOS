# Software Requirements Specification (SRS)
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable

---

## 1. Purpose
Define the functional and non-functional requirements for the AI Receptionist
MVP: a multilingual, embeddable AI front-desk assistant for independent
Nepali hotels that captures booking leads, answers guest questions from a
hotel-specific knowledge base, and escalates to human staff when needed.

## 2. Scope
In scope (MVP): website chat widget, WhatsApp Business channel, hotel
knowledge base (rooms, rates, policies, FAQs, amenities), RAG-grounded
answers, structured booking-inquiry capture, human handoff dashboard,
basic analytics, manual room/rate data entry.

Out of scope (MVP): live PMS/channel-manager sync, payment processing,
voice AI, native mobile app, multi-property enterprise console, Facebook
Messenger/Instagram DMs (deferred until WhatsApp is proven).

## 3. Functional Requirements

FR-1  System shall respond to guest queries in English, Romanized Nepali,
      and Devanagari Nepali, detecting language automatically per message.
FR-2  System shall answer only from the hotel's own ingested knowledge base
      (RAG over pgvector) — no answers from general model knowledge about
      the hotel's specifics (rates, policies, amenities).
FR-3  System shall extract structured booking-inquiry fields (check-in date,
      check-out date, guest count, room-type preference) from free text.
FR-4  System shall check room availability via deterministic function call
      against manually-entered hotel inventory data (not live PMS in MVP).
FR-5  System shall detect frustration, ambiguity beyond its knowledge base,
      or complex negotiation, and escalate to a human staff member via a
      live WebSocket dashboard, preserving full conversation context.
FR-6  System shall support guest conversations via (a) embeddable website
      widget and (b) WhatsApp Business API, with shared conversation state
      where a guest can be identified across channels by phone number.
FR-7  Hotel staff shall be able to create/edit rooms, rates, policies,
      amenities, and FAQ entries through a dashboard without engineering
      support.
FR-8  Hotel staff shall be able to view, filter, and export booking
      inquiries and conversation transcripts.
FR-9  System shall log every AI request (prompt, model, tokens, latency,
      cost) for analytics and eval purposes.
FR-10 System shall support at least 2 concurrent hotel tenants with data
      isolation (hotel A's knowledge base never leaks into hotel B's
      answers).

## 4. Non-Functional Requirements

NFR-1  Response latency: target under 3 seconds end-to-end for a standard
       guest query (model + RAG retrieval + function call combined).
NFR-2  Availability: 99% uptime target during pilot (single-region VPS
       acceptable for MVP; no HA requirement yet).
NFR-3  Data isolation: tenant data segregated at the database-row level
       minimum (hotel_id scoping on every table), row-level security
       preferred.
NFR-4  Cost: inference cost per conversation should stay low enough that
       gross margin holds at the target NPR 8,000-15,000/month price
       point — track cost-per-conversation as a first-class metric.
NFR-5  Model portability: no AI model string hardcoded outside a single
       config/router module, given observed Gemini deprecation cadence
       (see ARCHITECTURE.md, Section on Model Abstraction).
NFR-6  Localization correctness: Nepali date formats, currency (NPR),
       and Devanagari rendering must display correctly in dashboard and
       chat UI.
NFR-7  Auditability: every escalation and every AI answer must be traceable
       to the source knowledge-base chunk(s) used.

## 5. Constraints
- Target hotels likely lack modern PMS APIs — manual data entry is the
  default path, not a fallback.
- WhatsApp Business API verification lead time (1-3 weeks) must be started
  early, not treated as a late-stage integration task.
- Team is small/solo with IT + design/video/3D skills, no prior professional
  IT work experience — architecture choices favor well-documented, widely
  supported tools (FastAPI, Next.js, Postgres) over exotic ones.

## 6. Acceptance Criteria (MVP-level)
See PROJECT_MANAGEMENT.txt "Definition of Done — MVP" for the full gate.
