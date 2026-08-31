"""
Platform events that modules can react to.

WHY THIS EXISTS RATHER THAN A DIRECT CALL
-----------------------------------------
Saving a hotel at /setup needs to refresh the receptionist's knowledge
base, or staff edit a policy, see it saved, and guests keep hearing the
old wording until somebody remembers to run sync-policies by hand. That
happened: a payment policy was corrected in the UI and the assistant went
on quoting the previous text.

The obvious fix - platform/api/routes.py calling
`receptionist.rag.ingest.sync_hotel_setup` - would invert the dependency
this repo is organised around. Dependencies run modules -> platform ->
core, and the moment platform imports a module, modules stop being
separable and the second module cannot be added without touching the
first.

So platform publishes an event and knows nothing about who listens.
Modules register themselves at import time (see
app/modules/receptionist/hooks.py). With no module loaded, nothing is
registered and saving a hotel simply saves a hotel.

FAILURE BEHAVIOUR IS DELIBERATE: listeners run inside the caller's
transaction, before the commit. A listener that raises takes the save
down with it. That is the intended trade - a save that "succeeded" while
its knowledge sync silently failed puts the two out of step, which is the
exact failure this exists to prevent, and the staff member would have no
way to know.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

HotelSavedListener = Callable[[AsyncSession, int], Awaitable[None]]

_hotel_saved: list[HotelSavedListener] = []


def on_hotel_saved(listener: HotelSavedListener) -> HotelSavedListener:
    """Register a listener. Usable as a decorator.

    Idempotent by identity so a module imported twice - which happens
    under some test layouts - does not run its sync twice per save.
    """
    if listener not in _hotel_saved:
        _hotel_saved.append(listener)
    return listener


async def hotel_saved(db: AsyncSession, hotel_id: int) -> None:
    """Announce that a hotel's profile, room types or policies changed.

    Called BEFORE commit, so a listener's writes land in the same
    transaction as the save itself: either both happen or neither does.
    """
    for listener in _hotel_saved:
        logger.debug("hotel_saved -> %s (hotel %s)", listener.__name__, hotel_id)
        await listener(db, hotel_id)


def clear_listeners() -> None:
    """Test helper. Never call this from application code."""
    _hotel_saved.clear()
