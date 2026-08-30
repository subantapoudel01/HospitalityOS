import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "HospitalityOS",
  description: "Hospitality platform for small hotels",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      {/* Browser extensions inject attributes into <body> before React
          hydrates, which React reports as a mismatch. This suppresses
          that warning for this element's attributes only, one level
          deep - it does not hide hydration bugs in our own components. */}
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
