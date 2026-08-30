import type { ReactNode } from "react";

/**
 * Shared staff dashboard shell. Navigation and the login guard land here
 * once auth exists; for now it just frames the page.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <div className="shell">{children}</div>;
}
