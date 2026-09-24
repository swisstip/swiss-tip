# Repository instructions

**Last update:** 20 September 2026

## Python environment

- Use a `.venv` for every Python command, including scripts, package
  installation, formatting, linting, and tests: the repository-local `.venv`,
  or the `.venv` of the folder that holds this repository and the packs
  repository (`swiss-tip-mvp`) side by side.
- On Windows, invoke `.venv\Scripts\python.exe` directly. On macOS/Linux, invoke
  `.venv/bin/python` directly. Do not rely on an activated shell or a system
  `python`/`pip` executable.
- Run pip as `.venv\Scripts\python.exe -m pip` (or the macOS/Linux equivalent).
- If `.venv` is absent, create it with Python 3.14 or newer, then install the
  editable packages listed in the README.
- Never commit `.venv`; it is intentionally excluded by the root `.gitignore`.
- Use `unittest` as the test runner. No test may need the network.
- The package build depends on no knowledge base. A test under `packages/*/tests`
  or `apps/*/tests` reads no pack; it builds its input or takes it from a
  `fixtures` folder next to it. The packs, their suites, reports, readiness
  records and catalogues, and the checks of all of them, live in the packs
  repository, so a pack that is being rebuilt or waits for its attestation
  cannot fail a build here.
- Do not add `from __future__ import annotations`. The minimum supported
  version is Python 3.14, where annotations are evaluated lazily (PEP 649)
  and `X | None`, `list[dict]` and the other syntax used here work natively.
  Define a name before annotating with it instead of relying on string
  annotations.

## Packs

- This repository holds the code and knows no pack by name. The server takes
  the release it serves with `--release` or `SWISSTIP_RELEASE`; the knowledge
  builder and the admin console take the packs directory with `--packs-dir`
  or `SWISSTIP_PACKS`; the image build takes `--pack-dir`. A default that
  points at a particular pack is a defect.
- Documentation and docstrings name paths as `releases/<pack>/...` and
  `.local/<pack>/...`, relative to the packs directory. A measurement that
  was made on a particular release keeps that release's ID: it is a record.

## One-time scripts

- Scripts that serve a single migration, copy or analysis and are not part
  of the application (for example importing files from another checkout) are
  created under the Git-ignored `.local/scripts/` directory, not under
  `scripts/` or a package. Delete them once the task is done unless there is
  a reason to keep them locally. Reusable logic they need belongs in the
  package, with tests; the script itself gets no test.

## Writing conventions

- Use the ASCII hyphen-minus (`-`) instead of en dashes or em dashes in repository text.
- Documentation states what is implemented and tested separately from what is planned.

## Dates and history in documents

- A living document (the root documents, the documents under `docs/product/`
  and `docs/architecture/`, and the README of every app, package, image and
  test script) describes the present state. Its only date is one line
  directly under the title: `**Last update:** 20 September 2026`, changed in
  every commit that changes its content. Exception: the root `README.md`
  carries no date line; it opens with the introduction and the quick start.
- No date in a section heading (`## Status`, not `## Status on 14 September
  2026`) and no dated progress markers in the text ("Done (13 September)").
  Dates that are content stay: the hackathon days, deadlines, a snapshot or
  stale date, the access date of a source, the date inside a release ID.
- When earlier states are worth keeping, move them into a companion document
  `docs/history/<document>-history.md`, newest entry first, each entry
  headed by its date, and link it from the living document with one line.
  A living document never carries its own history.
- Dated records are history by nature and keep their dates. A factual error
  in a record is corrected with a dated correction note, not by rewriting
  the record.
- Experiment records (dated measurement records with their transcripts,
  grades and counts) live under `.local/experiments/`, outside the
  repository. A living document names such a record by its path in code and
  does not link it; a number a record established is quoted in the living
  document. Test inputs are not records: the retrieval fixture is
  `packages/runtime/tests/fixtures/semantic-search-queries.json`.
- State a changing number in one place and link to it instead of repeating
  it. Test totals go in no document (give the command that counts them).
  Documents name "the current release" of a pack rather than its ID unless
  the ID is the point.

## Shell conventions

- Prefer PowerShell instead of Bash for working commands and ad hoc scripts
  unless the user explicitly requests another shell.
- Continue to prefer Unix-style shell commands in repository documentation.
- Shell scripts under `docker/` run inside Linux images; `.gitattributes`
  keeps them LF on every checkout. Keep that rule when adding one.

## Secrets

- Never commit credentials. `.env` and `.env.*` are Git-ignored; keys are
  passed as environment variables.

## Commit messages

- After every substantial repository update, propose a one-line commit message in the final response.
- Before proposing the message, inspect the subjects of the 10 most recent commits and follow their established style.
- Never add a Claude or Anthropic attribution trailer; see `CLAUDE.md`.
