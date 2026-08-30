# User Stories
## HospitalityOS — AI Receptionist MVP
Version 1.0 | Stage 1 deliverable

Format: As a [role], I want [capability], so that [benefit]. Includes
acceptance criteria and maps to the vertical slice (Stage 3) that delivers it.

---

## Guest-facing

US-1 (Slice C/D) As a guest, I want to ask about room rates in my own
language (English, Romanized Nepali, or Devanagari), so that I don't need
to translate my question.
  AC: message in any of the 3 language forms returns an accurate, correctly
  formatted answer; language is auto-detected, not user-selected.

US-2 (Slice E) As a guest, I want to describe my trip in natural language
("2 people, next weekend, need a room with a view") and have the system
understand my dates and preferences, so that I don't fill out a form.
  AC: check-in/out dates, guest count, and room-type preference are
  correctly extracted into a structured booking-inquiry record.

US-3 (Slice C) As a guest, I want to reach the hotel through WhatsApp
instead of a website, so that I can use the app I already have open.
  AC: a guest can complete an entire booking inquiry via WhatsApp with the
  same accuracy as the website widget.

US-4 (Slice G) As a guest with a complex request (group booking, custom
negotiation, complaint), I want to be connected to a real person quickly,
so that I'm not stuck arguing with a bot.
  AC: escalation triggers within 1-2 turns of detected complexity/
  frustration; guest is told a human is joining, not left waiting silently.

---

## Hotel staff-facing

US-5 (Slice A) As hotel staff, I want to add my rooms, rates, and policies
myself without calling support, so that I can get set up quickly.
  AC: a non-technical staff member can complete hotel setup in under 1 hour
  using the dashboard and provided templates.

US-6 (Slice B) As hotel staff, I want to upload or write FAQs and policies
in my own words, so that the AI answers guests the way I would.
  AC: uploaded/entered content is ingested into the knowledge base and
  reflected in AI answers within minutes.

US-7 (Slice G) As hotel staff, I want to see live conversations and jump in
when the AI hands off, so that I don't lose a guest mid-conversation.
  AC: staff dashboard shows real-time conversation with full context
  (guest messages + AI responses) the moment escalation triggers.

US-8 (Slice A/B) As hotel staff, I want to see all booking inquiries in one
place, so that I can follow up and convert them into confirmed bookings.
  AC: inquiries list is filterable by date range and status, exportable.

US-9 (Dashboard) As a hotel owner, I want basic analytics (conversation
volume, escalation rate, resolution rate), so that I can see whether this
is actually saving my staff time.
  AC: dashboard shows these 3 metrics for a selectable date range, matching
  the pilot's "60% workload reduction" measurement goal.

---

## Founder/operator-facing (internal)

US-10 (Stage 4, continuous) As the founder, I want a golden-set eval that
runs against every build, so that I catch AI quality regressions the same
day they're introduced, not weeks later.
  AC: eval harness runs on CI for every merge touching prompts/RAG/models;
  results visible without manual re-running.

US-11 (Architecture) As the founder, I want the AI model to be swappable
via config, so that a Gemini deprecation doesn't force an emergency rewrite.
  AC: changing the active model is a one-line config change, verified by a
  test that fails if a model string is hardcoded outside the router module.
