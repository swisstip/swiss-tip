# Swiss TIP

An MCP (Model Context Protocol) server that gives an AI assistant grounded
access to authoritative Swiss public information. It serves a curated,
versioned knowledge base in which every fact is tied to an exact excerpt of
an official page, with a citation. The calling assistant composes the answer
itself; the server never invents one, and when a question is not covered it
says so by name.

This repository holds the **code**: the server, the knowledge-base pipeline
and the tooling around them. The knowledge bases it serves are published
separately, in [swiss-tip-mvp](https://github.com/swisstip/swiss-tip-mvp).

## The hackathon

Built for the **Swiss {ai} Weeks** hackathon in Zurich, 24 and 25 September
2026, for the challenge **Swiss Grounding MCP**, set by Swisscom's myAI team:

<https://zh.ai-weeks.ch/challenges/swiss-grounding-mcp>

## Status

Initial setup. Contents are being added; this README will describe the
components, the installation and the served tools once they are in place.
