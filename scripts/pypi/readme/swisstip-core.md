# swisstip-core

**Last update:** 20 September 2026

The shared library of Swiss TIP, the grounding MCP server for Swiss public
information. It ships two modules:

- `swisstip.core`: the Pydantic models of the knowledge release format and of
  the four tool contracts, the release validator and the readiness record.
- `swisstip.runtime`: the four tool operations (`get_coverage`, `search`,
  `resolve`, `get_evidence`) over a validated release, without any transport.

Most users install `swisstip-mcp` (the server) or `swisstip-builder` (the
workstation tools), which depend on this package at the same version.

## Use

Python 3.14 or newer.

```bash
pip install swisstip-core
swisstip-validate-release release.json
```

```python
from pathlib import Path

from swisstip.core.contracts import SearchRequest
from swisstip.runtime.service import ReleaseService

service = ReleaseService.from_file(Path("release.json"))
print(service.search(SearchRequest(query="Anmeldung Gemeinde Zürich")))
```

Knowledge releases are not on PyPI; they are published with the GitHub
releases of the packs repository,
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).

Licence: Apache-2.0.
