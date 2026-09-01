// Chat API client for the guest widget.
//
// No auth header: the widget is public by design. hotel_id identifies the
// property; see the security note on the backend routes.

import { API_BASE } from "./apiBase";

const BASE = API_BASE;

export interface Citation {
  chunk_id: number;
  document_id: number;
  document_title: string;
  score: number;
}

export type ChatIntent =
  | "smalltalk"
  | "answer"
  | "refusal"
  | "booking"
  | "escalation"
  | "stood_down";

export type ConversationStatus = "active" | "escalated" | "resolved";

export type DetectedLanguage = "en" | "ne_romanized" | "ne_devanagari";

export const LANGUAGE_LABELS: Record<DetectedLanguage, string> = {
  en: "English",
  ne_romanized: "Nepali (Romanized)",
  ne_devanagari: "नेपाली",
};

export interface ChatReply {
  conversation_id: number;
  reply: string;
  intent: ChatIntent;
  conversation_status: ConversationStatus;
  language: DetectedLanguage;
  /** The English text actually searched, when the question was translated. */
  search_text: string | null;
  grounded: boolean;
  citations: Citation[];
  provider: string;
  model: string;
  latency_ms: number;
  top_score: number | null;
}

export type Sender = "guest" | "ai" | "staff";

export interface TranscriptMessage {
  id: number;
  sender: Sender;
  content: string;
  language_detected: string | null;
  created_at: string;
}

export interface Transcript {
  id: number;
  hotel_id: number;
  channel: string;
  status: "active" | "escalated" | "resolved";
  started_at: string;
  resolved_at: string | null;
  messages: TranscriptMessage[];
}

export class ChatApiError extends Error {}

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ChatApiError(
      "Cannot reach the assistant. Is the backend running?"
    );
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new ChatApiError(
      typeof detail === "string" ? detail : "Request failed (" + res.status + ")."
    );
  }
  return data as T;
}

export const sendMessage = (
  hotelId: number,
  message: string,
  conversationId: number | null
) =>
  post<ChatReply>("/api/receptionist/chat", {
    hotel_id: hotelId,
    message,
    conversation_id: conversationId,
  });

export const requestHuman = (hotelId: number, conversationId: number) =>
  post<Transcript>(
    "/api/receptionist/conversations/" + conversationId + "/request-human",
    { hotel_id: hotelId }
  );

export async function getTranscript(
  hotelId: number,
  conversationId: number
): Promise<Transcript> {
  const res = await fetch(
    BASE +
      "/api/receptionist/conversations/" +
      conversationId +
      "?hotel_id=" +
      hotelId
  );
  if (!res.ok) throw new ChatApiError("Could not load the conversation.");
  return (await res.json()) as Transcript;
}
