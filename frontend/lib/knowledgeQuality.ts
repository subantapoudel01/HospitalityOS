// Mirror of backend/app/modules/receptionist/rag/quality.py.
//
// Duplicated deliberately: staff need the warning while they are still
// looking at the field, and a round trip per keystroke is not worth it. The
// backend check remains authoritative and runs on ingest regardless.
//
// Keep the two in step. The rule exists because a policy field containing
// "10/12" made the assistant state "Check-out is at 12:00 pm" as fact.

const MEANING_WORD = /[^\W\d_]{2,}/gu;
const MIN_MEANING_WORDS = 2;
const SHORT_ENTRY_CHARS = 25;

export interface QualityWarning {
  code: string;
  severity: "high" | "low";
  message: string;
}

export function assessEntry(text: string, label = "This entry"): QualityWarning[] {
  const content = (text || "").trim();
  if (!content) return [];

  const warnings: QualityWarning[] = [];
  const words = content.match(MEANING_WORD) || [];

  if (words.length < MIN_MEANING_WORDS) {
    warnings.push({
      code: "not_readable",
      severity: "high",
      message:
        `${label} does not read as a sentence, so the assistant cannot ` +
        `answer questions from it and may guess at what it means. Write it ` +
        `the way you would say it to a guest — for example "Check-in is ` +
        `from 2 PM and check-out is by 11 AM."`,
    });
  } else if (content.length < SHORT_ENTRY_CHARS) {
    warnings.push({
      code: "very_short",
      severity: "low",
      message:
        `${label} is very short. A little more detail helps the assistant ` +
        `answer follow-up questions.`,
    });
  }

  if (!content.replace(/[\s\d/\-.:,]+/g, "")) {
    warnings.push({
      code: "numbers_only",
      severity: "high",
      message:
        `${label} is only numbers, so its meaning is ambiguous — "10/12" ` +
        `could be hours, dates, or a month and day. Spell it out in words.`,
    });
  }

  return warnings;
}

export function worstSeverity(warnings: QualityWarning[]): "high" | "low" | null {
  if (warnings.some((w) => w.severity === "high")) return "high";
  return warnings.length ? "low" : null;
}
