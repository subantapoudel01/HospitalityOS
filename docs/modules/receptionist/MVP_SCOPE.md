# MVP Scope
## HospitalityOS — AI Receptionist
Version 1.0 | Stage 1 deliverable

## In Scope (build now)
| Feature | Notes |
|---|---|
| Hotel registration & profile | manual setup, no PMS required |
| Room types & rooms | manual CRUD |
| Hotel policies, amenities, FAQ | manual CRUD, feeds RAG |
| Knowledge base ingestion (RAG, pgvector) | documents + structured fields |
| AI conversation (English) | Gemini-based, function calling + RAG |
| AI conversation (Romanized Nepali) | same engine, language-detected |
| AI conversation (Devanagari Nepali) | same engine, language-detected |
| Booking-inquiry structured extraction | dates, guest count, room pref |
| Availability check | against manual inventory, deterministic |
| Human escalation + live dashboard (WebSocket) | frustration/complexity triggers |
| Website chat widget | embeddable snippet |
| WhatsApp Business API channel | promoted from "later" to MVP |
| Hotel dashboard (staff) | conversations, KB editor, rooms, inquiries, basic analytics |
| Tenant data isolation | hotel_id scoping minimum |
| AI request logging | prompt/model/tokens/latency/cost |
| Continuous LLM eval harness | golden sets, run every build (see QA docs) |

## Explicitly Out of Scope (MVP)
| Feature | Reason / revisit trigger |
|---|---|
| Live PMS / channel-manager sync | most pilot-tier hotels lack modern PMS APIs; revisit once paying hotels' actual PMS mix is known |
| Payment processing (eSewa/Khalti) | knowledge base can *answer* payment questions; processing is v2 |
| Voice AI | out of budget/time for 8-week sprint |
| Native mobile app | dashboard is responsive web only |
| Multi-property enterprise console | single-property-per-tenant is enough for pilot |
| Facebook Messenger / Instagram DMs | WhatsApp proven first; add once validated |
| Full HospitalityOS suite (revenue mgmt, housekeeping, restaurant, accounting, inventory) | post-MVP expansion, see ROADMAP.md |
| Complex tiered billing / metering | flat monthly pricing for pilot |

## Deferred-but-flagged (design so it doesn't block later)
- Schema should allow a hotel to later plug in a PMS feed without a
  redesign (availability table sourced from "manual" OR "pms_sync" flag)
- Channel model should allow adding Instagram/Messenger as new channel
  types without restructuring the conversation/message tables
