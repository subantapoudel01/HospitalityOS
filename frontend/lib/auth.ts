// Staff session handling.
//
// The token lives in a cookie rather than localStorage for one concrete
// reason: Next.js middleware runs before the page renders and can only read
// cookies, so a localStorage token cannot gate a route without first
// flashing the protected page. The same cookie is read here to build the
// Authorization header for API calls.
//
// The cookie is NOT httpOnly, which means an XSS in this dashboard can
// steal a session. That is a real cost, accepted because the UI and the API
// are separate origins today. See backend/app/platform/api/auth_routes.py
// for the full reasoning and the conditions under which it should change.
//
// NONE OF THIS IS THE SECURITY BOUNDARY. The backend verifies the
// signature on every request. Everything in this file is about showing the
// right screen, not about deciding who is allowed in.

import { API_BASE } from "./apiBase";

const BASE = API_BASE;

export const COOKIE_NAME = "hos_staff_session";

export interface StaffUser {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  hotel_id: number | null;
  is_active: boolean;
  last_login_at: string | null;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: StaffUser;
}

export class AuthError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// --- cookie plumbing -----------------------------------------------------

export function readToken(): string {
  if (typeof document === "undefined") return "";
  const prefix = COOKIE_NAME + "=";
  for (const part of document.cookie.split("; ")) {
    if (part.startsWith(prefix)) {
      return decodeURIComponent(part.slice(prefix.length));
    }
  }
  return "";
}

function writeToken(token: string, expiresAt: string) {
  if (typeof document === "undefined") return;
  const expires = new Date(expiresAt);
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    COOKIE_NAME +
    "=" +
    encodeURIComponent(token) +
    "; Path=/; SameSite=Lax; Expires=" +
    expires.toUTCString() +
    secure;
}

export function clearSession() {
  if (typeof document === "undefined") return;
  document.cookie =
    COOKIE_NAME + "=; Path=/; SameSite=Lax; Max-Age=0";
}

/** Header for API calls. Empty object when signed out, so callers can spread
 *  it unconditionally rather than branching at every call site. */
export function authHeader(): Record<string, string> {
  const token = readToken();
  return token ? { Authorization: "Bearer " + token } : {};
}

// --- claims --------------------------------------------------------------

export interface Claims {
  sub: string;
  email?: string;
  role?: string;
  hotel_id?: number | null;
  exp: number;
}

/**
 * Read the payload WITHOUT verifying the signature.
 *
 * Only ever used to decide which screen to show and which hotel id to
 * default to. A forged token gets past this and is then rejected by the
 * backend, which is the only place the signature is actually checked.
 */
export function decodeClaims(token: string): Claims | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const claims = JSON.parse(json) as Claims;
    return typeof claims?.exp === "number" ? claims : null;
  } catch {
    return null;
  }
}

export function isExpired(claims: Claims | null): boolean {
  if (!claims) return true;
  // 30s of slack for clock skew between the browser and the API host.
  return claims.exp * 1000 <= Date.now() - 30_000;
}

export function currentHotelId(fallback: number): number {
  const claims = decodeClaims(readToken());
  return typeof claims?.hotel_id === "number" ? claims.hotel_id : fallback;
}

// --- API -----------------------------------------------------------------

export async function login(
  email: string,
  password: string
): Promise<LoginResult> {
  let res: Response;
  try {
    res = await fetch(BASE + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The API also sets the cookie itself, which works when it is served
      // from this origin behind the reverse proxy. Cross-origin in dev it
      // may not stick, so the token is written from the body below too.
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new AuthError("Cannot reach the API. Is the backend running?", 0);
  }

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new AuthError(
      typeof detail === "string" ? detail : "Sign-in failed (" + res.status + ").",
      res.status
    );
  }

  const result = body as LoginResult;
  writeToken(result.access_token, result.expires_at);
  return result;
}

export async function logout(): Promise<void> {
  // Clear locally first. If the network call fails the session must still
  // be gone from this browser - a logout button that leaves you logged in
  // when the API is down is worse than no button.
  clearSession();
  try {
    await fetch(BASE + "/api/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: authHeader(),
    });
  } catch {
    /* already cleared locally */
  }
}

export interface Session {
  authenticated: boolean;
  method: string;
  user: StaffUser | null;
  hotel_id: number | null;
  role: string;
}

/** Ask the server who it thinks we are. Catches a token signed with a
 *  rotated secret, or one belonging to a since-deactivated account. */
export async function fetchSession(): Promise<Session> {
  const res = await fetch(BASE + "/api/auth/me", {
    credentials: "include",
    headers: authHeader(),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new AuthError(
      typeof detail === "string" ? detail : "Not signed in.",
      res.status
    );
  }
  return body as Session;
}
