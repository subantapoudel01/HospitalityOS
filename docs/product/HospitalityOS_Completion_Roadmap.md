# HospitalityOS — Project Completion Roadmap
**Prepared:** August 18, 2026
**Scope:** One consolidated, prioritized path from where the project stands today to a paid pilot customer.

---

## 1. Where the project actually stands

I opened the uploaded zip and read every file. Here's the honest state, not the state the docs claim:

**Genuinely done and good (Stage 1 — Discovery + Architecture):**
SRS, PRD, MVP Scope, User Stories, Architecture doc, Database Design, UI/UX Plan, Roadmap, Model Selection doc, and the two planning files (PROJECT_MANAGEMENT.txt, FOLDER_STRUCTURE.txt) are all present, internally consistent, and reasonably scoped for an 8-week solo/small-team build. This is real planning work, not filler.

**Not actually done, despite what the docs say (Stage 2 — Foundation Bootstrap):**
`PROJECT_MANAGEMENT.txt` and the roadmap both state Stage 2's exit criterion is *"a fresh clone + `make dev` produces a running stack."* That is not true of the zip as delivered. There is a `docker-compose.yml` and a `Makefile`, but:
- `backend/` has no `Dockerfile`, no `requirements.txt`, and no application code (no `main.py`, no FastAPI app instance) — only empty `__init__.py` files and the one real file, `model_router.py` (which itself is a stub — the `chat()`/`classify()`/`embed()` functions it promises are marked `TODO`, not implemented).
- `frontend/` has no `Dockerfile`, no `package.json`, and no Next.js app — the folders in `FOLDER_STRUCTURE.txt` (`app/`, `components/`, `lib/`) don't exist yet.
- There's no `alembic/` migration setup despite `FOLDER_STRUCTURE.txt` listing it.
- There's no CI pipeline config despite Stage 2's stated output being "CI pipeline running lint + test on every push."

Running `make dev` today would fail on the first `docker compose build` because there's nothing for Docker to build. This is the actual bottleneck — not a documentation gap, an execution gap. Everything from Stage 3 onward is blocked on this.

**One fact worth updating before you lock anything in:** `MODEL_SELECTION.md` and `.env.example` default to `gemini-3.6-flash` ($1.50/$7.50 per 1M tokens). Google shipped **Gemini 3.7 Flash** on August 13, 2026 — five days before this was written — at roughly **$0.75/$3.75 per 1M tokens** (about half the price) with specific gains on coding/agentic tasks. It doesn't change your architecture (the model-router pattern already isolates this), but it's a one-line `.env` change worth making before you run the model benchmark in Stage 1's eval step, so you're benchmarking against the model you'd actually ship. This is a good early proof point for why the router pattern exists: models will keep shifting under you every few weeks in 2026, and your job is to never have that force a rewrite.

