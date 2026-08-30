# UI/UX Plan
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable

---

## Guest-Facing Surfaces

### 1. Website Chat Widget
- Minimal floating widget, hotel-brandable (logo + primary color)
- Language auto-detected, no language picker required (reduces friction)
- Clear "AI Assistant" labeling — sets expectation, supports trust (per
  PRD risk: "guests distrust AI responses")
- Visible, low-friction path to request a human at any time
- Mobile-first layout — majority of guest traffic will be mobile

### 2. WhatsApp
- Same conversational engine, native WhatsApp UX (no custom UI needed —
  this is a major speed advantage of prioritizing WhatsApp in MVP)
- First message includes a one-line disclosure that this is an AI
  assistant for [Hotel Name]

## Hotel Staff-Facing Surfaces (Dashboard, Next.js)

### Screens (MVP)
1. **Login** — simple email/password, staff vs. owner role
2. **Setup wizard** (first-run only) — guided hotel profile, room types,
   at least 3 policies, at least 5 FAQs; target: under 1 hour to complete
   (US-5 acceptance criteria)
3. **Dashboard home** — today's conversation volume, escalation rate,
   resolution rate, new booking inquiries (US-9)
4. **Conversations** — live list, filter by status; escalated conversations
   surfaced at top with visual urgency indicator; click-through to live
   WebSocket chat view for takeover (US-7)
5. **Knowledge Base editor** — rooms/rates, policies, FAQs, amenities;
   plain-language editing, no technical jargon, Devanagari input supported
   natively in text fields (NFR-6)
6. **Booking Inquiries** — filterable/exportable list with status tracking
   (US-8)
7. **Settings** — hotel profile, WhatsApp number connection status,
   staff accounts

### Design Principles
- Non-technical staff must be able to use every screen without training
  beyond the setup wizard walkthrough (matches target user profile —
  independent guesthouse staff, not IT-trained)
- Devanagari and Romanized Nepali text must render and input correctly
  everywhere, not just in chat (NFR-6)
- Currency always displayed as NPR by default
- Escalation UI must be impossible to miss — this is the highest-stakes
  moment in the product (a guest is about to be lost if staff don't notice)

## Out of Scope for MVP UI
- Native mobile app (dashboard is responsive web, works on staff phones
  via browser)
- Multi-property switcher (single property per tenant account for pilot)
- Advanced analytics visualizations beyond the 3 core metrics in US-9
