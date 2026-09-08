"""
Single source of truth for AI model selection.
NO OTHER FILE MAY REFERENCE A RAW MODEL STRING OR PROVIDER SDK DIRECTLY.

Provider-agnostic by design: don't assume Gemini is the final answer.
See docs/01-platform/MODEL_SELECTION.md for how to benchmark alternatives
before locking a provider in.

Re-verify choices against each provider's deprecation/pricing pages before
every deploy — models retire and reprice on a cadence measured in months.

Embeddings and chat are routed independently. The pilot runs embeddings on
a local ONNX model so the knowledge base works with no API key, no network
and no per-call cost; flipping AI_EMBED_PROVIDER=gemini switches to the
hosted model without touching a caller.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

PROVIDER = os.environ.get("AI_PROVIDER", "gemini")  # gemini | anthropic | openai

CHAT_MODEL = os.environ.get("AI_CHAT_MODEL", "gemini-3.6-flash")
FAST_MODEL = os.environ.get("AI_FAST_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = os.environ.get("AI_EMBED_MODEL", "gemini-embedding-001")

# --- embeddings ---------------------------------------------------------

EMBED_PROVIDER = os.environ.get("AI_EMBED_PROVIDER", "local")  # local | gemini

# Multilingual (XLM-R derived, covers Nepali/Devanagari), 384-dim, ~0.22GB.
LOCAL_EMBED_MODEL = os.environ.get(
    "AI_EMBED_LOCAL_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

_DIMENSIONS = {
    "local": 384,
    "gemini": 3072,
}

_model_lock = threading.Lock()
_local_model = None


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced, with a fixable message."""


def embedding_dimension(provider: str | None = None) -> int:
    """Vector width for the active embedding provider."""
    p = provider or EMBED_PROVIDER
    try:
        return _DIMENSIONS[p]
    except KeyError:
        raise EmbeddingError(
            f"Unknown AI_EMBED_PROVIDER {p!r}. Expected one of {sorted(_DIMENSIONS)}."
        ) from None


def _get_local_model():
    """Load the ONNX embedding model once, lazily and thread-safely.

    Lazy because importing fastembed pulls in onnxruntime, and nothing that
    merely imports this module (alembic, the health check) should pay for it.
    """
    global _local_model
    if _local_model is not None:
        return _local_model
    with _model_lock:
        if _local_model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise EmbeddingError(
                    "fastembed is not installed but AI_EMBED_PROVIDER=local. "
                    "Install it, or set AI_EMBED_PROVIDER=gemini."
                ) from exc
            _local_model = TextEmbedding(model_name=LOCAL_EMBED_MODEL)
    return _local_model


def _embed_local(texts: list[str], *, as_query: bool) -> list[list[float]]:
    model = _get_local_model()
    # query_embed/passage_embed matter for asymmetric models (e5 and kin);
    # for the symmetric default they are equivalent to embed(). Going
    # through them anyway means swapping in an asymmetric model stays a
    # config change.
    vectors = model.query_embed(texts) if as_query else model.passage_embed(texts)
    return [v.tolist() for v in vectors]


def _embed_gemini(texts: list[str], *, as_query: bool) -> list[list[float]]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingError(
            "AI_EMBED_PROVIDER=gemini but GEMINI_API_KEY is empty. "
            "Set the key, or use AI_EMBED_PROVIDER=local."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise EmbeddingError(
            "google-genai is not installed but AI_EMBED_PROVIDER=gemini."
        ) from exc

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY" if as_query else "RETRIEVAL_DOCUMENT"
        ),
    )
    return [list(e.values) for e in result.embeddings]


