"""One module per screen of section 4 of docs/architecture/admin-console.md.

Each module exposes a `read_router` and, where the screen writes or starts a
job, a `write_router`. In read-only mode only the read routers are registered.
"""
