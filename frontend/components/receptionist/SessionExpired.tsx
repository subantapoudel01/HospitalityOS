"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { clearSession } from "@/lib/auth";

/**
 * Shown when the API rejects the session mid-use.
 *
 * Replaces TokenGate, which collected a shared secret and typed it into
 * localStorage. There is a real login now, so the right response to a
 * rejected call is to clear the dead cookie and send the person to it.
 *
 * Middleware cannot catch this case: it only sees the cookie, and a cookie
 * can be perfectly well-formed while the server rejects it - a rotated
 * JWT_SECRET, a deactivated account, an expiry that passed while the tab
 * sat open polling.
 */
export function SessionExpired({ message }: { message: string }) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    clearSession();
    const to = "/staff/login?next=" + encodeURIComponent(pathname || "/staff");
    // A beat so the reason is readable rather than a flash of text. The
    // dead cookie is already gone, so nothing is gated on this delay.
    const timer = setTimeout(() => router.replace(to), 1200);
    return () => clearTimeout(timer);
  }, [pathname, router]);

  return (
    <div className="token-gate">
      <h1 className="page-title">Signed out</h1>
      <div className="banner banner-error" role="alert">
        {message}
      </div>
      <p className="page-sub">Taking you to the sign-in page…</p>
    </div>
  );
}
