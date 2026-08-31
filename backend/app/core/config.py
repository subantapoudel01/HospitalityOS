"""
App-wide settings, read from environment variables (see .env.example).
Model selection stays out of here on purpose — that lives in model_router.py,
the single source of truth for AI model strings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hospitalityos:devpassword@postgres:5432/hospitalityos"
    redis_url: str = "redis://redis:6379/0"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- retrieval-augmented chat (Slice C) ---
    # How many chunks to retrieve per question.
    chat_top_k: int = 4
    # Minimum cosine similarity for a chunk to count as relevant. Below this
    # the assistant refuses without calling a model at all.
    #
    # MEASURED, and the result is important: this floor does NOT cleanly
    # separate answerable from unanswerable questions. On the Slice B corpus
    # the lowest in-scope score was 0.283 ("When can I get into my room?")
    # while the highest out-of-scope score was 0.434 ("What time does your
    # spa and bowling alley open?"). They overlap, because embeddings measure
    # topical similarity, not answerability - a plausible-sounding hotel
    # question retrieves plausible-looking hotel chunks.
    #
    # So the floor is a cheap first-pass filter for obvious noise (foreign
    # topics, nonsense), NOT the hallucination guarantee on its own. The
    # grounding instruction in the system prompt is the second and stronger
    # line of defence. 0.20 is set low deliberately: raising it starts
    # refusing real questions long before it stops the dangerous ones.
    chat_min_score: float = 0.20
    # Prior turns replayed to the model for follow-up questions.
    chat_history_turns: int = 6

    # --- automatic escalation (US-4) ---
    # Hand a conversation to a human without waiting to be asked, when the
    # guest says the assistant is not helping, or when the assistant has
    # dead-ended several questions in a row.
    chat_auto_escalate: bool = True
    # Consecutive refusals before handing over, counting the one about to
    # be sent. 3, not 2: two unanswerable questions in a row is common and
    # unremarkable (a guest asking about a spa and a casino that do not
    # exist). Three is a pattern. Every false escalation interrupts a real
    # person, and staff who are pulled into three non-conversations stop
    # trusting the queue. Set to 0 to disable this trigger alone.
    chat_dead_end_turns: int = 3

    # --- staff accounts / JWT ---
    # Signing secret for staff session tokens. EMPTY MEANS LOGIN IS
    # DISABLED (503), never a built-in default: a shipped default secret
    # is forgeable by anyone who can read this repo.
    jwt_secret: str = ""
    # One working shift. Long enough that staff are not re-typing a
    # password between guests, short enough that a laptop left open in the
    # lobby stops being a session by morning.
    jwt_expire_minutes: int = 12 * 60

    # Secure flag on the session cookie. MUST be true anywhere the site is
    # served over HTTPS; false locally because a Secure cookie is simply
    # never stored on http://localhost, which looks like "login silently
    # does nothing".
    cookie_secure: bool = False
    # Domain for the session cookie. Empty = host-only, which is right
    # unless the API and the UI sit on different subdomains.
    cookie_domain: str = ""

    # --- staff dashboard gate (Slice G) ---
    # Shared secret for the staff endpoints. Empty means the staff API is
    # disabled entirely (503), never open.
    #
    # SUPERSEDED by JWT staff accounts. Kept working so an existing
    # deployment does not break on upgrade, and so scripts and the eval
    # harness have a way in. Leave it EMPTY in production - it is a
    # second, weaker door into the same rooms.
    staff_api_token: str = ""

    # --- multilingual (Slice D) ---
    # Translate Nepali questions to English before retrieval. Slice B
    # measured why this matters: a native-Devanagari question scored 0.17
    # against the English corpus and its answer was not in the top three.
    chat_translate_queries: bool = True
    # When the deterministic detector cannot decide, spend a FAST_MODEL call
    # to classify. Turn off to stay entirely free and default to English.
    chat_language_model_fallback: bool = True


settings = Settings()
