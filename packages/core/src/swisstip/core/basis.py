"""The basis of an excerpt and the ranking weights that follow from it.

A basis says what an excerpt is, independent of the page that carries it: an
article of the AIG quoted on a cantonal page is federal law, the canton's own
answer next to it is cantonal authority guidance. The label is the one string
a caller quotes; the weights make a concept resting on the law and a reviewed
statement outrank one resting on a portal summary or an unreviewed candidate
when a query does not separate them. The publisher's level never weighs:
a municipal page is authoritative for a narrower jurisdiction, not less
authoritative. Design: docs/architecture/institutions-and-provenance-weights.md.

The level of a basis is a level id of the release's place hierarchy, and the
label starts with that level's adjective: LEVEL_WORD for a Swiss release,
the declared hierarchy's words ("National act", "Commune ordinance")
otherwise. Design: docs/architecture/country-profiles.md, section 7.
"""

from .hierarchy import SWISS, Hierarchy
from .release import Basis, BasisKind, EvidenceRecord, FactRecord, RankingPolicy

NORM_KINDS = ("act", "ordinance", "treaty", "directive")
# The words of the built-in Swiss levels (SWISS.words); a declared hierarchy passes its own.
LEVEL_WORD = {"federal": "Federal", "cantonal": "Cantonal", "municipal": "Municipal"}
DEFAULT_RANKING_POLICY = RankingPolicy(
    floor=0.7, aggregate="max",
    basis_kind={"act": 1.0, "ordinance": 1.0, "treaty": 1.0, "directive": 0.95, "guidance": 0.9, "directory": 0.9,
                "summary": 0.75},
    provenance_kind={"curated-statement": 1.0, "source-section": 0.9, "model-candidate": 0.8},
    review_status={"human-reviewed": 1.0, "model-candidate-automated-review": 0.8, "assistant-authored-unreviewed": 0.7,
                   "automatically-derived-unreviewed": 0.6})


def basis_label(level: str, kind: BasisKind, norm: str | None = None, refers_to: str | None = None,
                words: dict[str, str] = LEVEL_WORD) -> str:
    """The served label: "Federal act: AIG, SR 142.20, Art. 12", "Cantonal authority guidance", ...; words maps the
    level ids of the release's hierarchy to their adjectives, which the caller checks the level against."""
    word = words[level]
    if kind in ("act", "ordinance", "treaty", "directive") and not norm:
        raise ValueError(f"a basis of kind {kind} needs a norm")
    if kind in ("act", "ordinance"):
        text = f"{word} {kind}: {norm}"
    elif kind == "treaty":
        text = f"International agreement: {norm}"
    elif kind == "directive":
        text = f"{word} directive: {norm}"
    elif kind == "guidance":
        text = f"{word} authority guidance"
    elif kind == "directory":
        text = f"{word} authority directory"
    elif kind == "summary":
        # The adjective lower-cased is the level id for each Swiss level: "Portal summary of federal rules".
        text = f"Portal summary of {word.lower()} rules"
    else:
        raise ValueError(f"unknown basis kind {kind!r}")
    if refers_to:
        text += f", referring to {refers_to}"
    return text


def make_basis(level: str, kind: BasisKind, norm: str | None = None, refers_to: str | None = None,
               words: dict[str, str] = LEVEL_WORD) -> Basis:
    return Basis(level=level, kind=kind, norm=norm, refers_to=refers_to,
                 label=basis_label(level, kind, norm, refers_to, words))


def basis_weight(basis: Basis | None, policy: RankingPolicy = DEFAULT_RANKING_POLICY) -> float:
    """The source weight of an excerpt; an excerpt without a basis weighs as the law, so older releases rank as before."""
    return policy.basis_kind.get(basis.kind, 1.0) if basis is not None else 1.0


def strongest_basis(bases: list[Basis | None], policy: RankingPolicy = DEFAULT_RANKING_POLICY) -> Basis | None:
    """The basis a fact rests on: the highest-weighted among its excerpts, the first of equals."""
    present = [basis for basis in bases if basis is not None]
    return max(present, key=lambda basis: basis_weight(basis, policy)) if present else None


def fact_weight(fact: FactRecord, evidence: dict[str, EvidenceRecord], policy: RankingPolicy = DEFAULT_RANKING_POLICY) -> float:
    """Source weight of the fact's strongest excerpt times the weight of how its statement came about."""
    source = max((basis_weight(evidence[evidence_id].basis, policy) for evidence_id in fact.evidence_ids), default=1.0)
    statement = (policy.provenance_kind.get(fact.provenance.kind, 1.0)
                 * policy.review_status.get(fact.provenance.review_status, 1.0))
    return source * statement


def concept_authority(fact_weights: list[float], policy: RankingPolicy = DEFAULT_RANKING_POLICY) -> float:
    if not fact_weights:
        return 1.0
    return max(fact_weights) if policy.aggregate == "max" else sum(fact_weights) / len(fact_weights)


def level_of_jurisdiction(code: str, hierarchy: Hierarchy = SWISS) -> str | None:
    """The level id of a code by its depth: federal, cantonal or municipal (anything deeper) for a Swiss release;
    in a declared hierarchy None for a code deeper than its lowest level."""
    return hierarchy.level_id(code)
