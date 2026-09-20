"""Source acquisition for Swiss TIP: a bounded crawler and a catalogue downloader.

Build-side only. The MCP server never imports this package.
"""

from .crawler import CrawlLimits, CrawlReport, SafeCrawler, SourceDefinition

__all__ = ["CrawlLimits", "CrawlReport", "SafeCrawler", "SourceDefinition"]
