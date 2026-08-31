"""
Saving a hotel refreshes the knowledge base.

The failure this closes, observed for real: a payment policy was corrected
in the /setup UI, the form said Saved, and the assistant went on quoting
the previous wording. `sync-policies` had to be called separately and
nothing in the UI suggested a second step existed - so the knowledge base
silently drifted from what staff believed they had published.

Also guards the architecture. platform must not import from a module, so
the sync is reached through a listener the receptionist module registers
on itself (app/platform/hooks.py). If someone later "simplifies" that into
a direct import, modules stop being separable.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import model_router
from app.modules.receptionist.models import KnowledgeDocument
from app.platform import hooks
from app.platform.api import routes as platform_routes
from app.platform.models import Hotel, PolicyCategory
from app.platform.schemas import HotelIn, PolicyIn, RoomTypeIn

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _local_only(monkeypatch):
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")


@pytest.fixture(autouse=True)
def _listeners_registered():
    """Importing the module's api package is what registers them; do it
    explicitly so this file passes whatever order pytest collects in."""
    import app.modules.receptionist.hooks  # noqa: F401

    assert hooks._hotel_saved, "the receptionist listener should be registered"


def _payload(**overrides) -> HotelIn:
    data = {
        "name": "Rupakot Test",
        "city": "Pokhara",
        "room_types": [
            RoomTypeIn(
                name="Deluxe Room", base_rate="10500.00", max_occupancy=2,
                amenities=["Mountain View Balcony"],
            )
        ],
        "policies": [
            PolicyIn(
                category=PolicyCategory.payment,
                content_text=(
                    "We accept cash in NPR and digital payments via eSewa. "
                    "A 20% advance deposit is required."
                ),
            )
        ],
    }
    data.update(overrides)
    return HotelIn(**data)


async def _documents(db, hotel_id) -> dict[str, str]:
    rows = await db.execute(
        select(KnowledgeDocument.title, KnowledgeDocument.raw_content).where(
            KnowledgeDocument.hotel_id == hotel_id
        )
    )
    return {title: content for title, content in rows.all()}


# --- the fix -------------------------------------------------------------


async def test_creating_a_hotel_populates_the_knowledge_base(db):
    hotel = await platform_routes.create_hotel(_payload(), db)
    docs = await _documents(db, hotel.id)

    assert any("Payment" in t for t in docs)
    assert any("Deluxe Room" in t for t in docs)


async def test_editing_a_policy_reaches_guests_without_a_second_step(db):
    """The reported failure, end to end."""
    hotel = await platform_routes.create_hotel(_payload(), db)

    corrected = (
        "We accept cash in NPR and digital payments via eSewa. A 20% "
        "advance deposit of the total booking cost is required."
    )
    await platform_routes.update_hotel(
        hotel.id,
        _payload(policies=[
            PolicyIn(category=PolicyCategory.payment, content_text=corrected)
        ]),
        db,
    )

    docs = await _documents(db, hotel.id)
    payment = next(v for k, v in docs.items() if "Payment" in k)
    assert "of the total booking cost" in payment
    assert payment.count("advance deposit") == 1, "the old wording must be gone"


async def test_an_edited_rate_reaches_guests_too(db):
    hotel = await platform_routes.create_hotel(_payload(), db)
    await platform_routes.update_hotel(
        hotel.id,
        _payload(room_types=[
            RoomTypeIn(
                name="Deluxe Room", base_rate="12750.00", max_occupancy=2,
                amenities=["Mountain View Balcony"],
            )
        ]),
        db,
    )

    joined = " ".join((await _documents(db, hotel.id)).values())
    assert "12,750" in joined
    assert "10,500" not in joined


async def test_a_removed_policy_stops_being_answered(db):
    """Deleting it in the form must delete it for guests. A policy that
    lingers in the knowledge base is worse than one that was never there."""
    hotel = await platform_routes.create_hotel(_payload(), db)
    assert any("Payment" in t for t in await _documents(db, hotel.id))

    await platform_routes.update_hotel(hotel.id, _payload(policies=[]), db)
    assert not any("Payment" in t for t in await _documents(db, hotel.id))


async def test_saving_twice_does_not_duplicate(db):
    """Staff press Save repeatedly. Duplicate chunks give retrieval several
    copies of one fact to disagree about."""
    hotel = await platform_routes.create_hotel(_payload(), db)
    first = await _documents(db, hotel.id)

    await platform_routes.update_hotel(hotel.id, _payload(), db)
    second = await _documents(db, hotel.id)

    assert sorted(first) == sorted(second)


async def test_a_hotel_with_nothing_to_sync_still_saves(db):
    """No room types and no policies is a legitimate first save - it is
    exactly what /setup posts before anyone has filled the form in."""
    hotel = await platform_routes.create_hotel(
        _payload(room_types=[], policies=[]), db
    )
    assert hotel.id
    assert await _documents(db, hotel.id) == {}


# --- the architecture ----------------------------------------------------


async def test_platform_does_not_import_the_receptionist_module():
    """The rule the hook exists to preserve: modules -> platform -> core.

    A direct `from app.modules...` in platform would work today and make
    the second module impossible to add without touching the first.
    """
    import ast
    import pathlib

    platform_dir = pathlib.Path(__file__).resolve().parents[2] / "app" / "platform"

    def imports_a_module(path: pathlib.Path) -> bool:
        # Parsed, not grepped. A substring search flags __init__.py, whose
        # docstring states this very rule - the check would fail on the
        # documentation of the thing it is checking.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("app.modules"):
                    return True
            elif isinstance(node, ast.Import):
                if any(a.name.startswith("app.modules") for a in node.names):
                    return True
        return False

    offenders = [
        path.relative_to(platform_dir).as_posix()
        for path in sorted(platform_dir.rglob("*.py"))
        if imports_a_module(path)
    ]
    assert offenders == [], (
        f"platform imports a module: {offenders}. Use app/platform/hooks.py."
    )


async def test_a_failing_listener_takes_the_save_down_with_it(db, monkeypatch):
    """Deliberate. A save that reports success while its knowledge sync
    failed silently puts the two out of step - which is the entire failure
    this feature exists to prevent, and staff would have no way to see it.
    """
    async def _boom(db_, hotel_id):
        raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(hooks, "_hotel_saved", [_boom])

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        await platform_routes.create_hotel(_payload(), db)


async def test_listeners_are_registered_once(db):
    """Registering by identity, so a module imported twice does not run
    its sync twice per save."""
    import app.modules.receptionist.hooks as receptionist_hooks

    hooks.on_hotel_saved(receptionist_hooks.resync_knowledge_base)
    hooks.on_hotel_saved(receptionist_hooks.resync_knowledge_base)

    assert hooks._hotel_saved.count(receptionist_hooks.resync_knowledge_base) == 1


async def test_with_no_listeners_a_save_is_just_a_save(db, monkeypatch):
    """Platform must stand alone. With no module loaded nothing registers,
    and saving a hotel must not depend on anything that is not there."""
    monkeypatch.setattr(hooks, "_hotel_saved", [])

    hotel = await platform_routes.create_hotel(_payload(), db)
    assert hotel.id
    assert await _documents(db, hotel.id) == {}


async def test_the_sync_is_scoped_to_the_saved_hotel(db):
    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()

    hotel = await platform_routes.create_hotel(_payload(), db)
    assert await _documents(db, other.id) == {}
    assert await _documents(db, hotel.id)
