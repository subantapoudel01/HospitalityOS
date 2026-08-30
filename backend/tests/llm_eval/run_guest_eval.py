"""
Run the guest golden set against a live, running system.

    python -m tests.llm_eval.run_guest_eval --hotel-id 1
    python -m tests.llm_eval.run_guest_eval --hotel-id 1 --seed

This is a reporting tool, not a pytest run. It calls the real API with the
real provider and prints what happened, because the questions it answers -
does this hallucinate, does it route correctly - are only meaningful against
the live stack.

`--seed` creates a fresh hotel and ingests the representative corpus first,
so retrieval quality can be measured. Without it, whatever the named hotel
already holds is used untouched.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import unicodedata
from dataclasses import dataclass

import httpx

from tests.llm_eval.guest_golden_set import CASES, SEEDED_CORPUS, SEEDED_ONLY, GuestCase

REFUSAL_MARKERS = (
    "do not have", "don't have", "no information", "staff member",
    "cannot confirm", "unable to", "not listed", "do not offer",
    "don't offer", "no record", "afraid", "not able to", "does not mention",
    "no mention", "not something", "cannot help", "can't help",
    "जानकारी", "छैन", "staff lai", "bhetina",
)

# Concrete claims that would be fabrication if the corpus cannot support
# them. Checked only on out-of-bounds and off-topic cases.
INVENTION_MARKERS = ("NPR", "AM", "PM", "o'clock", "%", "free of charge")



# Models format facts in ways a naive substring check misses: "NPR 4,500"
# for 4500, a non-breaking hyphen in "Wi-Fi", Devanagari numerals for
# digits. Normalising before comparison stops the grader reporting correct
# answers as failures - which it did on the first run.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_DEVANAGARI_DIGITS)
    # Any dash-like character becomes a plain hyphen.
    text = re.sub(r"[‐-―−]", "-", text)
    # Digit grouping: 4,500 -> 4500
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    return text.lower()


@dataclass
class Result:
    case: GuestCase
    intent: str
    language: str
    status: str
    reply: str
    citations: list[str]
    top_score: float | None
    latency_ms: int
    error: str | None = None

    @property
    def refused(self) -> bool:
        """Did it decline in SUBSTANCE, whatever the intent label says?

        The grounded path can produce a perfectly good refusal while the
        intent is reported as "answer": retrieval returned something above
        the floor, and the grounding prompt then correctly declined. Judging
        that as a failure punishes exactly the behaviour we want.
        """
        low = norm(self.reply)
        return self.intent == "refusal" or any(
            norm(m) in low for m in REFUSAL_MARKERS
        )


class Client:
    def __init__(self, base: str, hotel_id: int, token: str = ""):
        self.base = base.rstrip("/")
        self.hotel_id = hotel_id
        self.token = token
        self.http = httpx.Client(timeout=180)

    def chat(self, message: str, conversation_id: int | None = None) -> dict:
        body = {"hotel_id": self.hotel_id, "message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        r = self.http.post(f"{self.base}/api/receptionist/chat", json=body)
        r.raise_for_status()
        return r.json()

    def ingest(self, title: str, source_type: str, content: str) -> None:
        r = self.http.post(
            f"{self.base}/api/receptionist/knowledge/documents",
            json={
                "hotel_id": self.hotel_id,
                "title": title,
                "source_type": source_type,
                "raw_content": content,
            },
        )
        r.raise_for_status()

    def create_hotel(self, name: str) -> int:
        r = self.http.post(
            f"{self.base}/api/platform/hotels",
            json={"name": name, "city": "Pokhara", "room_types": [], "policies": []},
        )
        r.raise_for_status()
        return r.json()["id"]


def run_case(client: Client, case: GuestCase, pace: float) -> Result:
    try:
        conversation_id = None
        for prior in case.setup:
            conversation_id = client.chat(prior, conversation_id)["conversation_id"]
            time.sleep(pace)
        data = client.chat(case.message, conversation_id)
        return Result(
            case=case,
            intent=data["intent"],
            language=data["language"],
            status=data["conversation_status"],
            reply=data["reply"],
            citations=[c["document_title"] for c in data.get("citations", [])],
            top_score=data.get("top_score"),
            latency_ms=data.get("latency_ms", 0),
        )
    except Exception as exc:  # noqa: BLE001 - a failed call is a result too
        return Result(
            case=case, intent="ERROR", language="", status="", reply="",
            citations=[], top_score=None, latency_ms=0, error=str(exc)[:160],
        )


def grade(result: Result, *, seeded: bool) -> tuple[str, list[str]]:
    """Return (verdict, notes). REVIEW means a human should read the reply."""
    case = result.case
    notes: list[str] = []

    if result.error:
        return "ERROR", [result.error]

    # A case marked seeded-only is out of bounds when the corpus lacks it.
    answerable = seeded or SEEDED_ONLY not in case.note
    expected = case.expect_intent
    if not answerable and expected == "answer":
        expected = "refusal"
        notes.append("fact absent from this corpus, refusal is correct")

    # Grade on behaviour, not on the intent label. A refusal delivered
    # through the answer path is still a refusal.
    if expected == "refusal":
        routing_ok = result.refused
        if routing_ok and result.intent != "refusal":
            notes.append(f"(refused via intent={result.intent})")
    else:
        routing_ok = result.intent == expected
    if not routing_ok:
        notes.append(f"routed {result.intent}, expected {expected}")

    if case.expect_language and result.language != case.expect_language:
        notes.append(f"language {result.language}, expected {case.expect_language}")

    # Fabrication checks.
    low = norm(result.reply)
    for bad in case.forbidden:
        if norm(bad) in low:
            notes.append(f"FORBIDDEN string present: {bad!r}")
            return "FAIL", notes

    if case.kind in ("out_of_bounds", "off_topic") and not result.refused:
        invented = [m for m in INVENTION_MARKERS if norm(m) in low]
        notes.append(
            "answered instead of refusing"
            + (f"; contains {invented}" if invented else "")
        )
        return "FAIL" if invented else "REVIEW", notes

    if expected == "answer" and result.intent == "answer":
        missing = [m for m in case.must_contain if norm(m) not in low]
        if missing:
            notes.append(f"missing expected content: {missing}")
            return "FAIL", notes

    if expected in ("smalltalk", "escalation") and case.must_contain:
        missing = [m for m in case.must_contain if norm(m) not in low]
        if missing:
            notes.append(f"missing expected content: {missing}")
            return "FAIL", notes

    return ("PASS" if routing_ok else "FAIL"), notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--hotel-id", type=int, default=1)
    ap.add_argument("--seed", action="store_true",
                    help="create a new hotel and ingest the reference corpus")
    ap.add_argument("--pace", type=float, default=0.4,
                    help="seconds between calls, to stay under rate limits")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    args = ap.parse_args()

    client = Client(args.base, args.hotel_id)

    if args.seed:
        hotel_id = client.create_hotel("Golden Set Reference Resort")
        client.hotel_id = hotel_id
        print(f"seeded hotel {hotel_id}")
        for title, source_type, content in SEEDED_CORPUS:
            client.ingest(title, source_type, content)
            print(f"  ingested {title}")
        print()

    cases = CASES
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c.id in wanted]

    label = "SEEDED REFERENCE CORPUS" if args.seed else f"HOTEL {args.hotel_id} (real data)"
    print("=" * 78)
    print(f"GUEST GOLDEN SET - {label}   {len(cases)} cases")
    print("=" * 78)

    results: list[tuple[Result, str, list[str]]] = []
    for case in cases:
        result = run_case(client, case, args.pace)
        verdict, notes = grade(result, seeded=args.seed)
        results.append((result, verdict, notes))
        print(f"\n[{verdict:6}] {case.id:8} {case.kind:14} {case.message[:52]}")
        print(f"          intent={result.intent:10} lang={result.language:14} "
              f"top={result.top_score}")
        print(f"          reply: {result.reply[:150]}")
        if result.citations:
            print(f"          cites: {result.citations}")
        for note in notes:
            print(f"          note : {note}")
        time.sleep(args.pace)

    print("\n" + "=" * 78)
    by_verdict: dict[str, int] = {}
    for _, verdict, _ in results:
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    total = len(results)
    for verdict in ("PASS", "REVIEW", "FAIL", "ERROR"):
        n = by_verdict.get(verdict, 0)
        if n:
            print(f"{verdict:6} {n:3}/{total}  ({n / total:.0%})")

    print("\nBY CASE CLASS")
    classes: dict[str, list[str]] = {}
    for result, verdict, _ in results:
        classes.setdefault(result.case.kind, []).append(verdict)
    for kind, verdicts in sorted(classes.items()):
        passed = sum(1 for v in verdicts if v == "PASS")
        print(f"  {kind:14} {passed}/{len(verdicts)} pass")

    problems = [(r, v, n) for r, v, n in results if v in ("FAIL", "REVIEW", "ERROR")]
    if problems:
        print("\nNEEDS ATTENTION")
        for result, verdict, notes in problems:
            print(f"  [{verdict}] {result.case.id} {result.case.message[:56]}")
            print(f"        -> {result.reply[:150]}")
            for note in notes:
                print(f"        !! {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
