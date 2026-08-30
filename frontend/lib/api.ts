// Thin API client for the platform endpoints.
//
// NOTE: no auth header yet — the backend has no users table or session.
// When auth lands, attach the token here and drop hotel ids from callers.

import type { HotelIn, HotelOut, HotelSummary } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Field-level errors keyed by a dotted path, e.g. "room_types.0.base_rate". */
export type FieldErrors = Record<string, string>;

export class ApiError extends Error {
  fieldErrors: FieldErrors;
  constructor(message: string, fieldErrors: FieldErrors = {}) {
    super(message);
    this.fieldErrors = fieldErrors;
  }
}

interface ValidationItem {
  loc: (string | number)[];
  msg: string;
}

/** Turn FastAPI's 422 `detail` array into a flat map the form can render. */
function parseValidation(detail: ValidationItem[]): FieldErrors {
  const out: FieldErrors = {};
  for (const item of detail) {
    // drop the leading "body" segment
    const path = item.loc.slice(1).join(".");
    if (path && !out[path]) out[path] = item.msg;
  }
  return out;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${BASE}. Is the backend running?`
    );
  }

  if (res.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error body */
  }

  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (res.status === 422 && Array.isArray(detail)) {
      throw new ApiError(
        "Please fix the highlighted fields.",
        parseValidation(detail as ValidationItem[])
      );
    }
    throw new ApiError(
      typeof detail === "string" ? detail : `Request failed (${res.status}).`
    );
  }
  return body as T;
}

export const listHotels = () => request<HotelSummary[]>("/api/platform/hotels");

export const getHotel = (id: number) =>
  request<HotelOut>(`/api/platform/hotels/${id}`);

export const createHotel = (payload: HotelIn) =>
  request<HotelOut>("/api/platform/hotels", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateHotel = (id: number, payload: HotelIn) =>
  request<HotelOut>(`/api/platform/hotels/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
