"""
Fixed guest-facing replies, in one place.

These are deliberately not generated. Each one either states a fact about
what the system just did (a staff member has been notified) or declines to
answer. A model paraphrasing them can promise something that did not
happen - which is exactly the bug this module was extracted to fix: asked
"yes" after a handoff offer, the classifier replied "Sure, I'll forward your
request to our team" while nothing was forwarded and the conversation stayed
active.

Lives in its own module because both intent.py and conversation.py need
these strings, and importing one from the other would be circular.
"""
from __future__ import annotations

from app.modules.receptionist.services.language import Language

# Shown when nothing clears the similarity floor. Note the trailing offer:
# answering "yes" to it is an escalation request, which is why the exact
# text matters to is_handoff_offer() below.
REFUSALS: dict[Language, str] = {
    Language.en: (
        "I do not have that information in this hotel's knowledge base, so I "
        "do not want to guess. Would you like me to pass this to a staff "
        "member?"
    ),
    Language.ne_romanized: (
        "Malai yo kura hotel ko jankari ma bhetina, tyasaile ma anuman garna "
        "chahanna. Ke ma staff lai sodhi dinu?"
    ),
    Language.ne_devanagari: (
        "यो जानकारी होटेलको ज्ञानभण्डारमा छैन, त्यसैले म अनुमान गर्न चाहन्न। "
        "के म स्टाफलाई सोधिदिऊँ?"
    ),
}

# Sent the moment a conversation is actually flagged for staff - by the
# widget button or by the guest asking in chat. Both paths use this, so the
# guest sees the same thing either way.
ESCALATION_CONFIRMED: dict[Language, str] = {
    Language.en: (
        "I have notified a staff member. They can see this conversation and "
        "will reply here shortly."
    ),
    Language.ne_romanized: (
        "Maile staff lai khabar gareko chu. Wahaharu le yo kura herna "
        "sakchan ra chandai yahi jawaf dinu hunecha."
    ),
    Language.ne_devanagari: (
        "मैले स्टाफलाई खबर गरेको छु। उहाँहरूले यो कुराकानी हेर्न सक्नुहुन्छ "
        "र चाँडै यहीँ जवाफ दिनुहुनेछ।"
    ),
}

# Once escalated, the AI stops answering. Talking over a human who is about
# to reply is the main way a handoff goes wrong, so subsequent guest
# messages are recorded and acknowledged rather than answered.
STAND_DOWN: dict[Language, str] = {
    Language.en: (
        "A staff member has been notified and can see everything you write "
        "here. I will let them answer this one."
    ),
    Language.ne_romanized: (
        "Staff lai khabar gari sakeko cha ra wahaharu le tapai le lekheko "
        "sabai dekhna sakchan. Yo jawaf wahaharu bata aaunecha."
    ),
    Language.ne_devanagari: (
        "स्टाफलाई खबर गरिसकिएको छ र उहाँहरूले तपाईंले लेख्नुभएको सबै "
        "देख्न सक्नुहुन्छ। यसको जवाफ उहाँहरूबाट आउनेछ।"
    ),
}

# The extractive provider appends this when it cannot compose an answer.
# It is also a handoff offer, so "yes" after it means the same thing.
EXTRACTIVE_HANDOFF_TAIL = (
    "If that does not answer your question, I can pass you to a staff member."
)


def _normalise(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


_OFFER_TEXTS = {_normalise(t) for t in REFUSALS.values()}
_OFFER_TAIL = _normalise(EXTRACTIVE_HANDOFF_TAIL)


def is_handoff_offer(text: str) -> bool:
    """Did this assistant message offer to fetch a human?

    Matched against the exact fixed strings rather than by looking for words
    like "staff", because a knowledge-base answer can easily mention staff
    without offering anything. A false positive here turns an innocent "yes"
    into an escalation.
    """
    cleaned = _normalise(text)
    if not cleaned:
        return False
    return cleaned in _OFFER_TEXTS or cleaned.endswith(_OFFER_TAIL)
