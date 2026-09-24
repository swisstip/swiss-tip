"""The immutable V3 prompts, the basis classification prompt, and audited whole-prompt overrides."""

import hashlib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


def load_bundled_prompt(name: str) -> str:
    return files("swisstip.concepts").joinpath("prompts", name).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Prompt:
    text: str
    sources: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return dict(sha256=self.sha256, sources=list(self.sources))


@dataclass(frozen=True, slots=True)
class PromptSet:
    profile: str
    extraction: Prompt
    review: Prompt
    basis: Prompt

    def to_dict(self) -> dict:
        return dict(extraction=self.extraction.to_dict(), review=self.review.to_dict(), basis=self.basis.to_dict())


def _load(names: tuple[str, ...], override: str | Path | None) -> Prompt:
    if override is None:
        return Prompt("".join(load_bundled_prompt(name) for name in names),
                      tuple(f"package:swisstip.concepts/prompts/{name}" for name in names))
    path = Path(override).resolve()
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError(f"prompt file must not be empty: {path}")
    return Prompt(text, (str(path),))


def load_prompts(profile: str = "concept_extraction_v3", *, extraction_prompt_file: str | Path | None = None,
                 review_prompt_file: str | Path | None = None, basis_prompt_file: str | Path | None = None) -> PromptSet:
    """The V3 extraction and review prompts, reproduced byte for byte, and the basis classification prompt of
    16 September 2026, which is a third, separate call so the V3 hashes stand."""
    if profile != "concept_extraction_v3":
        raise ValueError(f"unsupported prompt profile: {profile}")
    return PromptSet(profile, _load(("concept_extraction_v2.md", "concept_extraction_v3_extension.md"), extraction_prompt_file),
                     _load(("concept_review_v3.md",), review_prompt_file),
                     _load(("basis_classification_v1.md",), basis_prompt_file))
