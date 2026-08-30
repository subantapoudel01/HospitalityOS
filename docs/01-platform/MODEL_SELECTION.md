# Model Selection — How to Actually Decide
## HospitalityOS — AI Receptionist MVP
Version 1.0

The original investor summary named Gemini 1.5 Flash. That model is
shut down, and more importantly, "Gemini" was never validated against
alternatives for this specific job (Nepali/Romanized-Nepali/Devanagari
hospitality conversation). This doc replaces "we use Gemini" with "here
is how we chose."

## 1. What the research actually shows (Aug 2026)

- Nepali is a "low-resource but not extremely low-resource" language.
  On a recent Indic-language benchmark, GPT-5 scored highest on this tier
  (44.9%), ahead of Claude-4.5 (42.0) and DeepSeek-3.2 (41.6). Gemini was
  not in that specific comparison — there is no confirmed evidence Gemini
  is the strongest option for Nepali specifically. Treat "Gemini is best
  for us" as an untested assumption, not a fact.
- Sarvam (India-focused, open-weight multilingual model) is built
  specifically for Indic-family languages and is worth benchmarking given
  Nepali's script/family overlap with Hindi — it's not a default choice
  either, just a candidate that shouldn't be skipped.
- On cost, Gemini's budget tier is genuinely the cheapest of the major
  proprietary options (Gemini 2.5 Flash-Lite: $0.10/$0.40 per 1M tokens
  input/output; 3.5 Flash-Lite: $0.30/$2.50). Claude Haiku 4.5 ($1/$5) and
  GPT-5.6 Luna (~$0.20/$1.20) are the nearest competitors. Cost alone
  should not decide this — cost-per-successfully-resolved-conversation
  matters more than cost-per-token if a cheaper model needs more retries
  or escalates more often.

## 2. Candidates to benchmark before locking in

| Candidate | Why it's a candidate |
|---|---|
| Gemini 3.5 Flash-Lite / 3.6 Flash | cheapest major-vendor tier, 1M context, original plan |
| Claude Haiku 4.5 | strong reasoning-per-token, good tool-calling reliability |
| GPT-5.6 mini/Luna tier | competitive Indic-language benchmark result, cheap |
| Sarvam (or similar Indic-focused open-weight model) | purpose-built for the language family, self-hostable |

## 3. How to actually decide (half-day task, not a rebuild)

Because `backend/app/core/model_router.py` is the only place a model
string lives, switching providers is a config change. Use that:

1. Build a golden eval set of 50-100 realistic guest questions, split
   across English, Romanized Nepali, and Devanagari — pull from real
   phrasing patterns (WhatsApp-style short questions, not formal text).
2. Run the set through each candidate via `tests/llm_eval/`.
3. Score: factual accuracy against your knowledge base, hallucination
   rate, correct escalation behavior, and latency.
4. Compute cost per **successfully resolved conversation**, not just
   cost per token — a cheaper model that needs 2x the follow-up turns
   or escalates more often may cost more in practice.
5. Pick the winner, set `AI_PROVIDER` and the three model env vars, done.
6. Re-run this same eval any time a provider announces a deprecation or
   a new model release — it costs an afternoon, not a rewrite, because
   of the router abstraction.

## 4. Decision log (fill in once you've actually run the eval)

| Date | Candidate tested | Nepali accuracy | Cost/resolved convo | Decision |
|---|---|---|---|---|
| _(pending)_ | | | | |