Sources: [Google — Introducing Gemini 3.7 Flash](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-3-7-flash/), [Gemini 3.7 Flash pricing](https://tokenkarma.app/blog/gemini-3-7-flash-price-half-agentic-coding-2026/)

One more caution: the competitor names, benchmark percentages, and per-hotel pricing figures in your PRD/Model Selection docs came from an earlier AI research pass. They read as plausible, but I have not independently re-verified each one (Springbase AI's actual pricing, the exact Indic-language benchmark numbers, etc.). Before anything with those figures goes in front of an investor or gets quoted to a pilot hotel, spot-check the specific numbers you plan to cite.

---

## 2. The critical path, in order

Everything below is sequenced by genuine dependency, not by "phase number." Items on the same line can happen in parallel.

**1. Close the Stage 2 gap (this is the actual next task, before anything else in Stage 3 is possible)**
Add the missing `backend/Dockerfile` + `requirements.txt` + a real `main.py` with a health-check route, the missing `frontend/Dockerfile` + `package.json` + a minimal Next.js app, and an `alembic/` baseline migration. Until `make dev` genuinely boots Postgres+pgvector, Redis, FastAPI, and Next.js from a clean clone, every later stage is theoretical. Budget: 1–2 focused days, not the multi-day slog the docs assume you've already skipped — but it hasn't happened yet.

**2. Start WhatsApp Business API verification in parallel, starting now**
This is the one item on your critical path with a fixed external delay you don't control (Meta's business verification has historically run 1–3 weeks, sometimes longer with document back-and-forth). Everything else here is compressible by working harder; this isn't. Kick off the Meta Business Manager verification and WhatsApp Business API application this week regardless of where the code stands — it gates Stage 3 Slice F and you'd rather it finish in the background while you build Slices A–E.

**3. Line up the Pokhara pilot hotel(s), starting now, in parallel**
Relationship-building takes real calendar time and shouldn't sit on the critical path either. Start these conversations before you have a demo, not after — "we're building this for hotels like yours, can we show you something in a few weeks" is a fine opening line, and it de-risks Stage 5–6 by making sure a willing hotel actually exists when the code is ready.

**4. Build the golden multilingual eval set before writing conversation logic**
50–100 realistic guest questions across English, Romanized Nepali, and Devanagari, covering the FAQ/rates/policy/booking-inquiry categories from your MVP scope. Do this before Slice C/D exist, not after, so the model benchmark and every later regression check has real target questions instead of ones invented after the fact.

**5. Run the model benchmark once the eval set exists**
Gemini 3.7 Flash (updated pick) vs. whatever else you want in the comparison, scored against the golden set for accuracy and against your NPR price target for cost-per-conversation. Log the decision and the numbers in `MODEL_SELECTION.md`'s decision-log section so it's not re-litigated later.

**6. Build the vertical slices in dependency order (Stage 3)**
- **Slice A — Hotel + room/rate manual data entry.** Everything downstream needs hotel data to exist, so this genuinely has to come first. It's also your onboarding-speed test: your own design constraint is under 1 hour for non-technical staff — measure that against yourself as you build it.
- **Slice B — Knowledge base ingestion + RAG retrieval**, no chat UI yet — verify retrieval quality against the golden set before conversation logic sits on top of it.
- **Slice C — Chat widget + AI conversation, English only.** Get the core loop (message in, function-calling + RAG, message out) working end-to-end before adding language complexity.
- **Slice D — Romanized/Devanagari Nepali NLU layer** on the same engine.
- **Slice E — Booking-inquiry structured extraction** (dates, guest count, room preference).
- **Slice F — WhatsApp Business channel** (should be verified and ready by now if step 2 started on schedule).
- **Slice G — Human handoff + live dashboard (WebSocket).**

Run the eval harness against every slice as it lands, not saved up for a QA phase at the end — that's the single biggest efficiency idea already in your `PROJECT_MANAGEMENT.txt` and it's worth protecting: don't let deadline pressure turn "continuous eval" back into "test at the end."

**7. Deploy directly to the pilot as staging (Stage 5)**
Per your existing plan, don't build a separate staging ceremony — deploy into the pilot hotel's actual website/WhatsApp number behind a feature flag. Exit criterion: pilot staff can edit their own knowledge base without you.

**8. Run the pilot as UAT against real guests (Stage 6)**
Two weeks, monitored closely. Measure the workload-reduction number your investor summary promises (60% reduction target) — this needs to be an actual measurement, not an estimate, so instrument it from day one of the pilot rather than trying to reconstruct it afterward.

**9. Iterate → productize → close the first paying hotel (Stage 7)**
Fix what the pilot surfaced, package pricing and onboarding, close. This is also the point where you decide, with real usage data instead of assumptions, whether WhatsApp-first or website-widget-first actually matters more to your specific pilot hotel's guests.

---

## 3. Open decisions worth making explicitly (not by default)

- **Model provider lock-in:** don't finalize before step 5 above runs. The router pattern means this is cheap to defer.
- **PMS integration depth:** your own research found most target hotels won't have modern PMS APIs. Confirm your `source: manual|pms_sync` schema flag is enough for MVP and resist the pull to build real PMS sync before you have a paying customer asking for it.
- **WhatsApp vs. website priority:** the research says WhatsApp is the bigger gap for your actual target hotels; the original scope had it as "not building yet." Current plan already moved it into Slice F — worth confirming that's still the right call once you talk to your pilot hotel(s) in step 3, since their actual guest channel mix may differ from the general research.
- **Pricing (NPR 8,000–15,000/month target):** validate this against real cost-per-conversation numbers from step 5 before quoting it to the pilot hotel, not after.

---

## 4. Risks to keep watching (carried over from your risk register, reprioritized)

1. **Scaffold-vs-reality gap recurring elsewhere** — before trusting any other claim in these docs at face value ("CI pipeline running," "eval dashboard"), verify it exists rather than assuming prior work matches its own description.
2. **WhatsApp verification delay** — start now; don't let it become the thing that blocks the pilot launch date.
3. **Model deprecation mid-build** — the router pattern mitigates this; keep it enforced (no raw model strings outside `model_router.py`).
4. **Pilot hotel has no digital room/rate data** — the manual-entry speed constraint (under 1 hour onboarding) is your mitigation; test it on yourself early, not just at pilot time.
5. **Scope creep toward full HospitalityOS before first paid customer** — the exit gate for Stage 7 is "first paid customer," not "more features." Worth restating to yourself when the MVP feels "almost done" and a bigger idea looks tempting.

---

## 5. Suggested timeline

| Milestone | What closes it | Rough timing from today |
|---|---|---|
| Scaffold actually runs (`make dev` boots clean) | Dockerfiles, requirements.txt, package.json, minimal FastAPI + Next.js apps, alembic baseline | 1–2 days |
| WhatsApp verification submitted | Meta Business Manager + WhatsApp Business API application | This week (parallel) |
| Pilot hotel conversations started | Outreach begun in Pokhara | This week (parallel) |
| Golden eval set + model benchmark done | 50–100 item multilingual set scored across candidate models | +1 week |
| Slices A–E live (core receptionist, English + Nepali, no WhatsApp yet) | End-to-end chat + RAG + booking-inquiry extraction working | +3–4 weeks |
| Slice F–G live (WhatsApp + human handoff) | Full MVP feature-complete | +5–6 weeks |
| Pilot live (staging = production) | 1–2 Pokhara hotels on real traffic | +6–7 weeks |
| Pilot = UAT complete | 60% workload-reduction measurement, guest signal, bug log | +8–9 weeks |
| First paid customer | Pricing + onboarding closed | +9–10 weeks |

This runs slightly past your original 8-week target, almost entirely because of the Stage 2 gap that needs closing first — everything after that follows your existing plan's pacing.

---

## 6. Definition of done — unchanged from your own plan, restated as the finish line

- 1–2 pilot hotels live on staging = production, staff self-serve their own knowledge base
- Guest can complete a booking inquiry in English, Romanized Nepali, or Devanagari via website widget **and** WhatsApp
- Escalation to human staff works and is measured (precision/recall tracked)
- LLM eval dashboard shows accuracy/hallucination/latency/cost per slice
- Documented 60%+ reduction in repetitive front-desk queries at the pilot hotel — actually measured, not estimated
- Pricing + onboarding flow ready, first paying customer signed
