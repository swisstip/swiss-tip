# swisstip-core

**Last update:** 20 September 2026

The knowledge release format of Swiss TIP: Pydantic models for the release
file a pack publishes (`swiss-tip-release/v1`) and the validator the build
and the server both run. Depends on Pydantic only; the serving side imports
this package and nothing from the build side.

```shell
./.venv/Scripts/python.exe -m pip install -e packages/core
./.venv/Scripts/python.exe -m unittest discover -s packages/core/tests
./.venv/Scripts/python.exe -m swisstip.core.validation releases/<pack>/release.json --text .local/<pack>/text --run .local/<pack>
```

`swisstip.core.places` turns the place a caller names into the jurisdiction
code the facts carry: `PlaceIndex` reads the release's place register (the
country, its cantons and municipalities with official names and aliases) and
resolves the parts `country`, `canton` and `city`, each a name or a code, to
a scope with the codes, the official names and the parts it could not place.
Nothing is guessed: a city alone supplies its canton, a name the register
does not hold moves the request to the broader place and is reported, a name
several municipalities share is an error that lists them. The server and the
mock both use it; the rules are in
[docs/architecture/tool-contracts.md](../../docs/architecture/tool-contracts.md),
section 2.

The format, the provenance kinds and review statuses, and every check of the
validator are described in
[docs/architecture/release-format.md](../../docs/architecture/release-format.md).
