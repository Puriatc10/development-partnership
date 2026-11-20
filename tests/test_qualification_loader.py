"""Qualification seam: persisted findings in, an IPP draft out.

The fake client serves rows shaped like the deployed tables (``research_findings`` carries
its own UUID, which the scorecard must cite), so the tests fail if the loader invents ids,
drops excerpts, or scores a company whose research produced nothing.

Not proven here: live RLS on ``lead_qualifications`` and real PostgREST behaviour. The
persist path is exercised against the fake, and the live read path is verified separately
against Refokus in the Stage 3 report.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from qualification import (
    QualificationInputError,
    build_input,
    findings_from_rows,
    load_evidence,
    qualify_company,
    score_company,
)

COMPANY_ID = "company-1"
REPORT_ID = "report-1"


def finding(**overrides: Any) -> dict:
    row = {
        "id": "f0000000-0000-4000-8000-000000000001",
        "report_id": REPORT_ID,
        "company_id": COMPANY_ID,
        "finding_type": "verified_fact",
        "category": "technology_stack",
        "statement": "Publishes a Webflow services page.",
        "evidence_url": "https://alpha.example.com/services",
        "evidence_excerpt": "We build Webflow sites for agencies.",
        "confidence": "high",
    }
    row.update(overrides)
    return row


class FakeClient:
    def __init__(self, *, company: dict | None = None, reports: list[dict] | None = None, findings: list[dict] | None = None):
        self.company = company if company is not None else {
            "id": COMPANY_ID,
            "name": "Alpha Studio",
            "primary_domain": "alpha.example.com",
            "primary_segment": "webflow_studio",
            "headcount_min": 12,
            "headcount_max": 40,
            "country": "United Kingdom",
            "lifecycle_stage": "identified",
        }
        self.reports = reports if reports is not None else [
            {"id": REPORT_ID, "company_id": COMPANY_ID, "report_version": 1, "created_at": "2026-11-13T00:00:00+00:00"}
        ]
        self.findings = findings if findings is not None else [
            finding(),
            finding(id="f2", category="client_roster", statement="Lists 14 agency clients.",
                    evidence_url="https://alpha.example.com/work"),
            finding(id="f3", category="growth_signals", statement="Hiring two backend engineers.",
                    evidence_url="https://alpha.example.com/careers"),
            finding(id="f4", category="business_model", statement="Retainer-based engagements.",
                    evidence_url="https://alpha.example.com/pricing"),
        ]
        self.calls: list[tuple[str, str, Any]] = []
        self.posted: list[dict] = []

    def get(self, table: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        self.calls.append(("GET", table, dict(params or {})))
        params = params or {}
        if table == "companies":
            return [self.company] if params.get("id") == f"eq.{COMPANY_ID}" else []
        if table == "research_reports":
            return list(self.reports)
        if table == "research_findings":
            report_filter = params.get("report_id")
            rows = [
                r
                for r in self.findings
                if not report_filter or report_filter == f"eq.{r['report_id']}"
            ]
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or 0)
            return rows[offset : offset + limit] if limit else rows[offset:]
        if table == "lead_qualifications":
            return []
        raise AssertionError(f"unexpected GET {table}")

    def post(self, table: str, data: Any) -> list[dict]:
        self.calls.append(("POST", table, data))
        assert table == "lead_qualifications"
        row = dict(data)
        row.setdefault("id", "qual-1")
        self.posted.append(row)
        return [row]


def test_findings_keep_their_own_identity():
    mapped = findings_from_rows([finding(), finding(id="f2", category="pricing_rates")])
    assert [f.finding_id for f in mapped] == ["f0000000-0000-4000-8000-000000000001", "f2"]
    assert mapped[0].evidence_url == "https://alpha.example.com/services"
    assert mapped[0].source_key == "We build Webflow sites for agencies."


def test_a_finding_without_an_id_is_refused_rather_than_reidentified():
    with pytest.raises(QualificationInputError) as exc:
        findings_from_rows([finding(id="")])
    assert "cannot be cited" in str(exc.value)


def test_a_company_without_research_is_refused():
    client = FakeClient(reports=[])
    with pytest.raises(QualificationInputError) as exc:
        load_evidence(client, COMPANY_ID)
    assert "no research report" in str(exc.value)


def test_a_report_without_findings_is_not_scored_as_zero():
    client = FakeClient(findings=[])
    with pytest.raises(QualificationInputError) as exc:
        score_company(client, COMPANY_ID)
    assert "nothing to " + "score" in str(exc.value)
    assert not [c for c in client.calls if c[0] == "POST"]


def test_the_input_uses_the_companys_own_profile_fields():
    client = FakeClient()
    evidence = load_evidence(client, COMPANY_ID)
    payload = build_input(evidence)
    assert payload.company_name == "Alpha Studio"
    assert payload.segment == "webflow_studio"
    assert payload.employee_estimate == 12, "headcount_min is the estimate that exists"
    assert payload.region == "United Kingdom"
    assert payload.findings[0].finding_id.startswith("f0000000")


def test_scoring_is_deterministic_and_never_final():
    client = FakeClient()
    first, _ = score_company(client, COMPANY_ID)
    second, _ = score_company(client, COMPANY_ID)
    assert first.total_score == second.total_score
    assert first.needs_human_review is True
    assert first.review_status == "draft"


def test_a_preview_does_not_write():
    client = FakeClient()
    result = qualify_company(client, COMPANY_ID, agent_identity="hermes-qualifier-v1", persist=False)
    assert result.persisted is None
    assert not [c for c in client.calls if c[0] == "POST"]


def test_persisting_writes_exactly_one_draft_with_the_engine_identity():
    client = FakeClient()
    result = qualify_company(client, COMPANY_ID, agent_identity="hermes-qualifier-v1")
    assert len(client.posted) == 1
    record = client.posted[0]
    assert record["evaluator_type"] == "agent"
    assert record["evaluator_identity"] == "hermes-qualifier-v1"
    assert record["review_status"] == "draft"
    assert record["company_id"] == COMPANY_ID
    assert result.persisted is not None


def test_a_scorecard_must_name_its_evaluator():
    with pytest.raises(ValueError):
        qualify_company(FakeClient(), COMPANY_ID, agent_identity="", persist=False)


def test_thin_evidence_is_refused_at_persist_rather_than_written_as_a_zero():
    client = FakeClient(findings=[finding()])
    with pytest.raises(Exception) as exc:
        qualify_company(client, COMPANY_ID, agent_identity="hermes-qualifier-v1")
    assert "inconclusive" in str(exc.value)
    assert not client.posted, "an unmeasured company must not gain a scorecard row"


# --- regressions from the independent adversarial review -------------------


def test_a_non_http_source_is_refused():
    """research_findings has no URL check of its own, so the loader is the only guard."""
    client = FakeClient(
        findings=[dict(finding(evidence_url="javascript:void(0)"))]
    )
    with pytest.raises(QualificationInputError) as exc:
        findings_from_rows(client.findings)
    assert "not a fetchable" in str(exc.value)


def test_a_verified_fact_without_a_source_is_refused():
    with pytest.raises(QualificationInputError) as exc:
        findings_from_rows([finding(evidence_url=None)])
    assert "verified fact with no source URL" in str(exc.value)


def test_findings_are_paged_rather_than_silently_truncated(monkeypatch):
    """PostgREST caps a response; an unpaged read would score a subset of the evidence."""
    import qualification.loader as loader

    many = [
        finding(id=f"f{i:03d}", category="technology_stack",
                statement=f"Fact {i}", evidence_url=f"https://alpha.example.com/{i}")
        for i in range(5)
    ]
    client = FakeClient(findings=many)
    monkeypatch.setattr(loader, "PAGE_SIZE", 2)
    evidence = loader.load_evidence(client, COMPANY_ID)
    assert len(evidence.findings) == 5, "every page must be read"
    pages = [c for c in client.calls if c[0] == "GET" and c[1] == "research_findings"]
    assert len(pages) == 3, "2 + 2 + 1"
    assert [p[2]["offset"] for p in pages] == ["0", "2", "4"]


def test_a_zero_headcount_means_unknown_not_empty():
    client = FakeClient(
        company={
            "id": COMPANY_ID, "name": "Alpha Studio", "primary_domain": "alpha.example.com",
            "primary_segment": "webflow_studio", "headcount_min": 0, "headcount_max": None,
            "country": "United Kingdom", "lifecycle_stage": "identified",
        }
    )
    payload = build_input(load_evidence(client, COMPANY_ID))
    assert payload.employee_estimate is None


def test_an_unverified_report_is_not_scored():
    client = FakeClient()
    client.reports[0]["overall_confidence"] = "unverified"
    with pytest.raises(QualificationInputError) as exc:
        score_company(client, COMPANY_ID)
    assert "unverified" in str(exc.value)
