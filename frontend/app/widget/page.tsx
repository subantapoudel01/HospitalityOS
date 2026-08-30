"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  ChatApiError,
  LANGUAGE_LABELS,
  getTranscript,
  requestHuman,
  sendMessage,
  type Citation,
  type DetectedLanguage,
} from "@/lib/chat";

/**
 * Guest chat widget.
 *
 * Mobile-first and iframe-friendly, per UI_UX_PLAN. Language is detected
 * automatically from what the guest types - English, Romanized Nepali or
 * Devanagari - with no picker, per UI_UX_PLAN.
 */

interface Bubble {
  id: number;
  role: "guest" | "ai" | "staff" | "note" | "error";
  text: string;
  citations?: Citation[];
  meta?: string;
}

// Single-property pilot: the hotel is fixed rather than chosen by the guest.
// A real embed will carry this in the snippet that mounts the widget.
const HOTEL_ID = Number(process.env.NEXT_PUBLIC_WIDGET_HOTEL_ID || "1");

const GREETING =
  "Hello! I can answer questions about the hotel — rooms and rates, " +
  "check-in times, dining, and things to do nearby. What would you like " +
  "to know?";

export default function WidgetPage() {
  const [bubbles, setBubbles] = useState<Bubble[]>([
    { id: 0, role: "ai", text: GREETING },
  ]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [escalated, setEscalated] = useState(false);
  // Auto-detected, never chosen by the guest (UI_UX_PLAN: no language picker).
  const [language, setLanguage] = useState<DetectedLanguage | null>(null);

  const nextId = useRef(1);
  const logRef = useRef<HTMLDivElement>(null);
  // Ids already rendered, so polling only appends what is genuinely new.
  const seenServerIds = useRef<Set<number>>(new Set());

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles, busy]);

  // Poll for staff replies. Without this, "a staff member will join" is a
  // promise the guest never sees fulfilled: a human answering in the
  // dashboard would land in the database and nowhere else.
  useEffect(() => {
    if (conversationId === null) return;
    let cancelled = false;

    async function poll() {
      try {
        const t = await getTranscript(HOTEL_ID, conversationId as number);
        if (cancelled) return;
        const fresh = t.messages.filter(
          (m) => m.sender === "staff" && !seenServerIds.current.has(m.id)
        );
        for (const m of fresh) {
          seenServerIds.current.add(m.id);
          push({ role: "staff", text: m.content });
        }
      } catch {
        // Transient failures are not worth interrupting the guest over.
      }
    }

    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(poll, 5000);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => (document.hidden ? stop() : (poll(), start()));

    poll();
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [conversationId]);

  function push(bubble: Omit<Bubble, "id">) {
    setBubbles((prev) => [...prev, { ...bubble, id: nextId.current++ }]);
  }

  async function handleSend(ev: React.FormEvent) {
    ev.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;

    setDraft("");
    push({ role: "guest", text });
    setBusy(true);

    try {
      const reply = await sendMessage(HOTEL_ID, text, conversationId);
      setConversationId(reply.conversation_id);
      setLanguage(reply.language);
      // Escalation can now be triggered by what the guest types, not
      // just the button, so the button must reflect the real status.
      if (reply.conversation_status === "escalated") setEscalated(true);
      push({
        role: reply.intent === "escalation" ? "note" : "ai",
        text: reply.reply,
        // Only cite when the answer was actually grounded. A greeting or a
        // refusal has nothing to cite, and showing sources would imply the
        // reply came from the knowledge base when it did not.
        citations: reply.intent === "answer" ? reply.citations : [],

        meta:
          reply.model +
          (reply.latency_ms ? " | " + reply.latency_ms + "ms" : "") +
          (reply.search_text ? " | searched: " + reply.search_text : ""),
      });
    } catch (err) {
      push({
        role: "error",
        text:
          err instanceof ChatApiError
            ? err.message
            : "Something went wrong. Please try again.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleRequestHuman() {
    if (!conversationId || escalated) return;
    try {
      await requestHuman(HOTEL_ID, conversationId);
      setEscalated(true);
      push({
        role: "note",
        text: "A staff member has been asked to join this conversation.",
      });
    } catch (err) {
      push({
        role: "error",
        text:
          err instanceof ChatApiError ? err.message : "Could not reach staff.",
      });
    }
  }

  return (
    <div className="widget">
      <header className="widget-header">
        <h1 className="widget-title">
          Hotel assistant
          <span className="widget-badge">AI assistant</span>
        </h1>
        <p className="widget-sub">
          Answers come from this hotel&apos;s own information. English and
          Nepali both work. Ask for a person any time.
        </p>
      </header>

      <div className="widget-log" ref={logRef} aria-live="polite">
        {bubbles.map((b) => (
          <div key={b.id} style={{ display: "contents" }}>
            <div
              className={
                b.role === "guest"
                  ? "bubble bubble-guest"
                  : b.role === "note"
                    ? "bubble bubble-note"
                    : b.role === "error"
                      ? "bubble bubble-error"
                      : b.role === "staff"
                        ? "bubble bubble-staff"
                        : "bubble bubble-ai"
              }
            >
              {b.text}
            </div>
            {b.citations && b.citations.length > 0 && (
              <div className="cites">
                {b.citations.map((c) => (
                  <span
                    className="cite"
                    key={c.chunk_id}
                    title={"similarity " + c.score.toFixed(3)}
                  >
                    {c.document_title}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="bubble bubble-ai">
            <span className="typing" aria-label="Assistant is typing">
              <i />
              <i />
              <i />
            </span>
          </div>
        )}
      </div>

      <form className="composer" onSubmit={handleSend}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about rooms, check-in, dining... / नेपालीमा सोध्नुहोस्"
          aria-label="Your message"
          disabled={busy}
        />
        <Button type="submit" variant="primary" disabled={busy || !draft.trim()}>
          Send
        </Button>
      </form>

      <div className="widget-foot">
        <Button
          type="button"
          variant="link"
          onClick={handleRequestHuman}
          disabled={!conversationId || escalated}
        >
          {escalated ? "Staff notified" : "Talk to a person"}
        </Button>
        <span className="widget-meta">
          {language ? LANGUAGE_LABELS[language] + " · " : ""}
          {conversationId ? "#" + conversationId : "not started"}
        </span>
      </div>
    </div>
  );
}
