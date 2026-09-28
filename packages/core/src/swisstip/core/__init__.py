"""Swiss TIP core: the knowledge release format and its validator.

The serving side imports this package and nothing from the build side.
"""

RELEASE_SCHEMA_VERSION = "swiss-tip-release/v2"
# The version of a release that declares its country (a place hierarchy in its register); Swiss builds write the one above.
RELEASE_SCHEMA_VERSION_DECLARED = "swiss-tip-release/v3"
DATASET_SCHEMA_VERSION = "swiss-tip-dataset/v1"
CONNECTOR_SCHEMA_VERSION = "swiss-tip-connector/v1"
