"""Swiss TIP knowledge builder: the build pipeline of a pack.

Runs the build-side stages in order, each on the previous stage's output,
skipping a stage whose hash-pinned inputs did not change, and records every
outcome in the pack's pipeline report. The serving side never imports it.
"""

REPORT_SCHEMA_VERSION = "swiss-tip-pipeline-report/v1"
