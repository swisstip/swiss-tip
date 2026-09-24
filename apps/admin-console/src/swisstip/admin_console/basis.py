"""The basis of a citation as the build would label it, for the review queue and the workbench.

The console resolves the institution and the basis with the build's own functions over the pack's text dataset,
so what a card shows is what the next build serves; a citation the build would refuse shows the refusal instead.
"""

from swisstip.build.release_build import BuildError, basis_for, institution_for
from swisstip.core.basis import basis_weight

BASIS_KINDS = ("act", "ordinance", "treaty", "directive", "guidance", "directory", "summary")
BASIS_LEVELS = ("federal", "cantonal", "municipal")


def citation_basis(pack, curation, fact, citation) -> dict:
    """label, kind, level, rule and warnings of one citation; label None when the page has no institution."""
    record = pack.dataset.record(citation.document_id)
    if curation is None or record is None:
        return dict(label=None, kind=None, level=None, rule="no record", warnings=[], institution=None)
    institution, _ = institution_for(curation, record)
    last = citation.last_block or citation.first_block
    block_ids = [block["block_id"] for block in record["blocks"][citation.first_block - 1:last]]
    try:
        basis, rule, warnings = basis_for(curation, citation, record, institution, fact.fact_id, block_ids)
    except BuildError as exc:
        return dict(label=None, kind=None, level=None, rule=f"invalid: {exc}", warnings=[], institution=institution)
    return dict(label=basis.label if basis else None, kind=basis.kind if basis else None,
                level=basis.level if basis else None, rule=rule, warnings=warnings, institution=institution,
                weight=basis_weight(basis) if basis else None)


def fact_basis(pack, curation, fact) -> dict:
    """The strongest basis among the fact's citations, the first of equals: what the build states on the fact."""
    rows = [citation_basis(pack, curation, fact, citation) for citation in fact.evidence]
    present = [row for row in rows if row["label"]]
    best = max(present, key=lambda row: row["weight"]) if present else None
    return dict(label=best["label"] if best else None, kind=best["kind"] if best else None, citations=rows)
