"""
Receptionist reactions to platform events.

Importing this module is what wires them up; see api/__init__.py, which
main.py already imports to mount the routers.

The dependency points the right way: this module imports platform, and
platform knows nothing about it.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.hooks import on_hotel_saved


@on_hotel_saved
async def resync_knowledge_base(db: AsyncSession, hotel_id: int) -> None:
    """Push policies and room types into the knowledge base on every save.

    Without this, /setup writes to hotel_policies and room_types while the
    assistant keeps answering from whatever was last synced by hand. A
    staff member correcting a deposit rule sees "Saved", and guests are
    told the old rule indefinitely - there is nothing in the UI to suggest
    a second step exists.

    Imported lazily so that merely registering the listener does not pull
    the embedding model into every process that touches platform code.
    """
    from app.modules.receptionist.rag import ingest

    await ingest.sync_hotel_setup(db, hotel_id=hotel_id)
