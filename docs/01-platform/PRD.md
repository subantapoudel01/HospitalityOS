# Product Requirements Document (PRD)
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable

---

## 1. Problem
Independent Nepali hotels are experiencing a tourism boom (Nepal Tourism
Board: H1 2026 arrivals up ~7.4% YoY, June 2026 up ~22% vs. pre-COVID June
2019) without matching growth in trained front-desk staff. Repetitive
questions (rates, WiFi, check-in times, availability) consume staff time
that should go to bookings and guest service, and after-hours/website
inquiries go unanswered, pushing guests to commission-charging OTAs.

## 2. Target User
Primary: front-desk staff / owner-operators at independent guesthouses and
boutique hotels (10-60 rooms) in Pokhara and Kathmandu, currently using
WhatsApp, phone, and/or a local PMS (SajhiloStay, Webbook, eZee, Sarvanam)
or no digital system at all.

Secondary (guest-facing): international and domestic travelers messaging
the hotel via website or WhatsApp, in English, Romanized Nepali, or
Devanagari.

## 3. Product Principles
1. Hospitality-specific, not a generic chatbot — structured rooms/rates/
   booking-inquiry data, not just FAQ matching.
2. Local-first channel — WhatsApp is co-equal with the website widget from
   day one, not an afterthought.
3. No-PMS-required onboarding — a hotel with zero existing software can be
   live within an hour of manual data entry.
4. Zero hallucination on hotel-specific facts — RAG-grounded answers only,
   with escalation as the fallback when the knowledge base doesn't cover it.
5. Priced for an independent Nepali hotel's budget, not a US chain's.

## 4. Competitive Positioning (from market scan)
Global incumbents (Asksuite, HiJiffy, Canary, Viqal, Quicktext, TrustYou,
Guestivo, roommaster) are mature, multilingual, PMS-integrated, and priced
$100-300+/month — out of reach and over-built for a 15-room Pokhara
guesthouse, and none are tuned for Nepali language or local payment/PMS
context. A local competitor (Springbase AI) sells generic Nepali-language
business chatbots but without hospitality-specific structured data
(rooms, rates, booking-inquiry extraction). HospitalityOS's wedge is the
intersection: hospitality-specific + Nepali-first + WhatsApp-first +
no-PMS-required, priced below global incumbents and above generic local
chatbots.

## 5. MVP Feature List (see MVP_SCOPE.md for full in/out table)
- Multilingual AI conversation (EN / Romanized Nepali / Devanagari)
- RAG-grounded hotel knowledge base (rooms, rates, policies, FAQ, amenities)
- Structured booking-inquiry capture
- Availability check against manually-entered inventory
- Human handoff via live dashboard
- Website widget + WhatsApp Business channel
- Hotel staff self-serve dashboard (knowledge base, rooms, inquiries)
- Basic analytics (conversation volume, escalation rate, resolution rate)

## 6. Success Metrics (Pilot)
- Primary: 60%+ reduction in repetitive front-desk queries reaching staff
- Secondary: escalation precision/recall, guest satisfaction (post-chat
  rating), booking-inquiry-to-confirmed-booking conversion, cost per
  conversation
- Business: pilot hotel converts to paying customer at end of Stage 6

## 7. Business Model
- Monthly SaaS subscription, tiered by message volume/feature access
- One-time onboarding/setup fee for knowledge-base configuration
- Price anchor: NPR 8,000-15,000/month (above generic local chatbots,
  below global hospitality incumbents' entry tiers)

## 8. Risks (product-level)
- Guests expect a human and distrust AI responses -> mitigate with clear
  "AI assistant" framing plus fast, visible escalation option
- Hotel staff resist maintaining their own knowledge base -> mitigate with
  a fast onboarding flow and templates (see knowledge_base_templates/)
- Nepali NLU quality gaps vs. English -> mitigate with a dedicated
  multilingual golden eval set from Stage 1 (see USER_STORIES.md, QA plan)
