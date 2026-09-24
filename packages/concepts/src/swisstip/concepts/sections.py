"""Section selection over immutable text-record blocks.

The rules live in ``swisstip.extraction.sections`` since the curation coverage
stage of the build needs the same notion of a content section as the
extraction jobs here; this module keeps the names the concepts package
imports.
"""

from swisstip.extraction.sections import (FURNITURE_HEADINGS, FURNITURE_LABELS, LINK_HEADINGS, NEWS_HEADINGS,
                                          block_exclusion, build_sections, heading_path, section_summary,
                                          terminology_key)

__all__ = ["FURNITURE_HEADINGS", "FURNITURE_LABELS", "LINK_HEADINGS", "NEWS_HEADINGS", "block_exclusion",
           "build_sections", "heading_path", "section_summary", "terminology_key"]
