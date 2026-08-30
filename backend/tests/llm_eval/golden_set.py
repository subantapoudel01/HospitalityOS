"""
Golden set for retrieval evaluation (Stage 4).

Kept as data, separate from the assertions, so adding a regression case is
a one-line edit and the same corpus can feed later slices (answer quality,
escalation triggers) without being rewritten.
"""

DOCUMENTS: list[tuple[str, str, str]] = [
    (
        "Guest FAQ",
        "faq",
        """Q: Do you offer airport pickup?
A: Yes, we arrange a private car from Pokhara airport for NPR 800. Tell the front desk your flight number a day ahead.

Q: Is there Wi-Fi?
A: Free fibre Wi-Fi covers all rooms and the garden. The password is printed on your key card.

Q: Can I pay by card?
A: We accept Visa, Mastercard, eSewa and cash in NPR. Card payments carry no surcharge.""",
    ),
    (
        "Restaurant and dining",
        "amenity",
        """The rooftop restaurant, Machhapuchhre Terrace, is open from 6:30 AM until 10 PM daily.

Breakfast runs 6:30 to 10:00 and includes Nepali sets with dal bhat, continental options, eggs cooked to order, and filter coffee.

The dinner menu leans Newari, with wood-fired pizza for children. Vegetarian, vegan and Jain meals are prepared on request with two hours notice.""",
    ),
    (
        "Nearby attractions",
        "amenity",
        """Sarangkot is the best known sunrise viewpoint in the valley, about a 30 minute drive from the property. Cars leave at 4:45 AM.

The World Peace Pagoda sits across the lake. Most guests take a hand-rowed boat from the Lakeside jetty and then walk up for roughly 40 minutes.

Paragliding launches from Sarangkot most mornings between September and April. Tandem flights last around 30 minutes and land beside the lake.""",
    ),
    (
        "Checkin Checkout policy",
        "policy",
        "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM.",
    ),
    (
        "Cancellation policy",
        "policy",
        "Cancellation policy: Free cancellation up to 48 hours before arrival.",
    ),
    (
        "Pets policy",
        "policy",
        "Pets policy: Pets are not permitted on the property.",
    ),
]

# (question, substring that must appear in a retrieved chunk, language tag)
CASES: list[tuple[str, str, str]] = [
    ("When can I get into my room?", "Check-in is from 2 PM", "en"),
    ("What time is check-out?", "Check-out is by 11 AM", "en"),
    ("How do I get from the airport?", "airport for NPR 800", "en"),
    ("Somewhere to eat in the morning?", "Breakfast runs 6:30", "en"),
    ("Where do people watch the sunrise?", "Sarangkot", "en"),
    ("Am I allowed to bring my dog?", "Pets are not permitted", "en"),
    ("Can I use my credit card?", "Visa, Mastercard", "en"),
    ("I want to fly over the lake", "Paragliding", "en"),
    ("Is the internet free?", "Wi-Fi", "en"),
    ("Do you have vegetarian food?", "Vegetarian", "en"),
    # Devanagari
    ("चेक-आउट कति बजे हो?", "Check-out is by 11 AM", "ne_devanagari"),
    ("के मसँग पाल्तु जनावर ल्याउन मिल्छ?", "Pets are not permitted", "ne_devanagari"),
    ("म कति बजे कोठा छोड्नु पर्छ?", "Check-out is by 11 AM", "ne_devanagari"),
    # Romanized Nepali
    ("check out kati baje ho?", "Check-out is by 11 AM", "ne_romanized"),
    ("wifi ko password kaha cha?", "Wi-Fi", "ne_romanized"),
]

# Cases the current embedding model is KNOWN to fail. Tracked here rather
# than deleted, so the gap stays visible in test output and any model change
# can be measured against it. Removing an entry should only ever happen
# because the case started passing.
#
# paraphrase-multilingual-MiniLM-L12-v2 handles Devanagari unevenly against
# an English corpus: queries carrying a transliterated loanword
# ("चेक-आउट") retrieve well (~0.78), but fully native vocabulary
# ("पाल्तु जनावर" = pet animal) scores ~0.17 against everything, i.e. the
# query embedding lands nowhere near the English passage that answers it.
# RESOLVED AT THE CHAT LAYER IN SLICE D, BUT NOT HERE. The chat pipeline now
# translates Nepali to English before retrieval, which lifted this exact
# question from 0.17 (correct chunk not even in the top three) to 0.52 with
# the right answer. This file tests retrieval in isolation and does NOT
# translate, so the gap below is still real for anyone calling
# retrieval.search() directly with Nepali text. Keeping the entry honest
# rather than deleting it because the underlying encoder did not improve.
#
# A stronger multilingual encoder (multilingual-e5-large, 1024-dim, ~2.2GB)
# would close it at the retrieval layer too, at the cost of image size and a
# re-embed.
KNOWN_GAPS: set[str] = {
    "के मसँग पाल्तु जनावर ल्याउन मिल्छ?",
}

# Recall@3 is the number that matters: retrieval feeds top-k chunks to the
# chat model, so the correct passage being present in the returned set is
# what determines whether a right answer is possible at all. Top-1 accuracy
# is tracked separately as a quality signal, not as a gate.
MIN_RECALL_AT_3 = 1.0
MIN_TOP1_ACCURACY = 0.70
RETRIEVAL_K = 3
