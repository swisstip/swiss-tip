# PyPI distributions

**Last update:** 21 September 2026

The repository keeps one `pyproject.toml` per component under `packages/` and
`apps/`, for editable development installs. PyPI gets three distributions
that bundle them; the module names do not change.

| Distribution | Components | Installed by |
| --- | --- | --- |
| `swisstip-core` | `packages/core`, `packages/runtime` | the other two, at the same version |
| `swisstip-mcp` | `apps/mcp-server` | the container image, MCP clients through `uvx` |
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
| Push of a tag `v<version>`, for example `v0.3.0` | PyPI, as `<version>` |
| Manual run ("Run workflow") with a version, for example `0.3.0rc1` | TestPyPI |
| Push to any branch | nothing; it builds and tests as `<version>.dev0` |

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

The build job runs every component's offline tests, builds the three
distributions, checks their metadata with `twine check --strict`, installs the
server wheel into a clean environment (health check and the stdio round trip
of `scripts/test/mcp/check_wheel.py` on a synthetic release, and a check that
no build tooling came with it), and installs the workstation wheel into
another (every command's `--help`, and the admin console serving a page and
its static files from a packs directory without packs). The build depends
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

1. **PyPI** (<https://pypi.org/manage/account/publishing/>): add three pending
   publishers with owner `swisstip`, repository `swiss-tip` and
   workflow `pypi-packages.yml`: project `swisstip-core` with environment
   `pypi-core`, `swisstip-mcp` with `pypi-mcp`, and `swisstip-builder` with
   `pypi-builder`. The first upload creates each project; its pending
   publisher then becomes its publisher.
2. **TestPyPI** (<https://test.pypi.org/manage/account/publishing/>, a separate
   account): the same three, with the environments `testpypi-core`,
   `testpypi-mcp` and `testpypi-builder`.
3. **GitHub** (repository Settings, Environments): create the six
   environments. Limit the three `pypi-*` environments to tags matching `v*`,
   and add yourself as a required reviewer of `pypi-core`: a tag push then
   waits for one approval before anything is uploaded, and the other two
   packages follow only after the core is published.

## Using the installed packages

The project descriptions in [readme/](readme/) show the use of each
distribution. The packages carry no knowledge and no pack: `swisstip-mcp`
needs `--release` (or `SWISSTIP_RELEASE`), `swisstip-build` and
`swisstip-admin` need `--packs-dir` (or `SWISSTIP_PACKS`), and
`swisstip-concepts` needs `--config`, because the installed package holds no
`config/semantic-models.toml`.
