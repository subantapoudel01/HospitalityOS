"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { AuthError, login } from "@/lib/auth";

/**
 * Staff sign-in.
 *
 * Replaces TokenGate, which asked for a shared secret and said plainly
 * that it was not a login. This one is: the password is checked against a
 * bcrypt hash, the session is scoped to one property, and the dashboard
 * can finally attribute a transcript read to a person.
 */
function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Only ever an internal path. A full URL here would turn the login page
  // into an open redirect: /staff/login?next=https://evil.example.
  const raw = params.get("next") || "/staff";
  const next = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/staff";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      // replace, not push: the back button should not return to a login
      // form that now silently redirects forward again.
      router.replace(next);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : "Sign-in failed. Please try again."
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="token-gate">
      <h1 className="page-title">Staff sign-in</h1>
      <p className="page-sub">
        HospitalityOS staff dashboard. Guest conversations and booking
        inquiries are behind this page.
      </p>

      {error && (
        <div className="banner banner-error" role="alert">
          {error}
        </div>
      )}

      <form onSubmit={submit}>
        <Field label="Email" htmlFor="email" required>
          <input
            id="email"
            type="email"
            value={email}
            required
            autoFocus
            autoComplete="username"
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>

        <Field label="Password" htmlFor="password" required>
          <input
            id="password"
            type="password"
            value={password}
            required
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>

        <Button
          type="submit"
          variant="primary"
          disabled={busy || !email.trim() || !password}
        >
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <p className="field-hint" style={{ marginTop: "1rem" }}>
        No account yet? An administrator creates one with{" "}
        <code>make seed-admin</code> on the server. Passwords cannot be reset
        from this screen.
      </p>
    </div>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary, or the whole route opts out
  // of static rendering and Next fails the production build.
  return (
    <Suspense fallback={<p>Loading…</p>}>
      <LoginForm />
    </Suspense>
  );
}
