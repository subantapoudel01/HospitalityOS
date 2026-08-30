"use client";

import { useEffect, useState } from "react";

export default function Home() {
  const [platform, setPlatform] = useState<string>("checking...");
  const [receptionist, setReceptionist] = useState<string>("checking...");

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    fetch(`${apiUrl}/health`)
      .then((res) => res.json())
      .then((data) => setPlatform(data.status ?? "unknown"))
      .catch(() => setPlatform("unreachable"));

    // Proves the receptionist module is mounted into the platform app.
    fetch(`${apiUrl}/api/receptionist/status`)
      .then((res) => res.json())
      .then((data) => setReceptionist(data.status ?? "unknown"))
      .catch(() => setReceptionist("unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "sans-serif", padding: "3rem" }}>
      <h1>HospitalityOS</h1>
      <p>Platform foundation — Stage 2 bootstrap.</p>
      <ul>
        <li>
          Platform API: <strong>{platform}</strong>
        </li>
        <li>
          Receptionist module: <strong>{receptionist}</strong>
        </li>
      </ul>
      <p>
        <a href="/setup">Go to resort setup</a>
        {" | "}
        <a href="/widget">Open the guest chat widget</a>
        {" | "}
        <a href="/staff">Staff dashboard</a>
      </p>
    </main>
  );
}
