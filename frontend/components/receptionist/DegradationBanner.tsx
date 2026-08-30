import { shortDateTime } from "@/lib/format";
import type { Degradation } from "@/lib/staff";

/**
 * Shown when recent turns ran on deterministic rules because a model call
 * failed - usually the daily token cap.
 *
 * Says what it means for guests rather than just naming a provider. When
 * this is up, booking requests can come back as refusals and Nepali
 * questions are searched untranslated, and staff have no other way to know.
 */

const CONSEQUENCE: Record<string, string> = {
  classification:
    "guest messages are being routed by keyword rules, so booking requests " +
    "and less common phrasings can come back as refusals",
  translation:
    "Nepali questions are being searched without translation, which finds " +
    "the right answer far less often",
  chat: "replies are coming from retrieved passages verbatim rather than being written",
};

export function DegradationBanner({ state }: { state: Degradation }) {
  if (!state.degraded) return null;

  const affected = Object.keys(state.by_purpose);
  const provider = state.providers.join(", ") || "the AI provider";

  return (
    <div className="banner banner-degraded" role="alert">
      <div className="degraded-head">
        Running on fallback rules — answers are degraded
      </div>
      <p className="degraded-body">
        {state.events} turn{state.events === 1 ? "" : "s"} in the last{" "}
        {state.window_minutes} minutes fell back because a call to {provider}{" "}
        failed
        {state.last_at ? `, most recently ${shortDateTime(state.last_at)}` : ""}.
      </p>
      <ul className="degraded-list">
        {affected.map((purpose) => (
          <li key={purpose}>
            <strong>{purpose}</strong> ({state.by_purpose[purpose]}):{" "}
            {CONSEQUENCE[purpose] || "running on rules instead of the model"}
          </li>
        ))}
      </ul>
      <p className="degraded-body">
        Usually the daily token cap. Rotate <code>GROQ_API_KEY</code> in{" "}
        <code>.env</code> and restart the backend, or wait for the daily
        reset. Conversations from this period are worth re-reading.
      </p>
    </div>
  );
}
