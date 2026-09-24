import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from swisstip.ingestion.catalog import attribute_url  # noqa: E402
from swisstip.ingestion.discovery import describe, load_audit_state, select_discovered  # noqa: E402


def entry(source_id: str, url: str, hosts: list[str], prefixes: list[str]) -> dict:
    return {"definition": {"source_id": source_id, "start_url": url, "allowed_hosts": hosts,
                           "allowed_path_prefixes": prefixes}}


ENTRIES = [
    entry("sem-de", "https://www.sem.admin.ch/sem/de/home/themen/aufenthalt.html", ["www.sem.admin.ch"],
          ["/sem/de/home/themen/aufenthalt.html", "/sem/de/home/themen/aufenthalt/"]),
    entry("zh-overview", "https://www.zh.ch/de/migration-integration.html", ["www.zh.ch"], ["/de/migration-integration"]),
]


def record(url: str, *discoveries: dict, status: str = "saved") -> dict:
    return {"url": url, "url_id": "id-" + url.rsplit("/", 1)[1], "status": status, "discoveries": list(discoveries)}


class DiscoveryTests(unittest.TestCase):
    def test_attribution_uses_host_and_path_allowlists(self) -> None:
        self.assertEqual(attribute_url("https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/eu_efta.html", ENTRIES), ["sem-de"])
        self.assertEqual(attribute_url("https://www.sem.admin.ch/sem/de/home/themen/aufenthalt.html", ENTRIES), ["sem-de"])
        self.assertEqual(attribute_url("https://www.sem.admin.ch/sem/de/home/themen/aufenthaltX", ENTRIES), [])
        self.assertEqual(attribute_url("https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html", ENTRIES), [])
        self.assertEqual(attribute_url("https://www.zh.ch/de/migration-integration/auslaenderbewilligungen.html", ENTRIES), ["zh-overview"])
        self.assertEqual(attribute_url("https://other.example/de/migration-integration", ENTRIES), [])

    def test_scope_selection_traces_language_variants_transitively(self) -> None:
        seed = "https://www.sem.admin.ch/sem/de/home/themen/aufenthalt.html"
        records = [
            record(seed, {"reason": "existing-catalogue-seed"}),
            record("https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/eu_efta.html", {"reason": "topic-link", "discovered_on": seed}),
            record("https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html",
                   {"reason": "published-language-link", "discovered_on": seed, "advertised_language": "en"}),
            record("https://www.sem.admin.ch/sem/fr/home/themen/aufenthalt.html",
                   {"reason": "published-language-link", "discovered_on": "https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html",
                    "advertised_language": "fr"}),
            record("https://www.sem.admin.ch/sem/en/home/themen/arbeit.html",
                   {"reason": "topic-link", "discovered_on": "https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html"}),
            record("https://www.bern.ch/x", {"reason": "topic-link", "discovered_on": seed}, status="failed"),
            record("https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/faq.html", {"reason": "existing-catalogue-seed"}),
        ]
        extra_catalogue = "https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/faq.html"
        chosen = select_discovered(records, ENTRIES, scope="catalogue", exclude_urls={extra_catalogue})
        by_url = {t["url"]: t["attribution"] for t in chosen}
        self.assertEqual(sorted(by_url), [
            "https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/eu_efta.html",
            "https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html",
            "https://www.sem.admin.ch/sem/fr/home/themen/aufenthalt.html",
        ])
        self.assertEqual(by_url["https://www.sem.admin.ch/sem/de/home/themen/aufenthalt/eu_efta.html"]["kind"], "in-scope")
        self.assertEqual(by_url["https://www.sem.admin.ch/sem/fr/home/themen/aufenthalt.html"],
                         {"kind": "language-variant", "source_ids": ["sem-de"], "advertised_languages": ["fr"],
                          "discovered_on": ["https://www.sem.admin.ch/sem/en/home/themen/aufenthalt.html"],
                          "reasons": ["published-language-link"]})
        everything = select_discovered(records, ENTRIES, scope="all", exclude_urls={extra_catalogue})
        kinds = {t["url"]: t["attribution"]["kind"] for t in everything}
        self.assertEqual(kinds["https://www.bern.ch/x"], "out-of-scope")
        self.assertEqual(kinds["https://www.sem.admin.ch/sem/en/home/themen/arbeit.html"], "out-of-scope",
                         "topic links from a variant are not variants")
        self.assertNotIn(seed, kinds)
        self.assertNotIn(extra_catalogue, kinds)
        self.assertEqual(describe(everything)["by_kind"], {"in-scope": 1, "language-variant": 2, "out-of-scope": 2})
        self.assertEqual(describe(everything)["by_source"], {"sem-de": 3})
        with self.assertRaisesRegex(ValueError, "Unknown discovery scope"):
            select_discovered(records, ENTRIES, scope="some")

    def test_audit_state_loader_drops_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit-state.json"
            path.write_text(json.dumps({"targets": {
                "https://a.example/x": {"url": "https://a.example/x", "url_id": "1", "status": "saved", "discoveries": []},
                "https://a.example/y": {"url": "https://a.example/y", "url_id": "2", "status": "url-normalization-alias"},
            }}), encoding="utf-8")
            self.assertEqual([r["url"] for r in load_audit_state(Path(directory))], ["https://a.example/x"])


if __name__ == "__main__":
    unittest.main()
