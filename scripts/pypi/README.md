# PyPI distributions

**Last update:** 24 September 2026

The repository keeps one `pyproject.toml` per component under `packages/` and
`apps/`, for editable development installs. PyPI gets five distributions
that bundle them; the module names do not change.

| Distribution | Components | Installed by |
| --- | --- | --- |
| `swisstip-core` | `packages/core`, `packages/runtime` | the others, at the same version |
| `swisstip-mcp` | `apps/mcp-server` | the container image, MCP clients through `uvx` |
| `swisstip-quickstart` | `apps/quickstart` | a first-time user through `uvx swisstip-quickstart <pack>`: fetches a published pack and runs the first tests; requires `swisstip-mcp` |
| `swisstip-calendar-connector` | `apps/calendar-connector` | the calendar connector image, a server operator who serves a pack's datasets |
| `swisstip-builder` | `packages/ingestion`, `packages/extraction`, `packages/build`, `packages/concepts`, `apps/knowledge-builder`, `apps/admin-console` | a curator's workstation |

Knowledge releases are not published to PyPI.

## Files

- [distributions.toml](distributions.toml): which components go into which
  distribution, and the PyPI metadata. Every component must be listed exactly
  once; the build stops otherwise, so a new component cannot be forgotten.
- [build_distributions.py](build_distributions.py): copies the components'
  `src/` trees into `build/pypi/<distribution>/`, writes a `pyproject.toml`
  whose dependencies, scripts, optional dependencies and package data are
  merged from the components' own files, and builds the sdist and the wheel
  into `dist/`. Dependencies are declared only in the component files.
- [check_versions.py](check_versions.py): the guard that every component
  states one version and that the version being built is it.
- [readme/](readme/): the PyPI project description of each distribution.
- [../../.github/workflows/pypi-packages.yml](../../.github/workflows/pypi-packages.yml):
  the publishing workflow.

## Building locally

```bash
./.venv/Scripts/python.exe -m pip install build twine
./.venv/Scripts/python.exe scripts/pypi/build_distributions.py --version 0.2.0
./.venv/Scripts/python.exe scripts/pypi/build_distributions.py --version 0.2.0 --stage-only   # print the generated pyproject files
./.venv/Scripts/python.exe -m twine check --strict dist/*
```

## The workflow

| Trigger | Publishes to |
| --- | --- |
| Push of a tag `v<version>`, for example `v0.3.0` | `<version>` |
| Manual run ("Run workflow") with a version, for example `0.3.0` | `<version>` |
| Push to any branch | nothing; it builds and tests as `<version>.dev0` |

The version decides the index: a release candidate, ending in `rc<N>` (for
example `0.3.0rc1`), goes to TestPyPI; every other version goes to PyPI.

A branch push is how the build is verified before a release tag exists: the
publish jobs run for a tag and for a manual run only. The manual run needs
this workflow on the default branch, because that is where GitHub looks for
the "Run workflow" button, so on a repository whose default branch does not
carry it yet, a branch push is the only trigger that works.

The distributions take their version from the command line of
`build_distributions.py`, which the workflow fills from the tag, never from
the component files. `check_versions.py` is the guard: every
`packages/*/pyproject.toml`, every `apps/*/pyproject.toml` and the server's
`SERVER_VERSION` must state one and the same version, and the version being
built must be it, so a tag `v0.3.0` on a checkout that still says `0.2.5`
fails the run before anything is built.

The build job runs every component's offline tests, builds the
distributions, checks their metadata with `twine check --strict`, installs the
server wheel into a clean environment (health check and the stdio round trip
of `scripts/test/mcp/check_wheel.py` on a synthetic release, and a check that
no build tooling came with it), the quickstart wheel into another (the
synthetic release written as a fetched pack and checked offline with
`--no-fetch`, round trip included), the connector wheel into a third, and
installs the workstation wheel into a fourth (every command's `--help`, and
the admin console serving a page and its static files from a packs directory
without packs). The build depends
on no knowledge base: the packs are checked in their own repository,
[swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp), so a pack that
is being rebuilt or waits for its attestation cannot fail a package build.
The publish jobs upload the files of that build job only.

A version on PyPI cannot be uploaded twice, even after it is deleted; publish
a new version instead. Try a release candidate on TestPyPI first.

## One-time setup

Publishing uses trusted publishing (OpenID Connect): PyPI trusts this
workflow, and no API token exists.

Every distribution is uploaded from its own job in its own GitHub
environment, because PyPI accepts a pending publisher for one repository,
workflow and environment combination for one project name only.

1. **PyPI** (<https://pypi.org/manage/account/publishing/>): add one pending
   publisher per distribution with owner `swisstip`, repository `swiss-tip`
   and workflow `pypi-packages.yml`: project `swisstip-core` with environment
   `pypi-core`, `swisstip-mcp` with `pypi-mcp`, `swisstip-quickstart` with
   `pypi-quickstart`, `swisstip-calendar-connector` with
   `pypi-calendar-connector` and `swisstip-builder` with `pypi-builder`. The
   first upload creates each project; its pending publisher then becomes its
   publisher. A project that exists already gets its publisher on its own
   settings page instead (`pypi.org/manage/project/<name>/settings/publishing/`).
2. **TestPyPI** (<https://test.pypi.org/manage/account/publishing/>, a separate
   account): the same, with the environments `testpypi-core`,
   `testpypi-mcp`, `testpypi-quickstart`, `testpypi-calendar-connector` and
   `testpypi-builder`.
3. **GitHub** (repository Settings, Environments): create the `pypi-*` and
   `testpypi-*` environments. A manual run publishes from the branch it is
   started on, so limit the `pypi-*` ones to tags matching `v*` and the
   default branch,
   and add yourself as a required reviewer of `pypi-core`: a tag push then
   waits for one approval before anything is uploaded, and the other
   packages follow only after the core is published.

## Using the installed packages

The project descriptions in [readme/](readme/) show the use of each
distribution. The packages carry no knowledge and no pack: `swisstip-mcp`
needs `--release` (or `SWISSTIP_RELEASE`), `swisstip-build` and
`swisstip-admin` need `--packs-dir` (or `SWISSTIP_PACKS`), and
`swisstip-concepts` needs `--config`, because the installed package holds no
`config/semantic-models.toml`.
