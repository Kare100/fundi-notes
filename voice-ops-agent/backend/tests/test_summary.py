"""Daily executive summary: structure, honesty about gaps, and totals that cannot be invented."""
from __future__ import annotations

from app.services.llm.base import LLMError
from helpers import create_demo

DAY = "2026-10-20"


def seed(client, day=DAY):
    out = {}
    for s in ("clean-installation", "blocked-waiting-for-part", "return-visit-needed", "contradictory-status", "vague-note"):
        out[s] = create_demo(client, sample_id=s, reported_on=day)
    return out


def get(client, day=DAY, **extra):
    r = client.get("/api/summary", params={"date": day, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_summary_contains_all_seven_required_sections(client):
    seed(client)
    s = get(client)
    text = s["text"]
    for heading in (
        "1. JOBS REPORTED (5)",
        "2. WORK COMPLETED (2)",
        "3. JOBS STILL IN PROGRESS (1)",
        "4. BLOCKED OR PARTIALLY COMPLETED JOBS (1)",
        "5. MISSING INFORMATION NEEDING ATTENTION",
        "6. OUTSTANDING ACTIONS",
        "7. NEEDS MANAGER INTERVENTION",
    ):
        assert heading in text, heading
    sec = s["sections"]
    assert len(sec["reported"]) == 5
    assert {i["label"] for i in sec["completed"]} == {"Greenfield Dental Clinic", "Eastside Logistics"}
    assert [i["label"] for i in sec["in_progress"]] == ["Lakeview Pharmacy"]
    blocked = sec["blocked_or_partial"][0]
    assert blocked["label"] == "Sunrise Bakery"
    assert "replacement control board has not arrived" in blocked["reasons"][0]


def test_totals_are_computed_not_invented(client):
    seed(client)
    t = get(client)["totals"]
    assert t == {**t, "total_reports": 5, "processed": 5, "failed": 0, "processing": 0, "reviewed": 0, "unreviewed": 5}
    assert t["completed"] + t["in_progress"] + t["blocked_or_incomplete"] + t["status_not_stated"] == t["processed"]


def test_unreviewed_ai_output_is_clearly_labelled(client):
    r = seed(client)["clean-installation"]
    s = get(client)
    assert any("not been reviewed by a manager" in n and "unverified AI output" in n for n in s["notes"])
    assert "[AI, unreviewed]" in s["text"]
    client.patch(f"/api/reports/{r['id']}", json={"reviewed": True})
    s = get(client)
    assert "[reviewed]" in s["text"]
    assert s["totals"]["reviewed"] == 1
    assert any("4 of 5 processed" in n for n in s["notes"])


def test_failed_reports_are_not_hidden_and_are_not_counted_as_done(client, monkeypatch):
    from helpers import FakeLLM
    from app.services import registry

    seed(client)  # processed by the mock extractor
    failing = FakeLLM().queue(LLMError("The AI service timed out. Try reprocessing the report."))
    monkeypatch.setattr(registry, "get_llm", lambda: failing)
    create_demo(client, text="This is Sam. I fixed the pump at the site and it works well now.", reported_on=DAY)
    s = get(client)
    assert s["totals"]["total_reports"] == 6 and s["totals"]["processed"] == 5 and s["totals"]["failed"] == 1
    assert any("failed to process and are NOT reflected" in n for n in s["notes"])
    assert "Failed to process (1)" in s["text"]
    assert any("Processing failed" in r for i in s["sections"]["needs_intervention"] for r in i["reasons"])


def test_summary_never_claims_everything_is_accounted_for(client):
    seed(client)
    s = get(client)
    assert any("cannot show jobs that were never uploaded" in n for n in s["notes"])
    for banned in ("all jobs", "everything is accounted"):
        assert banned not in s["text"].lower()


def test_empty_day_says_so(client):
    s = get(client, day="2026-01-01")
    assert s["totals"]["total_reports"] == 0
    assert "No reports were received for this period." in s["notes"]
    assert "None" in s["text"]


def test_only_the_selected_date_is_included_and_ranges_work(client):
    seed(client, DAY)
    create_demo(client, sample_id="swahili-english-mix", reported_on="2026-10-21")
    assert get(client, DAY)["totals"]["total_reports"] == 5
    assert get(client, "2026-10-21")["totals"]["total_reports"] == 1
    assert get(client, DAY, end="2026-10-21")["totals"]["total_reports"] == 6
    assert client.get("/api/summary", params={"date": DAY, "end": "2026-10-01"}).status_code == 422
    assert client.get("/api/summary", params={"date": "not-a-date"}).status_code == 422
    assert client.get("/api/summary").status_code == 422


def test_outstanding_actions_show_owner_and_deadline_only_when_stated(client):
    seed(client)
    actions = get(client)["sections"]["outstanding_actions"]
    by_desc = {a["description"]: a for a in actions}
    monday = by_desc["Return to site with a new pressure switch"]
    assert monday["owner"] == "Daniel Kamau" and monday["deadline"] == "Monday"
    unowned = by_desc["Technician will give an update later"]
    assert unowned["owner"] is None and unowned["deadline"] is None
    assert "Owner: not stated | Deadline: not stated" in get(client)["text"]


def test_completed_actions_drop_out_of_the_summary(client):
    r = seed(client)["return-visit-needed"]
    before = len(get(client)["sections"]["outstanding_actions"])
    client.patch(f"/api/actions/{r['actions'][0]['id']}", json={"completed": True})
    assert len(get(client)["sections"]["outstanding_actions"]) == before - 1


def test_manager_edits_flow_into_the_summary(client):
    r = seed(client)["vague-note"]
    client.patch(f"/api/reports/{r['id']}", json={"job_status": "blocked"})
    client.patch(f"/api/reports/{r['id']}/fields/issues", json={"value": "Customer not on site"})
    s = get(client)
    assert any(i["report_id"] == r["id"] and i["reasons"] == ["Customer not on site"] for i in s["sections"]["blocked_or_partial"])


def test_interventions_cover_blocked_conflicts_concerns_and_unowned_actions(client):
    seed(client)
    reasons = " | ".join(r for i in get(client)["sections"]["needs_intervention"] for r in i["reasons"])
    assert "Blocked:" in reasons
    assert "Conflicting information:" in reasons
    assert "Customer concern:" in reasons
    assert "Action has no owner:" in reasons


def test_overdue_iso_deadlines_are_marked(client):
    r = seed(client)["return-visit-needed"]
    client.patch(f"/api/actions/{r['actions'][0]['id']}", json={"deadline": "2020-01-01"})
    s = get(client)
    assert any(a["overdue"] for a in s["sections"]["outstanding_actions"])
    assert "(OVERDUE)" in s["text"]
    assert any("Overdue action" in x for i in s["sections"]["needs_intervention"] for x in i["reasons"])


def test_summary_text_is_plain_and_downloadable(client):
    seed(client)
    text = get(client)["text"]
    assert text.startswith("DAILY EXECUTIVE SUMMARY\nPeriod: 2026-10-20\n")
    assert text.endswith("\n") and "\r" not in text
    assert "—" not in text and "–" not in text


def test_demo_data_is_disclosed(client):
    seed(client)
    assert any("demo data" in n.lower() for n in get(client)["notes"])