def _dispatch(texts: list[str], *, as_query: bool) -> list[list[float]]:
    if not texts:
        return []
    if EMBED_PROVIDER == "local":
        vectors = _embed_local(texts, as_query=as_query)
    elif EMBED_PROVIDER == "gemini":
        vectors = _embed_gemini(texts, as_query=as_query)
    else:
        raise EmbeddingError(
            f"Unknown AI_EMBED_PROVIDER {EMBED_PROVIDER!r}. "
            f"Expected one of {sorted(_DIMENSIONS)}."
        )

    expected = embedding_dimension()
    for v in vectors:
        if len(v) != expected:
            raise EmbeddingError(
                f"{EMBED_PROVIDER} returned a {len(v)}-dim vector but the "
                f"schema stores {expected}. Changing embedding model requires "
                f"a migration and a full re-embed."
            )
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed passages for storage."""
    return _dispatch(list(texts), as_query=False)


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return _dispatch([text], as_query=True)[0]


def describe() -> dict[str, object]:
    """Active configuration — surfaced by the debug/search endpoint."""
    return {
        "embed_provider": EMBED_PROVIDER,
        "embed_model": LOCAL_EMBED_MODEL if EMBED_PROVIDER == "local" else EMBED_MODEL,
        "dimension": embedding_dimension(),
    }


# --- chat ---------------------------------------------------------------

CHAT_PROVIDER = os.environ.get("AI_CHAT_PROVIDER", "extractive")
# extractive | gemini | anthropic | openai
#
# `extractive` composes a reply directly out of the retrieved passages with
# no model call at all: no API key, no cost, no network, and no way to
# invent a fact. It is a real fallback rather than a mock — if a hosted
# provider is misconfigured or down, this still answers the guest.

# Per-1M-token prices. Deliberately env-driven and unset by default:
# hardcoding 2026 prices into source guarantees they are wrong within a
# quarter, and a confidently wrong cost figure is worse than an absent one.
# Unset means cost_estimate stays None rather than fabricated.
_COST_IN = os.environ.get("AI_CHAT_COST_PER_1M_INPUT", "").strip()
_COST_OUT = os.environ.get("AI_CHAT_COST_PER_1M_OUTPUT", "").strip()

# Gemini 3.x spends "thinking" tokens before answering. They are billed and
# they cost latency, which matters against NFR-1 (sub-3s end to end).
# Thinking-token counts are stable and worth constraining:
#     unset  -> ~190 thinking tokens
#     128    -> 128 thinking tokens   <- default
#     -1     -> ~218 thinking tokens (dynamic)
#     0      -> HTTP 400; gemini-3.x refuses to disable thinking entirely
#
# WALL-CLOCK LATENCY IS NOT A PROPERTY OF THIS SETTING on the free tier.
# The same model, same prompt, same budget measured 1.9s once and 14.8s an
# hour later, and 503 "high demand" is common. Do NOT read a latency number
# here as something the budget bought. NFR-1 (sub-3s) cannot be honestly
# assessed until this runs on a paid tier with predictable capacity.
_THINKING_BUDGET = os.environ.get("AI_CHAT_THINKING_BUDGET", "128").strip()

# When a hosted provider fails - rate limit, outage, expired key - serve the
# guest from the retrieved passages instead of showing an error bubble. The
# answer is blunter but it is correct and grounded, which beats a dead
# widget. Set to "off" to surface provider failures as 503 instead.
CHAT_FALLBACK = os.environ.get("AI_CHAT_FALLBACK", "extractive").strip()

# Hosted models return transient 429 (rate limit) and 503 (capacity) far more
# often than you would hope, especially on free tiers. Retry those a couple of
# times with backoff before giving up and degrading.
CHAT_RETRIES = int(os.environ.get("AI_CHAT_RETRIES", "2"))
_TRANSIENT = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503", "500", "INTERNAL")


def _is_transient(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in _TRANSIENT)

MAX_OUTPUT_TOKENS = int(os.environ.get("AI_CHAT_MAX_OUTPUT_TOKENS", "2048"))
TEMPERATURE = float(os.environ.get("AI_CHAT_TEMPERATURE", "0.2"))


class ChatError(RuntimeError):
    """Raised when a reply cannot be generated, with a fixable message."""


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    thinking_tokens: int | None = None
    cost_estimate: float | None = None
    # Set when a hosted provider failed and extractive served instead, so
    # degradation is visible in telemetry rather than silent.
    degraded_from: str | None = None


def _estimate_cost(prompt_tokens, completion_tokens, thinking_tokens) -> float | None:
    if not _COST_IN or not _COST_OUT:
        return None
    try:
        rate_in, rate_out = float(_COST_IN), float(_COST_OUT)
    except ValueError:
        return None
    # Thinking tokens bill at the output rate.
    out = (completion_tokens or 0) + (thinking_tokens or 0)
    return round(
        (prompt_tokens or 0) / 1_000_000 * rate_in + out / 1_000_000 * rate_out, 6
    )


def _chat_extractive(system, messages, context) -> tuple[str, dict]:
    """Surface the retrieved passages verbatim, with no model involved.

    Deliberately does NOT claim to have answered. Without a model there is no
    way to judge whether these passages actually address the question — a
    guest asking about a casino would otherwise be handed restaurant opening
    hours under a confident heading. Framing them as the closest available
    information, plus an offer of a human, is the honest presentation, and it
    keeps the whole pipeline exercisable with no API key.
    """
    if not context:
        raise ChatError("extractive provider called with no context passages")
    body = "\n\n".join("- " + c.strip() for c in context[:3])
    return (
        "Here is the closest information I have from the hotel:\n\n"
        + body
        + "\n\nIf that does not answer your question, I can pass you to a "
        "staff member."
    ), {}


def _chat_gemini(system, messages, context) -> tuple[str, dict]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ChatError(
            "AI_CHAT_PROVIDER=gemini but GEMINI_API_KEY is empty. "
            "Set the key, or use AI_CHAT_PROVIDER=extractive."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ChatError("google-genai is not installed.") from exc

    contents = [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])],
        )
        for m in messages
    ]
    cfg = {
        "system_instruction": system,
        "temperature": TEMPERATURE,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if _THINKING_BUDGET:
        try:
            cfg["thinking_config"] = types.ThinkingConfig(
                thinking_budget=int(_THINKING_BUDGET)
            )
        except (ValueError, TypeError):
            pass

    client = genai.Client(api_key=api_key)
    try:
        resp = client.models.generate_content(
            model=CHAT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(**cfg),
        )
    except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types
        # Thinking budgets are model-specific and validated server side:
        # gemini-3.6-flash rejects 0 outright with a bare 400. A tuning knob
        # must never be able to take guest-facing chat down, so drop it and
        # retry once before giving up.
        if "thinking_config" in cfg and "INVALID_ARGUMENT" in str(exc):
            cfg.pop("thinking_config")
            try:
                resp = client.models.generate_content(
                    model=CHAT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(**cfg),
                )
            except Exception as retry_exc:  # noqa: BLE001
                raise ChatError(
                    "Gemini request failed: " + str(retry_exc)
                ) from retry_exc
        else:
            raise ChatError("Gemini request failed: " + str(exc)) from exc

    text = (resp.text or "").strip()
    usage = getattr(resp, "usage_metadata", None)
    meta = {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "completion_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
    }
    if not text:
        # An empty candidate usually means the entire budget went to
        # thinking, or a safety filter fired. Both are actionable, so say so
        # rather than returning a blank bubble to the guest.
        raise ChatError(
            "Gemini returned no text. Raise AI_CHAT_MAX_OUTPUT_TOKENS or lower "
            "AI_CHAT_THINKING_BUDGET."
        )
    return text, meta


def _groq_client():
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ChatError(
            "Groq is selected but GROQ_API_KEY is empty. Set the key, or use "
            "AI_CHAT_PROVIDER=extractive."
        )
    try:
        from groq import Groq
    except ImportError as exc:
        raise ChatError("groq is not installed.") from exc
    return Groq(api_key=api_key)


def _groq_completion(model: str, messages: list[dict], *, max_tokens: int,
                     temperature: float) -> tuple[str, dict]:
    client = _groq_client()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types
        raise ChatError("Groq request failed: " + str(exc)) from exc

    text = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    return text, {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        # Groq exposes no separate reasoning-token count.
        "thinking_tokens": None,
    }


def _chat_groq(system, messages, context) -> tuple[str, dict]:
    text, meta = _groq_completion(
        CHAT_MODEL,
        [{"role": "system", "content": system}] + messages,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
    )
    if not text:
        raise ChatError(
            "Groq returned an empty response. Check AI_CHAT_MODEL is a model "
            "your key can reach - retired model ids fail this way."
        )
    return text, meta


def _chat_anthropic(system, messages, context) -> tuple[str, dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ChatError("AI_CHAT_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty.")
    try:
        import anthropic
    except ImportError as exc:
        raise ChatError("anthropic is not installed.") from exc
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=CHAT_MODEL,
        system=system,
        messages=messages,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text, {
        "prompt_tokens": resp.usage.input_tokens,
        "completion_tokens": resp.usage.output_tokens,
        "thinking_tokens": None,
    }


def _chat_openai(system, messages, context) -> tuple[str, dict]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ChatError("AI_CHAT_PROVIDER=openai but OPENAI_API_KEY is empty.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ChatError("openai is not installed.") from exc
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "system", "content": system}] + messages,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=TEMPERATURE,
    )
    return resp.choices[0].message.content.strip(), {
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "thinking_tokens": None,
    }


_CHAT_PROVIDERS = {
    "extractive": _chat_extractive,
    "groq": _chat_groq,
    "gemini": _chat_gemini,
    "anthropic": _chat_anthropic,
    "openai": _chat_openai,
}


def chat(
    *,
    system: str,
    messages: list[dict],
    context: list[str] | None = None,
) -> ChatResult:
    """Generate a reply.

    `messages` is [{"role": "user"|"assistant", "content": str}, ...].
    `context` is the retrieved passages: hosted providers receive them
    folded into the caller's system prompt, while the extractive provider
    answers from them directly. Callers never name a provider or a model.
    """
    context = context or []
    handler = _CHAT_PROVIDERS.get(CHAT_PROVIDER)
    if handler is None:
        raise ChatError(
            f"Unknown AI_CHAT_PROVIDER {CHAT_PROVIDER!r}. Expected one of "
            + ", ".join(sorted(_CHAT_PROVIDERS))
        )

    started = time.perf_counter()
    degraded_from = None
    try:
        last: ChatError | None = None
        for attempt in range(CHAT_RETRIES + 1):
            try:
                text, meta = handler(system, messages, context)
                provider_used = CHAT_PROVIDER
                break
            except ChatError as exc:
                last = exc
                if attempt >= CHAT_RETRIES or not _is_transient(exc):
                    raise
                # 1s, 2s, 4s ... keeps a guest waiting far less than a
                # failed turn costs, and free-tier limits are per-minute.
                time.sleep(2**attempt)
        else:  # pragma: no cover - loop always breaks or raises
            raise last if last else ChatError("chat failed")
    except ChatError:
        can_fall_back = (
            CHAT_FALLBACK == "extractive"
            and CHAT_PROVIDER != "extractive"
            and bool(context)
        )
        if not can_fall_back:
            raise
        text, meta = _chat_extractive(system, messages, context)
        degraded_from = CHAT_PROVIDER
        provider_used = "extractive"
    latency_ms = int((time.perf_counter() - started) * 1000)

    return ChatResult(
        text=text,
        provider=provider_used,
        model="extractive" if provider_used == "extractive" else CHAT_MODEL,
        latency_ms=latency_ms,
        prompt_tokens=meta.get("prompt_tokens"),
        completion_tokens=meta.get("completion_tokens"),
        thinking_tokens=meta.get("thinking_tokens"),
        cost_estimate=_estimate_cost(
            meta.get("prompt_tokens"),
            meta.get("completion_tokens"),
            meta.get("thinking_tokens"),
        ),
        degraded_from=degraded_from,
    )


def describe_chat() -> dict[str, object]:
    """Active chat configuration — surfaced by the chat endpoint."""
    return {
        "chat_provider": CHAT_PROVIDER,
        "chat_model": "extractive" if CHAT_PROVIDER == "extractive" else CHAT_MODEL,
    }


# --- fast subagent tasks (Slice D) --------------------------------------
#
# Language classification and translation run on FAST_MODEL rather than
# CHAT_MODEL. These are narrow, high-frequency calls on every Nepali turn,
# and ARCHITECTURE.md reserves the Flash-Lite tier for exactly this. Measured
# on gemini-3.5-flash-lite: ~906ms with zero thinking tokens, against ~13s
# for a full chat model on the same translation.

FAST_PROVIDER = os.environ.get("AI_FAST_PROVIDER", CHAT_PROVIDER).strip()

_LANGUAGE_CODES = ("en", "ne_romanized", "ne_devanagari")


def _fast_gemini(system: str, prompt: str, *, max_tokens: int) -> tuple[str, dict]:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ChatError("FAST_MODEL needs GEMINI_API_KEY, which is empty.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ChatError("google-genai is not installed.") from exc

    client = genai.Client(api_key=api_key)
    try:
        resp = client.models.generate_content(
            model=FAST_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                max_output_tokens=max_tokens,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise ChatError("FAST_MODEL request failed: " + str(exc)) from exc

    usage = getattr(resp, "usage_metadata", None)
    return (resp.text or "").strip(), {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "completion_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
    }


def _fast_groq(system: str, prompt: str, *, max_tokens: int) -> tuple[str, dict]:
    return _groq_completion(
        FAST_MODEL,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    )


_FAST_PROVIDERS = {
    "groq": _fast_groq,
    "gemini": _fast_gemini,
}


def fast_available() -> bool:
    """Whether the fast tier can be used at all.

    Callers use this to decide between a model-backed path and a
    deterministic fallback, rather than provoking an exception to find out.
    """
    return FAST_PROVIDER in _FAST_PROVIDERS


def _fast_call(system: str, prompt: str, *, max_tokens: int) -> ChatResult:
    handler = _FAST_PROVIDERS.get(FAST_PROVIDER)
    if handler is None:
        # No fast tier (typically AI_FAST_PROVIDER=extractive, i.e. no key).
        # Callers are expected to degrade gracefully, not treat this as fatal.
        raise ChatError(
            f"AI_FAST_PROVIDER={FAST_PROVIDER!r} has no fast-model "
            "implementation. Expected one of " + ", ".join(sorted(_FAST_PROVIDERS))
        )

    started = time.perf_counter()
    last: ChatError | None = None
    for attempt in range(CHAT_RETRIES + 1):
        try:
            text, meta = handler(system, prompt, max_tokens=max_tokens)
            break
        except ChatError as exc:
            last = exc
            if attempt >= CHAT_RETRIES or not _is_transient(exc):
                raise
            time.sleep(2**attempt)
    else:  # pragma: no cover
        raise last if last else ChatError("fast call failed")

    return ChatResult(
        text=text,
        provider=FAST_PROVIDER,
        model=FAST_MODEL,
        latency_ms=int((time.perf_counter() - started) * 1000),
        prompt_tokens=meta.get("prompt_tokens"),
        completion_tokens=meta.get("completion_tokens"),
        thinking_tokens=meta.get("thinking_tokens"),
        cost_estimate=_estimate_cost(
            meta.get("prompt_tokens"),
            meta.get("completion_tokens"),
            meta.get("thinking_tokens"),
        ),
    )


CLASSIFY_SYSTEM = (
    "You label the language of a short message from a hotel guest. "
    "Answer with exactly one of these codes and nothing else:\n"
    "en - English\n"
    "ne_romanized - Nepali written in the Latin alphabet\n"
    "ne_devanagari - Nepali written in Devanagari script\n"
    "If the message mixes languages, choose the one the guest is mainly "
    "writing in. Output only the code."
)


def classify_language(text: str) -> ChatResult:
    """Label a message as en / ne_romanized / ne_devanagari.

    Used only when the deterministic detector cannot decide, so this stays
    a small fraction of traffic rather than a per-message cost.
    """
    result = _fast_call(CLASSIFY_SYSTEM, text, max_tokens=512)
    code = result.text.strip().strip(".").lower()
    if code not in _LANGUAGE_CODES:
        # Take the first recognised code if the model wrapped it in prose.
        code = next((c for c in _LANGUAGE_CODES if c in code), "")
        if not code:
            raise ChatError(f"Unrecognised language label: {result.text!r}")
    result.text = code
    return result


TRANSLATE_SYSTEM = (
    "You translate short hotel guest messages. Output ONLY the translation, "
    "with no quotes, no explanation and no transliteration notes. Preserve "
    "numbers, times and proper nouns exactly. If the text is already in the "
    "target language, return it unchanged."
)


def translate(text: str, *, target: str = "English") -> ChatResult:
    """Translate a guest message, typically into English for retrieval."""
    return _fast_call(
        TRANSLATE_SYSTEM,
        f"Translate into {target}:\n\n{text}",
        max_tokens=1024,
    )


def describe_fast() -> dict[str, object]:
    return {"fast_provider": FAST_PROVIDER, "fast_model": FAST_MODEL}


# TODO (Slice G): escalation-trigger classification, also on FAST_MODEL.
