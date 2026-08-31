"""Route handlers for the receptionist module."""

# Importing this registers the module's platform-event listeners (a
# knowledge-base resync whenever a hotel is saved). main.py already
# imports this package to mount the routers, so it is the natural
# wiring point - and an unimported module registers nothing, which is
# what keeps modules separable.
from app.modules.receptionist import hooks  # noqa: F401
from app.modules.receptionist.api.routes import router, staff_router

__all__ = ["router", "staff_router"]
