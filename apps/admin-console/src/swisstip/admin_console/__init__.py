"""Swiss TIP admin console: a local web interface over the files of a pack.

Build-side only; the MCP server never imports this package. Every screen
reads a file the pipeline already writes, and the console writes only the
curation file, the source catalogue and its own checks file. See
docs/architecture/admin-console.md.
"""

CONSOLE_NAME = "swisstip-admin"
CONSOLE_VERSION = "0.3.2"
CHECKS_SCHEMA_VERSION = "swiss-tip-checks/v1"
