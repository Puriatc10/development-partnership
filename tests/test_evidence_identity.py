"""Evidence identity must survive every hop, from a research_findings row to a
scorecard's dimension evidence, without moving a score.

The failures this file pins down:

* ``bridge.py`` used to mint ``evidence-{index}`` for every row, so a real
  ``research_findings.id`` was lost between research and scoring, and
  ``outreach/gate.py:864-873`` then rejected the draft because
  ``personalization_evidence_ids`` is ``UUID[]``.
* the quoted text a reviewer needs (``evidence_excerpt``) was dropped on the way
  into ``dimension_evidence``.

Everything here is about *carrying* provenance. Nothing here may change a
points awarded, a confidence, a coverage ratio or a disqualifier, which is what
``test_scores_are_byte_identical_with_and_without_excerpts`` asserts.
"""

from __future__ import annotations

import json
import re

import pytest

from intelligence.bridge import (
    FINDING_ID_RE,
    BridgedEvidence,
    EvidenceContractError,
    bridge_evidence,
    findings_from_evidence,
    score_candidate,
)
from intelligence.ipp import Finding, QualificationInput, score_qualification

CANDIDATE = {"name": "Gamma Dev", "domain": "gamma.dev", "segment": "webflow_studio"}

#: Three persisted research_findings ids, in a known order so the expected
#: citation order (the engine sorts findings by id) is unambiguous.
ID_A = "0b1f2c34-1111-4a2b-9c3d-0000000a0001"
ID_B = "0b1f2c34-2222-4a2b-9c3d-0000000a0002"
ID_C = "0b1f2c34-3333-4a2b-9c3d-0000000a0003"

#: ``outreach/gate.py:273`` verbatim: the check personalization evidence ids must
#: pass. Mirrored here rather than imported so this test states the contract the
#: bridge has to satisfy, not the gate's current internals.
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

STATEMENTS = (
    "Gamma Dev's portfolio page names Acme Retail as a commercial client.",
    "Gamma Dev's portfolio page names Northwind Studio as a commercial client.",
    "Gamma Dev's portfolio page names Contoso Labs as a commercial client.",
)


def persisted_rows(with_excerpts: bool = True) -> list[dict[str, object]]:
    """Three rows shaped exactly like ``research_findings`` (``id``, not ``finding_id``).

    No statement matches a higher Customer Flow anchor's vocabulary, so the
    dimension lands on the anchor that only counts findings (10 points, three
    citations) — which is the one that shows every id at once.
    """
    rows: list[dict[str, object]] = []
    for index, (finding_id, statement) in enumerate(zip((ID_A, ID_B, ID_C), STATEMENTS)):
        row: dict[str, object] = {
            "id": finding_id,
            "report_id": "6f1a0a1e-0000-4000-8000-00000000dead",
            "company_id": "6f1a0a1e-0000-4000-8000-00000000beef",
            "finding_type": "verified_fact",
            "category": "client_roster",
            "statement": statement,
            "evidence_url": f"https://gamma.dev/work/{index}",
            "confidence": "high",
        }
        if with_excerpts:
            row["evidence_excerpt"] = f"Verbatim excerpt {index}: ...our work with the client..."
        rows.append(row)
    return rows


# --- (a) a real UUID survives bridge -> scorecard evidence record ----------


def test_persisted_uuid_reaches_the_dimension_evidence_record():
    card = score_candidate(CANDIDATE, persisted_rows())

    customer_flow = card.dimension_evidence["customer_flow"]
    assert customer_flow["finding_ids"] == [ID_A, ID_B, ID_C]
    assert customer_flow["score"] == 10.0
    # the scorecard carries the same identities the bridge bound
    assert [f.finding_id for f in card.findings] == [ID_A, ID_B, ID_C]
    # and the persisted DB payload is what outreach reads back
    assert card.to_db_record()["dimension_evidence"]["customer_flow"]["finding_ids"] == [
        ID_A,
        ID_B,
        ID_C,
    ]


def test_finding_id_key_is_preferred_over_the_row_id():
    rows = [dict(persisted_rows()[0], finding_id=ID_C, id=ID_A)]
    binding = bridge_evidence(rows)
    assert binding.findings[0].finding_id == ID_C
    assert binding.has_only_real_ids


def test_an_existing_non_uuid_id_is_still_never_rewritten():
    """Identity is preserved verbatim; only its absence triggers a minted id."""
    binding = bridge_evidence([dict(persisted_rows()[0], finding_id="f1", id=None)])
    assert binding.findings[0].finding_id == "f1"
    assert binding.has_only_real_ids


# --- (b) the source excerpt reaches the dimension evidence -----------------


def test_source_excerpt_appears_in_the_dimension_evidence():
    rows = persisted_rows()
    card = score_candidate(CANDIDATE, rows)
    record = card.dimension_evidence["customer_flow"]

    # the engine cites by sorted finding id, so the first citation is ID_A's row
    assert record["excerpt"] == rows[0]["evidence_excerpt"]
    assert record["source_url"] == rows[0]["evidence_url"]
    # only the excerpt/source_url payload is affected; the rendered evidence
    # string still names the statements the score rests on
    assert ID_A in record["evidence"]
    assert rows[0]["statement"] in record["evidence"]


def test_missing_excerpt_falls_back_to_the_statement():
    card = score_candidate(CANDIDATE, persisted_rows(with_excerpts=False))
    record = card.dimension_evidence["customer_flow"]
    assert record["excerpt"] == STATEMENTS[0]
    assert record["source_url"] == "https://gamma.dev/work/0"


