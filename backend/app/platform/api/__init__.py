"""Route handlers for platform-level resources."""

from app.platform.api.auth_routes import router as auth_router
from app.platform.api.routes import router

__all__ = ["auth_router", "router"]
