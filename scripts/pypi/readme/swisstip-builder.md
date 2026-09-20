# swisstip-builder

**Last update:** 20 September 2026

The workstation tools of Swiss TIP, the grounding MCP server for Swiss public
information. They turn a source catalogue into a validated, reviewed knowledge
release that `swisstip-mcp` serves.

| Command | Does |
| --- | --- |
| `swisstip-admin` | Local web console: sources, saved documents, reading view, review queue, tool sandbox, release |
| `swisstip-build` | The whole pipeline of a pack: acquire, gaps, extract, validate, build, accept, ready |
| `swisstip-download`, `swisstip-gaps` | Bounded source acquisition (robots.txt, rate limits, request budget) and the gap report |
| `swisstip-extract`, `swisstip-extract-validate` | Saved pages and PDFs to labelled text blocks with offsets and hashes |
| `swisstip-build-release` | Curation file plus text dataset to `release.json` |
| `swisstip-concepts`, `swisstip-concepts-package` | Model-proposed concept candidates with a separate review |

## Install

Python 3.14 or newer. Install it as an isolated tool; the extraction
libraries are pinned exactly, because the text hashes of a release depend on
them:

```bash
uv tool install swisstip-builder              # or: pipx install swisstip-builder
uv tool install "swisstip-builder[office]"    # adds legacy Office and RTF extraction
```

## Use

The tools work on a packs directory, one that holds `releases/<pack>/` (a
source catalogue, a curation file, saved pages and a text dataset), for
example a clone of a packs repository such as
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp) or an unpacked
pack workspace. Pass it as `--packs-dir`, or set `SWISSTIP_PACKS`:

```bash
swisstip-admin --packs-dir .                 # console on http://127.0.0.1:8765
swisstip-build <pack> --packs-dir . --from build --until accept
swisstip-build <pack> --packs-dir . --from accept --until ready --attested-by "A. Person"
```

The resulting `releases/<pack>/release.json` and `readiness.json` are what
`swisstip-mcp --release` serves.

Licence: Apache-2.0.
