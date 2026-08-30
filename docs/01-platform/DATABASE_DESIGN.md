# Database Design
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable
Engine: PostgreSQL + pgvector extension

---

## Core Tables (MVP)

```
hotels
  id (pk), name, description, city, address, phone, whatsapp_number,
  currency (default 'NPR'), timezone, created_at
  -- `description` added in Slice A: staff need somewhere to describe the
  -- property in the setup screen, and it doubles as RAG source material.

users            -- hotel staff accounts
  id (pk), hotel_id (fk), email, password_hash, role
  ('owner'|'staff'), created_at

room_types
  id (pk), hotel_id (fk), name, description, base_rate,
  max_occupancy, amenities_json

rooms
  id (pk), hotel_id (fk), room_type_id (fk), room_number,
  status ('active'|'maintenance'|'inactive')

availability          -- MANUAL inventory for MVP; pms_synced flag
  id (pk), room_type_id (fk), date, rooms_available,
  rate_override, source ('manual'|'pms_sync')   -- future-proofs PMS later

hotel_policies
  id (pk), hotel_id (fk), category
  ('checkin_checkout'|'cancellation'|'pets'|'payment'|'other'),
  content_text, updated_at

faqs
  id (pk), hotel_id (fk), question, answer, language
  ('en'|'ne_romanized'|'ne_devanagari'|'auto'), updated_at

knowledge_documents      -- source docs before chunking
  id (pk), hotel_id (fk), title, source_type
  ('faq'|'policy'|'amenity'|'upload'), raw_content, created_at

knowledge_chunks         -- RAG unit, one row per embedded chunk
  id (pk), knowledge_document_id (fk), hotel_id (fk),
  chunk_text, embedding (vector(384)), token_count, created_at
  -- Slice B ships vector(384), NOT the 3072 originally planned: the pilot
  -- embeds with a local ONNX model (paraphrase-multilingual-MiniLM-L12-v2)
  -- so the knowledge base needs no API key and costs nothing per call.
  -- Switching to gemini-embedding-001 (3072) needs a NEW MIGRATION plus a
  -- full re-embed of every stored chunk - the model router abstracts the
  -- provider, it cannot abstract the column width.
  -- No ANN index: pgvector cannot index past 2000 dimensions, and at pilot
  -- scale exact sequential scan is both faster to reason about and exact.
  -- hotel_id duplicated here (denormalized) so RAG queries filter
  -- WITHOUT a join, for latency (NFR-1) and to make tenant isolation
  -- (NFR-3) enforceable directly on this table

guests
  id (pk), phone, name, preferred_language, created_at

conversations
  id (pk), hotel_id (fk), guest_id (fk), channel
  ('website'|'whatsapp'), status
  ('active'|'escalated'|'resolved'), started_at, resolved_at

messages
  id (pk), conversation_id (fk), sender ('guest'|'ai'|'staff'),
  content, language_detected, created_at

booking_inquiries
  id (pk), conversation_id (fk), hotel_id (fk), guest_id (fk),
  check_in_date, check_out_date, guest_count,
  room_type_preference, status
  ('new'|'contacted'|'confirmed'|'lost'), raw_request, created_at
  -- raw_request added in Slice E: the guest's own wording. Extraction is a
  -- model output and can be subtly wrong, and staff act on these rows, so
  -- they need the original text to check a date against.
  -- DB-level CHECK constraints enforce check_out_date > check_in_date and
  -- guest_count >= 1, so no caller can store an impossible stay whatever a
  -- model produced.

ai_requests               -- FR-9 / NFR-4 cost tracking
  id (pk), conversation_id (fk), model_used, purpose
  ('chat'|'classification'|'embedding'), prompt_tokens,
  completion_tokens, latency_ms, cost_estimate,
  retrieved_chunk_ids, created_at
  -- retrieved_chunk_ids added in Slice C (the column this doc's design
  -- note already anticipated): NFR-7 requires every AI answer to be
  -- traceable to the knowledge that produced it, and storing the ids at
  -- answer time is the only point where that mapping is known.
  -- model_used also records non-model turns as 'deterministic-refusal',
  -- so refusal rate is queryable next to real calls.
  -- cost_estimate is NULL when no price is configured, never a guess.
  -- degraded_from (added later) names the provider that failed when a turn
  -- ran on deterministic rules instead of a model. NULL means the turn ran
  -- normally, INCLUDING deliberately keyless setups - those are configured
  -- behaviour, not failure, and counting them would make the dashboard
  -- warning permanent and therefore ignored.

audit_logs
  id (pk), hotel_id (fk), actor_type ('staff'|'system'),
  actor_id, action, target_table, target_id, created_at
```

## Design Notes

- **hotel_id everywhere**: minimum viable tenant isolation (NFR-3);
  `knowledge_chunks.hotel_id` is intentionally denormalized for RAG query
  performance and as an isolation safety net.
- **`availability.source`**: lets MVP run fully on manual data while
  leaving a clean seam to plug in PMS sync later (per MVP_SCOPE.md
  "deferred but flagged" note) without a schema rewrite.
- **`ai_requests`**: exists specifically to make NFR-4 (cost per
  conversation) and the continuous LLM eval harness (Stage 4) queryable
  from day one, not bolted on later.
- **`knowledge_chunks.embedding vector(3072)`**: matches
  `gemini-embedding-001` output dimensionality (see ARCHITECTURE.md);
  Matryoshka truncation can reduce this later for storage/cost if needed
  without re-architecting, only re-embedding.
- **Escalation traceability (NFR-7)**: `messages` + `knowledge_chunks`
  together let you reconstruct exactly which knowledge fed any AI answer,
  via the RAG retrieval log recorded per `ai_requests` row (extend with a
  `retrieved_chunk_ids` array column if deeper audit trails are needed
  post-MVP).
