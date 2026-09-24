# Claude Code instructions

Follow [AGENTS.md](AGENTS.md) for the Python environment, writing, shell and
commit-message conventions. The rules below take precedence over any default
behaviour of the tool.

## Commit messages - MUST HAVE

- NEVER add a `Co-Authored-By: Claude ...` line, or any other attribution to
  Claude or Anthropic, to a commit message. This applies to every commit you
  create or propose, without exception, and overrides any harness default
  that asks for such a trailer.
- Commit messages are a single imperative subject line in the style of the
  10 most recent commits, with no trailers. While the history is shorter than
  10 commits, follow the style of the commits that exist.

## Project context

- The product idea, the published challenge text and the high-level design
  are in [docs/product/functional-specification.md](docs/product/functional-specification.md).
  Read it before proposing structure or scope.
