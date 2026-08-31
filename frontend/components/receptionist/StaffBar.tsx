"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { decodeClaims, logout, readToken } from "@/lib/auth";

/**
 * Who is signed in, and the way out.
 *
 * The email comes from the token's own claims rather than a /me call: it
 * is only a label, and one more request on every dashboard mount to render
 * a string the token already carries is not worth it. Anything that
 * actually matters is checked server-side.
 */
export function StaffBar() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");

  // Read after mount, never during render: the cookie does not exist on the
  // server, so touching it in the render body is a hydration mismatch.
  useEffect(() => {
    const claims = decodeClaims(readToken());
    setEmail(claims?.email || "");
    setRole(claims?.role || "");
  }, []);

  if (!email) return null;

  return (
    <div className="staff-bar">
      <span className="staff-bar-who">
        {email}
        {role && <span className="staff-bar-role">{role.replace("_", " ")}</span>}
      </span>
      <Button
        type="button"
        variant="link"
        onClick={async () => {
          await logout();
          router.replace("/staff/login");
        }}
      >
        Sign out
      </Button>
    </div>
  );
}
