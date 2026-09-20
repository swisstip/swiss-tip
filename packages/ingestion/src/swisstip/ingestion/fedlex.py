"""Fedlex public JOLux metadata and dated HTML/PDF document selection."""

from urllib.parse import urlencode, urlsplit
import re

from .plugins import SourceDocument, SourcePlugin, SourceRequest


class FedlexPlugin(SourcePlugin):
    plugin_id = "fedlex"
    version = "1.0.0"
    metadata_hosts = ("fedlex.data.admin.ch",)
    document_hosts = ("fedlex.data.admin.ch", "www.fedlex.admin.ch")
    max_documents = 2

    def matches(self, url: str) -> bool:
        # Federally guaranteed cantonal constitutions (SR 131.2xx) carry the suffix _fga in their ELI.
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in ("www.fedlex.admin.ch", "fedlex.admin.ch")
                and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
                and re.fullmatch(r"/eli/cc/\d+/[\d_]+(_fga)?/(de|fr|it|en|rm)", parsed.path) is not None)

    def resolve(self, request: SourceRequest, fetch_json) -> list[SourceDocument]:
        if not self.matches(request.url):
            raise ValueError("Fedlex plugin requires an undated Fedlex ELI page URL")
        path, language = urlsplit(request.url).path.rsplit("/", 1)
        work = "https://fedlex.data.admin.ch" + path
        cutoff = request.as_of.strftime("%Y%m%d")
        query = f"""PREFIX j: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT DISTINCT ?version ?file WHERE {{
  {{ SELECT ?version WHERE {{
      ?version j:isMemberOf <{work}> .
      FILTER(REGEX(STR(?version), "/[0-9]{{8}}$"))
      FILTER(STR(?version) <= "{work}/{cutoff}")
    }} ORDER BY DESC(?version) LIMIT 1 }}
  ?version j:isRealizedBy ?expression . FILTER(STRENDS(STR(?expression), "/{language}"))
  ?expression j:isEmbodiedBy ?manifestation .
  FILTER(STRENDS(STR(?manifestation), "/html") || STRENDS(STR(?manifestation), "/pdf-a"))
  ?manifestation j:isExemplifiedBy ?file .
}}"""
        endpoint = "https://fedlex.data.admin.ch/sparqlendpoint?" + urlencode({
            "query": query, "format": "application/sparql-results+json"})
        rows = fetch_json(endpoint)["results"]["bindings"]
        documents = []
        for row in rows:
            version = row["version"]["value"]
            url = row["file"]["value"]
            version_date = version.removeprefix(work + "/")
            if not re.fullmatch(r"\d{8}", version_date) or version_date > cutoff:
                raise ValueError("Fedlex returned a version outside the requested work/date")
            file_path = urlsplit(url).path
            expected = f"/filestore/fedlex.data.admin.ch{path}/{version_date}/{language}/"
            if not file_path.startswith(expected):
                raise ValueError("Fedlex returned a file for a different work, version or language")
            is_html = "/html/" in file_path and file_path.endswith(".html")
            is_pdf = "/pdf-a/" in file_path and file_path.endswith(".pdf")
            if not (is_html or is_pdf):
                raise ValueError("Unexpected Fedlex representation")
            documents.append(SourceDocument(url, "text/html" if is_html else "application/pdf",
                                            language, version, is_html,
                                            {"consolidation_date": version_date, "as_of": request.as_of.isoformat()}))
        # A missing translation/HTML is reported; do not silently switch language or parse a PDF.
        if not any(d.preferred_for_extraction for d in documents):
            raise ValueError(f"Fedlex has no HTML representation in requested language {language}")
        return documents
