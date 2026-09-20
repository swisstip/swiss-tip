"""Source adapters that resolve a catalogue URL to the documents to download.

A plugin turns one listed page (for example an undated Fedlex ELI page, which
is a JavaScript shell) into exact document URLs through a public metadata
endpoint. Networking and storage stay with the shared runner in
``plugin_downloads``; a plugin only builds metadata requests and validates
what comes back.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlsplit
import re


PLUGIN_API_VERSION = 1


@dataclass(frozen=True)
class SourceRequest:
    url: str
    as_of: date


@dataclass(frozen=True)
class SourceDocument:
    url: str
    media_type: str
    language: str
    version_uri: str | None = None
    preferred_for_extraction: bool = True
    metadata: dict = field(default_factory=dict)


class SourcePlugin:
    """Subclass and register through ``load_source_plugins`` or an explicit registry."""

    api_version = PLUGIN_API_VERSION
    plugin_id: str
    version: str
    metadata_hosts: tuple[str, ...] = ()
    document_hosts: tuple[str, ...] = ()
    max_documents = 8

    def matches(self, url: str) -> bool:
        raise NotImplementedError

    def resolve(self, request: SourceRequest, fetch_json: Callable[[str], dict]) -> list[SourceDocument]:
        raise NotImplementedError


class PluginRegistry:
    def __init__(self, plugins: list[SourcePlugin]) -> None:
        self.plugins = tuple(plugins)
        ids = []
        for plugin in plugins:
            if not isinstance(plugin, SourcePlugin) or plugin.api_version != PLUGIN_API_VERSION:
                raise ValueError("Unsupported source plugin API")
            if not re.fullmatch(r"[a-z][a-z0-9-]*", plugin.plugin_id) or not plugin.version:
                raise ValueError("Source plugins require a stable ID and version")
            if not 1 <= plugin.max_documents <= 100:
                raise ValueError("Source plugin max_documents must be between 1 and 100")
            for host in (*plugin.metadata_hosts, *plugin.document_hosts):
                if not re.fullmatch(r"[a-z0-9.-]+", host):
                    raise ValueError("Source plugins must declare exact host names")
            ids.append(plugin.plugin_id)
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate source plugin ID")

    def select(self, url: str) -> SourcePlugin | None:
        matches = [plugin for plugin in self.plugins if plugin.matches(url)]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous source plugins for {url}: {[p.plugin_id for p in matches]}")
        return matches[0] if matches else None

    def describe(self) -> list[dict]:
        return [{"id": p.plugin_id, "version": p.version, "api_version": p.api_version}
                for p in self.plugins]


def load_source_plugins(names: list[str] | None = None) -> PluginRegistry:
    """Only bundled plugins are available; Fedlex is enabled by default."""
    from .fedlex import FedlexPlugin

    bundled = {"fedlex": FedlexPlugin}
    names = ["fedlex"] if names is None else names
    unknown = [name for name in names if name not in bundled]
    if unknown:
        raise ValueError(f"Unknown source plugins: {unknown}; bundled: {sorted(bundled)}")
    return PluginRegistry([bundled[name]() for name in names])


def validate_documents(plugin: SourcePlugin, documents: list[SourceDocument]) -> None:
    if not documents or len(documents) > plugin.max_documents:
        raise ValueError(f"{plugin.plugin_id} returned an empty or oversized document list")
    seen = set()
    for document in documents:
        url = urlsplit(document.url)
        if (url.scheme != "https" or url.hostname not in plugin.document_hosts or
                url.username or url.password or url.port not in (None, 443)):
            raise ValueError(f"{plugin.plugin_id} returned a document outside its declared hosts")
        if document.url in seen:
            raise ValueError(f"{plugin.plugin_id} returned duplicate document URLs")
        if not document.media_type or not document.language:
            raise ValueError("Plugin documents require a media type and language")
        seen.add(document.url)
