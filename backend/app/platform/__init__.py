"""
Platform layer — concepts shared by every HospitalityOS module.

Nothing in here may import from app.modules.*. The dependency arrow points
one way only: modules depend on platform, never the reverse. That rule is
what keeps a second module from having to fork hotel/room data later.

Tables owned here (see docs/01-platform/DATABASE_DESIGN.md):
    hotels, users, room_types, rooms, availability, guests, audit_logs

Note: this package is named `platform`, which shadows a stdlib module name.
Under Python 3's absolute imports that is harmless — `import platform`
anywhere still resolves to the stdlib. Always refer to this one as
`app.platform`.
"""


from app.platform.guests import Guest  # noqa: E402,F401
