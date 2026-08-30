"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { setToken } from "@/lib/staff";

/**
 * Shown when the API rejects the staff token.
 *
 * Deliberately does not pretend to be a login: there are no accounts yet.
 * It collects the shared secret from .env so a human can get in, and says
 * as much rather than implying identity it does not have.
 */
export function TokenGate({
  message,
  onSaved,
}: {
  message: string;
  onSaved: () => void;
}) {
  const [value, setValue] = useState("");

  return (
    <div className="token-gate">
      <h1 className="page-title">Staff access</h1>
      <div className="banner banner-error" role="alert">
        {message}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setToken(value);
          onSaved();
        }}
      >
        <Field
          label="Staff token"
          htmlFor="staff-token"
          hint="The STAFF_API_TOKEN value from .env. This is a shared secret, not a personal login — there are no staff accounts yet."
        >
          <input
            id="staff-token"
            type="password"
            value={value}
            autoComplete="off"
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        <Button type="submit" variant="primary" disabled={!value.trim()}>
          Continue
        </Button>
      </form>
    </div>
  );
}
