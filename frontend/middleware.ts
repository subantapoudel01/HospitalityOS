// Route guard for /staff.
//
// WHAT THIS IS: a redirect, so a signed-out person lands on the login page
// instead of watching a dashboard shell render and then error.
//
// WHAT THIS IS NOT: the security boundary. It checks that a cookie exists
// and has not expired. It does NOT verify the signature, because doing so
// would mean putting JWT_SECRET in the frontend container, widening the
// blast radius of the one value that makes every token forgeable.
//
// So a hand-written cookie with a future `exp` gets past this file and sees
// an empty dashboard: every piece of data on it comes from the API, and the
// API verifies the signature on every request. Middleware decides which
// screen to show. The backend decides what data exists.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const COOKIE_NAME = "hos_staff_session";
const LOGIN_PATH = "/staff/login";

/** Payload-only decode; see the note above on why there is no verification. */
function expiryOf(token: string): number | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    // Edge runtime has atob but not Buffer.
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const token = request.cookies.get(COOKIE_NAME)?.value ?? "";
  const exp = token ? expiryOf(token) : null;
  const signedIn = exp !== null && exp * 1000 > Date.now();

  // Already signed in and heading for the login page: send them on.
  if (pathname === LOGIN_PATH) {
    if (signedIn) {
      return NextResponse.redirect(new URL("/staff", request.url));
    }
    return NextResponse.next();
  }

  if (signedIn) return NextResponse.next();

  // Carry the destination so sign-in returns to the page they wanted
  // rather than dumping everyone on the queue.
  const login = new URL(LOGIN_PATH, request.url);
  login.searchParams.set("next", pathname + search);

  const response = NextResponse.redirect(login);
  // An expired cookie would otherwise sit there causing a redirect on
  // every navigation until it is manually cleared.
  if (token) response.cookies.delete(COOKIE_NAME);
  return response;
}

export const config = {
  // /staff and everything under it. Deliberately narrow: /widget is the
  // guest chat and must stay public, and /setup is covered separately
  // below once its API is gated too.
  matcher: ["/staff", "/staff/:path*"],
};
