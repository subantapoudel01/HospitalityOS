"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { use } from "react";

import { StatusPill } from "@/components/receptionist/StatusPill";
import { TokenGate } from "@/components/receptionist/TokenGate";
import { Button } from "@/components/ui/Button";
import { Section } from "@/components/ui/Section";
import { shortDateTime } from "@/lib/format";
import {
  CONVERSATION_STATUSES,
  StaffApiError,
  getConversation,
  sendStaffMessage,
  setConversationStatus,
  type Conversation,
  type ConversationStatus,
} from "@/lib/staff";

const HOTEL_ID = Number(process.env.NEXT_PUBLIC_WIDGET_HOTEL_ID || "1");
const POLL_MS = 5000;

const SENDER_LABEL: Record<string, string> = {
  guest: "Guest",
  ai: "AI assistant",
  staff: "Staff",
};

export default function ConversationDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const conversationId = Number(id);

  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setConversation(await getConversation(HOTEL_ID, conversationId));
      setAuthError(null);
      setError(null);
    } catch (err) {
      if (err instanceof StaffApiError && err.isAuthProblem) {
        setAuthError(err.message);
      } else {
        setError(
          err instanceof Error ? err.message : "Could not load the conversation."
        );
      }
    } finally {
      setLoaded(true);
    }
  }, [conversationId]);

  useEffect(() => {
    refresh();
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(refresh, POLL_MS);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => (document.hidden ? stop() : (refresh(), start()));
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [conversation?.messages.length]);

  async function changeStatus(status: ConversationStatus) {
    try {
      setConversation(
        await setConversationStatus(HOTEL_ID, conversationId, status)
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update status.");
    }
  }

  async function handleSend(ev: React.FormEvent) {
    ev.preventDefault();
    const content = draft.trim();
    if (!content || sending) return;
    setSending(true);
    try {
      setConversation(await sendStaffMessage(HOTEL_ID, conversationId, content));
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send.");
    } finally {
      setSending(false);
    }
  }

  if (authError) return <TokenGate message={authError} onSaved={refresh} />;
  if (!loaded) return <p>Loading conversation…</p>;
  if (!conversation) {
    return (
      <>
        <div className="banner banner-error">
          {error || "Conversation not found."}
        </div>
        <a href="/staff">Back to dashboard</a>
      </>
    );
  }

  return (
    <>
      <div className="dash-head">
        <div>
          <h1 className="page-title">
            Conversation #{conversation.id}{" "}
            <StatusPill status={conversation.status} />
          </h1>
          <p className="page-sub">
            {conversation.channel} · started {shortDateTime(conversation.started_at)}
            {conversation.resolved_at
              ? " · resolved " + shortDateTime(conversation.resolved_at)
              : ""}
          </p>
        </div>
        <a href="/staff">← All conversations</a>
      </div>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <Section title="Status" hint="Changing to resolved records the time.">
        <div className="filters">
          {CONVERSATION_STATUSES.map((s) => (
            <Button
              key={s}
              type="button"
              className={conversation.status === s ? "btn-on" : undefined}
              disabled={conversation.status === s}
              onClick={() => changeStatus(s)}
            >
              {s}
            </Button>
          ))}
        </div>
      </Section>

      <Section
        title="Transcript"
        hint="Everything the guest and the AI have said, in order."
      >
        <div className="transcript" ref={logRef}>
          {conversation.messages.map((m) => (
            <div key={m.id} className={"msg msg-" + m.sender}>
              <div className="msg-who">
                {SENDER_LABEL[m.sender] || m.sender} ·{" "}
                {shortDateTime(m.created_at)}
              </div>
              {m.content}
            </div>
          ))}
        </div>

        <form className="composer" onSubmit={handleSend}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Reply as staff — the guest sees this in the widget"
            aria-label="Reply to the guest"
            disabled={sending}
          />
          <Button
            type="submit"
            variant="primary"
            disabled={sending || !draft.trim()}
          >
            {sending ? "Sending…" : "Send"}
          </Button>
        </form>
      </Section>
    </>
  );
}