def test_row_source_url_overrides_the_evidence_url_in_the_record():
    rows = persisted_rows()
    rows[0]["source_url"] = "https://gamma.dev/clients"
    record = score_candidate(CANDIDATE, rows).dimension_evidence["customer_flow"]
    assert record["source_url"] == "https://gamma.dev/clients"


# --- (c) a row with no id falls back to a marked synthetic id --------------


def test_row_without_an_id_gets_a_synthetic_id_that_is_marked():
    rows = persisted_rows()
    for row in rows:
        row.pop("id")
    binding = bridge_evidence(rows)

    assert [f.finding_id for f in binding.findings] == ["evidence-0", "evidence-1", "evidence-2"]
    assert binding.synthetic_ids == ("evidence-0", "evidence-1", "evidence-2")
    assert binding.has_synthetic_ids is True
    assert binding.has_only_real_ids is False
    # a minted id can never be mistaken for a citable one
    assert all(FINDING_ID_RE.match(f.finding_id) is None for f in binding.findings)


def test_mixed_evidence_marks_only_the_rows_it_had_to_mint():
    rows = [persisted_rows()[0], dict(persisted_rows()[1], id=None)]
    binding = bridge_evidence(rows)
    assert [f.finding_id for f in binding.findings] == [ID_A, "evidence-1"]
    assert binding.synthetic_ids == ("evidence-1",)


def test_synthetic_evidence_can_be_refused_for_use():
    rows = [dict(persisted_rows()[0], id=None)]
    binding = bridge_evidence(rows)

    with pytest.raises(EvidenceContractError) as exc:
        binding.require_real_ids()
    assert "no persisted research_findings id" in str(exc.value)

    with pytest.raises(EvidenceContractError):
        score_candidate(CANDIDATE, rows, require_real_ids=True)

    # the same evidence still scores when nobody is going to cite it
    assert score_candidate(CANDIDATE, rows).total_score > 0


# --- (d) excerpts never move a score ---------------------------------------


def _render(card) -> str:
    return json.dumps(card.as_dict(), sort_keys=True, default=str)


def test_scores_are_byte_identical_with_and_without_excerpts():
    with_excerpts = score_candidate(CANDIDATE, persisted_rows(with_excerpts=True))
    without_excerpts = score_candidate(CANDIDATE, persisted_rows(with_excerpts=False))

    assert _render(with_excerpts) == _render(without_excerpts)
    assert with_excerpts.total_score == without_excerpts.total_score
    assert with_excerpts.tier == without_excerpts.tier
    assert with_excerpts.scores == without_excerpts.scores
    assert with_excerpts.overall_confidence == without_excerpts.overall_confidence
    assert with_excerpts.evidence_coverage == without_excerpts.evidence_coverage
    assert with_excerpts.disqualification_reasons == without_excerpts.disqualification_reasons
    assert with_excerpts.verified_fact_count == without_excerpts.verified_fact_count


def test_engine_findings_differing_only_in_excerpt_score_identically():
    base = {
        "category": "client_roster",
        "finding_type": "verified_fact",
        "confidence": "high",
        "statement": "Gamma Dev publishes three commercial client case studies.",
        "evidence_url": "https://gamma.dev/work",
    }
    plain = score_qualification(
        QualificationInput(findings=[Finding(finding_id=ID_A, **base)])
    )
    annotated = score_qualification(
        QualificationInput(
            findings=[
                Finding(
                    finding_id=ID_A,
                    excerpt="Three commercial client case studies are published.",
                    source_url="https://gamma.dev/work?ref=clients",
                    **base,
                )
            ]
        )
    )

    assert _render(plain) == _render(annotated)
    assert plain.to_db_record()["dimension_evidence"]["customer_flow"]["score"] == (
        annotated.to_db_record()["dimension_evidence"]["customer_flow"]["score"]
    )


def test_finding_still_constructs_with_the_old_signature():
    """The new fields are optional, so every existing constructor call works."""
    finding = Finding(ID_A, "client_roster", "verified_fact", "high", "A statement.", "https://x.dev")
    assert finding.excerpt is None
    assert finding.source_url is None


# --- (e) outreach's UUID expectation is satisfiable end to end -------------


def test_produced_ids_satisfy_the_uuid_expectation_outreach_applies():
    binding = bridge_evidence(persisted_rows())

    ids = tuple(f.finding_id for f in binding.findings)
    assert all(UUID_RE.match(finding_id) for finding_id in ids)
    # exactly the filter outreach/gate.py build_draft_payload performs
    uuid_refs = tuple(ref for ref in ids if UUID_RE.match(ref))
    assert uuid_refs == ids
    assert FINDING_ID_RE.pattern == UUID_RE.pattern


def test_bridge_evidence_iterates_like_the_findings_list_it_replaces():
    binding = bridge_evidence(persisted_rows())
    assert isinstance(binding, BridgedEvidence)
    assert len(binding) == 3
    assert [f.finding_id for f in binding] == [ID_A, ID_B, ID_C]
    assert binding[0].finding_id == ID_A


def test_compatibility_shim_keeps_returning_findings_only():
    findings = findings_from_evidence(persisted_rows())
    assert isinstance(findings, list)
    assert [f.finding_id for f in findings] == [ID_A, ID_B, ID_C]
    assert findings[0].excerpt == "Verbatim excerpt 0: ...our work with the client..."
