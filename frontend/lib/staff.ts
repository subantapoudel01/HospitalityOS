// Staff dashboard API client.
//
// Every call carries X-Staff-Token. That token is a shared deployment gate,
// not authentication — see backend/app/core/auth.py. It lives in
// localStorage, which means anyone with access to the browser has it; that
// is acceptable for a pilot behind a gate and not acceptable once real
// staff accounts exist.

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const STORAGE_KEY = "hospitalityos.staffToken";

export type ConversationStatus = "active" | "escalated" | "resolved";
export type InquiryStatus = "new" | "contacted" | "confirmed" | "lost";

export const CONVERSATION_STATUSES: ConversationStatus[] = [
  "active",
  "escalated",
  "resolved",
];
export const INQUIRY_STATUSES: InquiryStatus[] = [
  "new",
  "contacted",
  "confirmed",
  "lost",
];

export interface Metrics {
  conversations_total: number;
  escalated: number;
  resolved: number;
  inquiries_new: number;
}

export interface ConversationSummary {
  id: number;
  status: ConversationStatus;
  channel: string;
  started_at: string;
  resolved_at: string | null;
  message_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  awaiting_staff: boolean;
}

export interface TranscriptMessage {
  id: number;
  sender: "guest" | "ai" | "staff";
  content: string;
  language_detected: string | null;
  created_at: string;
}

export interface Conversation {
  id: number;
  hotel_id: number;
  channel: string;
  status: ConversationStatus;
  started_at: string;
  resolved_at: string | null;
  messages: TranscriptMessage[];
}

export interface BookingInquiry {
  id: number;
  conversation_id: number;
  hotel_id: number;
  check_in_date: string;
  check_out_date: string;
  guest_count: number;
  room_type_preference: string | null;
  status: InquiryStatus;
  raw_request: string | null;
  created_at: string;
}

export interface KnowledgeIssue {
  source: string;
  source_id: number;
  title: string;
  severity: "high" | "low";
  code: string;
  message: string;
  excerpt: string;
}

export interface KnowledgeHealth {
  hotel_id: number;
  documents_checked: number;
  policies_checked: number;
  issues: KnowledgeIssue[];
}

export interface Degradation {
  degraded: boolean;
  window_minutes: number;
  events: number;
  last_at: string | null;
  providers: string[];
  by_purpose: Record<string, number>;
}

export class StaffApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
  /** 401 means a bad token; 503 means the server has no token configured. */
  get isAuthProblem() {
    return this.status === 401 || this.status === 503;
  }
}

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return (
    window.localStorage.getItem(STORAGE_KEY) ||
    process.env.NEXT_PUBLIC_STAFF_TOKEN ||
    ""
  );
}

export function setToken(token: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, token.trim());
}

export function clearToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Staff-Token": getToken(),
        ...(init?.headers || {}),
      },
    });
  } catch {
    throw new StaffApiError(
      "Cannot reach the API. Is the backend running?",
      0
    );
  }

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new StaffApiError(
      typeof detail === "string" ? detail : "Request failed (" + res.status + ").",
      res.status
    );
  }
  return body as T;
}

const q = (hotelId: number, status?: string) =>
  "?hotel_id=" + hotelId + (status ? "&status=" + status : "");

export const getMetrics = (hotelId: number) =>
  request<Metrics>("/api/receptionist/staff/metrics?hotel_id=" + hotelId);

export const listConversations = (hotelId: number, status?: string) =>
  request<ConversationSummary[]>(
    "/api/receptionist/staff/conversations" + q(hotelId, status)
  );

export const getConversation = (hotelId: number, id: number) =>
  request<Conversation>(
    "/api/receptionist/conversations/" + id + "?hotel_id=" + hotelId
  );

export const setConversationStatus = (
  hotelId: number,
  id: number,
  status: ConversationStatus
) =>
  request<Conversation>("/api/receptionist/staff/conversations/" + id, {
    method: "PATCH",
    body: JSON.stringify({ hotel_id: hotelId, status }),
  });

export const sendStaffMessage = (hotelId: number, id: number, content: string) =>
  request<Conversation>(
    "/api/receptionist/staff/conversations/" + id + "/messages",
    { method: "POST", body: JSON.stringify({ hotel_id: hotelId, content }) }
  );

export const listInquiries = (hotelId: number, status?: string) =>
  request<BookingInquiry[]>(
    "/api/receptionist/staff/booking-inquiries" + q(hotelId, status)
  );

export const setInquiryStatus = (
  hotelId: number,
  id: number,
  status: InquiryStatus
) =>
  request<BookingInquiry>(
    "/api/receptionist/staff/booking-inquiries/" + id,
    { method: "PATCH", body: JSON.stringify({ hotel_id: hotelId, status }) }
  );

export const getKnowledgeHealth = (hotelId: number) =>
  request<KnowledgeHealth>(
    "/api/receptionist/staff/knowledge/health?hotel_id=" + hotelId
  );

export const getDegradation = (hotelId: number) =>
  request<Degradation>(
    "/api/receptionist/staff/degradation?hotel_id=" + hotelId
  );
