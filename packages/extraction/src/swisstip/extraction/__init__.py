"""Source-text extraction for Swiss TIP.

Turns the saved responses of an ingestion run into text records: ordered
blocks with stable identifiers, code-point offsets into one normalized text
string, and hashes of every block, of the whole text and of the raw bytes.
No request, no model, no interpretation.
"""

# The version of the records' content, not of the package: a change to what a saved response extracts to bumps it,
# so existing records are extracted again (0.2.2: City of Zurich contact cards and data tables).
EXTRACTOR_VERSION = "0.2.2"
SCHEMA_VERSION = "swisstip.source-intermediate/v1"
