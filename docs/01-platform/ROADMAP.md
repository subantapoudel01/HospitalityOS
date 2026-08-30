# Development Roadmap
## HospitalityOS — AI Receptionist MVP and beyond
Version 1.0 | Stage 1 deliverable

See PROJECT_MANAGEMENT.txt for the detailed 7-stage execution plan
(Stages 1-7, ~56 days). This document is the milestone-level view plus
what comes after the pilot.

## MVP Milestones (maps to PROJECT_MANAGEMENT.txt stages)

| Milestone | Stage | Target day |
|---|---|---|
| M0 — Planning frozen (this doc set, v1.0) | 1 | Day 5 |
| M1 — Dev environment live (`docker compose up` works) | 2 | Day 8 |
| M2 — Hotel setup + knowledge base slice live | 3 (Slice A/B) | Day 15 |
| M3 — AI conversation (EN) live with continuous eval | 3/4 (Slice C) | Day 20 |
| M4 — Nepali/Devanagari NLU live | 3/4 (Slice D) | Day 24 |
| M5 — Booking inquiry + availability check live | 3/4 (Slice E) | Day 27 |
| M6 — WhatsApp channel live | 3/4 (Slice F) | Day 30 |
| M7 — Human handoff dashboard live | 3/4 (Slice G) | Day 30 |
| M8 — Pilot hotel(s) onboarded, staging=production | 5 | Day 35 |
| M9 — Pilot running with real guests, metrics tracked | 6 | Day 49 |
| M10 — First paying customer | 7 | Day 56+ |

## Post-MVP (HospitalityOS expansion — not before M10)

Sequenced by what the pilot is most likely to reveal as the next-highest
guest/staff pain point, based on the original "NOT Building Yet" list:

1. **PMS/channel-manager sync** — once you know which PMS your first 3-5
   paying hotels actually use (likely a mix of SajhiloStay/eZee/Sarvanam/
   none), integrate the most common one first, not all of them.
2. **Payment context -> payment processing** — eSewa/Khalti question-
   answering exists in MVP; actual in-chat payment capture is a natural
   next step once booking-inquiry-to-confirmation conversion is measured.
3. **Instagram/Messenger channels** — only after WhatsApp channel metrics
   justify the additional integration and moderation overhead.
4. **Multi-property console** — once a single owner operates 2+ properties
   on the platform (will surface naturally from sales pipeline).
5. **Revenue management, housekeeping, restaurant, accounting, inventory**
   — full HospitalityOS suite, sequenced by direct customer request
   frequency post-launch, not built speculatively.

## Explicit Non-Goals (revisit only if pilot data says otherwise)
- Building for hotel chains / enterprise before proving the independent-
  hotel segment
- Competing head-on with Asksuite/HiJiffy on feature breadth — the wedge
  is local fit, not feature parity
