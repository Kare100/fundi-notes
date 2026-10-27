"""Report editing, flag resolution, actions, filtering, authentication and transcript preservation."""
from __future__ import annotations

from helpers import create_demo


def fld(report, key):
    return next(f for f in report["fields"] if f["key"] == key)


def flag_for(report, code_field, type_="missing"):
    return next(f for f in report["flags"] if f["field_key"] == code_field and f["type"] == type_)


# --------------------------------------------------------------------- authentication boundary
def test_api_requires_authentication(anon_client):
    for path in ("/api/reports", "/api/stats", "/api/config", "/api/samples", "/api/summary?date=2026-10-24"):
        assert anon_client.get(path).status_code == 401, path
    assert "www-authenticate" not in {k.lower() for k in anon_client.get("/api/reports").headers}


def test_wrong_password_is_rejected(anon_client):
    assert anon_client.get("/api/reports", auth=("manager", "wrong")).status_code == 401
    assert anon_client.get("/api/reports", auth=("someone", "test-password")).status_code == 401


def test_health_is_public_and_leaks_nothing_sensitive(anon_client):
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "auth_required", "demo_transcription", "demo_llm"}


def test_auth_can_be_disabled_explicitly(anon_client, set_env):
    set_env(AUTH_ENABLED="false")
    assert anon_client.get("/api/reports").status_code == 200


def test_config_never_exposes_secrets(client, set_env):
    set_env(OPENAI_API_KEY="sk-super-secret", ANTHROPIC_API_KEY="sk-ant-secret")
    body = client.get("/api/config").text
    assert "secret" not in body


# --------------------------------------------------------------------- editing keeps the original transcript
def test_editing_fields_preserves_original_transcript_and_ai_value(client):
    r = create_demo(client, sample_id="done-but-incomplete-info")
    original = r["transcript"]["original_text"]
    assert fld(r, "customer")["value"] is None

    edited = client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": "  Maji   Safi Ltd "}).json()
    f = fld(edited, "customer")
    assert f["value"] == "Maji Safi Ltd"
    assert f["ai_value"] is None, "the AI's original value is kept for audit"
    assert f["review_state"] == "corrected"
    assert f["edited_at"] is not None
    assert edited["transcript"]["original_text"] == original
    assert edited["job_report"]["header"]["customer"] == "Maji Safi Ltd"
    assert "Customer: Maji Safi Ltd" in edited["report_text"]

    # the edit is saved, not just echoed
    again = client.get(f"/api/reports/{r['id']}").json()
    assert fld(again, "customer")["value"] == "Maji Safi Ltd"
    assert again["transcript"]["original_text"] == original


def test_editing_a_list_field_splits_lines_and_drops_blanks(client):
    r = create_demo(client, sample_id="clean-installation")
    edited = client.patch(
        f"/api/reports/{r['id']}/fields/parts_materials", json={"value": ["Copper pipe", "", "Bracket", "copper pipe"]}
    ).json()
    assert fld(edited, "parts_materials")["value"] == ["Copper pipe", "Bracket", "copper pipe"]
    edited = client.patch(f"/api/reports/{r['id']}/fields/equipment", json={"value": "Unit A\nUnit B\n\n"}).json()
    assert fld(edited, "equipment")["value"] == ["Unit A", "Unit B"]


def test_filling_a_missing_field_closes_its_flag_automatically(client):
    r = create_demo(client, sample_id="done-but-incomplete-info")
    assert flag_for(r, "customer")["state"] == "outstanding"
    edited = client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": "Maji Safi Ltd"}).json()
    flag = flag_for(edited, "customer")
    assert flag["state"] == "corrected" and flag["resolved_by"] == "system"
    assert edited["missing_info"] is True  # testing is still missing


def test_clearing_a_value_reopens_the_flag(client):
    r = create_demo(client, sample_id="done-but-incomplete-info")
    client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": "Maji Safi Ltd"})
    reopened = client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": ""}).json()
    assert flag_for(reopened, "customer")["state"] == "outstanding"


def test_manager_can_change_status_and_contradiction_updates(client):
    r = create_demo(client, sample_id="contradictory-status")
    assert any(f["type"] == "contradiction" and f["state"] == "outstanding" for f in r["flags"])
    edited = client.patch(f"/api/reports/{r['id']}", json={"job_status": "awaiting_return_visit"}).json()
    assert edited["status"] == "awaiting_return_visit"
    assert edited["status_label"] == "Awaiting return visit"
    rule_contradictions = [f for f in edited["flags"] if f["origin"] == "rule" and f["type"] == "contradiction"]
    assert rule_contradictions, "the rule based contradiction should still be listed, but resolved"
    assert all(f["state"] != "outstanding" for f in rule_contradictions)


def test_invalid_status_is_rejected(client):
    r = create_demo(client, sample_id="clean-installation")
    assert client.patch(f"/api/reports/{r['id']}", json={"job_status": "finished-ish"}).status_code == 422
    assert client.patch(f"/api/reports/{r['id']}/fields/job_status", json={"value": "done!"}).status_code == 422


def test_unknown_field_and_unknown_keys_are_rejected(client):
    r = create_demo(client, sample_id="clean-installation")
    assert client.patch(f"/api/reports/{r['id']}/fields/not_a_field", json={"value": "x"}).status_code == 404
    assert client.patch(f"/api/reports/{r['id']}", json={"hacker": True}).status_code == 422
    assert client.patch(f"/api/reports/{r['id']}/fields/customer", json={}).status_code == 422


def test_editing_the_summary_keeps_the_ai_version(client):
    r = create_demo(client, sample_id="clean-installation")
    edited = client.patch(f"/api/reports/{r['id']}", json={"summary": "Manager rewrite."}).json()
    assert edited["summary"] == "Manager rewrite."
    assert edited["summary_ai"] == r["summary"]
    assert any("edited by a manager" in n for n in edited["job_report"]["source_confidence"]["notes"])


def test_mark_report_reviewed(client):
    r = create_demo(client, sample_id="clean-installation")
    assert r["reviewed"] is False
    done = client.patch(f"/api/reports/{r['id']}", json={"reviewed": True}).json()
    assert done["reviewed"] is True
    assert "Reviewed by a manager." in done["job_report"]["source_confidence"]["notes"]


def test_cannot_edit_fields_of_a_failed_report(client, fake_llm):
    from app.services.llm.base import LLMError

    fake_llm.queue(LLMError("down"))
    r = create_demo(client, text="This is Sam. I fixed the pump at the site today and it works.")
    assert r["state"] == "failed"
    assert client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": "X"}).status_code == 409


# --------------------------------------------------------------------- flags: confirm / correct / not applicable
def test_manager_can_resolve_flags_four_ways(client):
    r = create_demo(client, sample_id="vague-note")
    flags = [f for f in r["flags"] if f["state"] == "outstanding"]
    states = ["confirmed", "corrected", "not_applicable", "outstanding"]
    for flag, state in zip(flags, states):
        updated = client.patch(f"/api/flags/{flag['id']}", json={"state": state, "note": "checked by phone"}).json()
        got = next(f for f in updated["flags"] if f["id"] == flag["id"])
        assert got["state"] == state
        if state != "outstanding":
            assert got["resolved_by"] == "manager" and got["resolved_at"] and got["note"] == "checked by phone"
    assert client.patch("/api/flags/99999", json={"state": "confirmed"}).status_code == 404
    assert client.patch(f"/api/flags/{flags[0]['id']}", json={"state": "banana"}).status_code == 422


def test_manager_decision_is_not_overridden_by_the_checker(client):
    r = create_demo(client, sample_id="done-but-incomplete-info")
    flag = flag_for(r, "testing_confirmed")
    client.patch(f"/api/flags/{flag['id']}", json={"state": "not_applicable"})
    after = client.patch(f"/api/reports/{r['id']}", json={"reviewed": True}).json()  # triggers a re-check
    assert flag_for(after, "testing_confirmed")["state"] == "not_applicable"


def test_resolved_flags_stop_counting_as_missing_information(client):
    r = create_demo(client, sample_id="vague-note")
    last = r
    for flag in [f for f in r["flags"] if f["type"] == "missing"]:
        last = client.patch(f"/api/flags/{flag['id']}", json={"state": "confirmed"}).json()
    assert last["missing_info"] is False


# --------------------------------------------------------------------- actions
def test_actions_can_be_completed_added_edited_and_removed(client):
    r = create_demo(client, sample_id="blocked-waiting-for-part")
    first = r["actions"][0]
    done = client.patch(f"/api/actions/{first['id']}", json={"completed": True}).json()
    a = next(x for x in done["actions"] if x["id"] == first["id"])
    assert a["completed"] is True and a["completed_at"]
    assert done["open_actions"] == r["open_actions"] - 1

    reopened = client.patch(f"/api/actions/{first['id']}", json={"completed": False}).json()
    assert next(x for x in reopened["actions"] if x["id"] == first["id"])["completed_at"] is None

    added = client.post(
        f"/api/reports/{r['id']}/actions", json={"description": "Call the supplier", "owner": "Mercy", "deadline": "Thursday"}
    ).json()
    new = added["actions"][-1]
    assert (new["description"], new["owner"], new["deadline"], new["source"]) == ("Call the supplier", "Mercy", "Thursday", "manager")

    edited = client.patch(f"/api/actions/{new['id']}", json={"owner": "Peter"}).json()
    assert next(x for x in edited["actions"] if x["id"] == new["id"])["owner"] == "Peter"

    removed = client.delete(f"/api/actions/{new['id']}").json()
    assert all(x["id"] != new["id"] for x in removed["actions"])
    assert client.patch("/api/actions/99999", json={"completed": True}).status_code == 404


def test_setting_an_owner_closes_the_missing_owner_flag(client):
    r = create_demo(client, sample_id="vague-note")
    action = r["actions"][0]
    assert any("responsible" in f["question"].lower() and f["state"] == "outstanding" for f in r["flags"])
    updated = client.patch(f"/api/actions/{action['id']}", json={"owner": "Peter"}).json()
    assert not any("responsible" in f["question"].lower() and f["state"] == "outstanding" for f in updated["flags"])


def test_completed_actions_no_longer_need_an_owner(client):
    r = create_demo(client, sample_id="vague-note")
    updated = client.patch(f"/api/actions/{r['actions'][0]['id']}", json={"completed": True}).json()
    assert not any("responsible" in f["question"].lower() and f["state"] == "outstanding" for f in updated["flags"])


# --------------------------------------------------------------------- list, filters, stats
def seed_all(client):
    return {s: create_demo(client, sample_id=s) for s in (
        "clean-installation", "done-but-incomplete-info", "blocked-waiting-for-part",
        "return-visit-needed", "contradictory-status", "swahili-english-mix", "vague-note",
    )}


def test_dashboard_statistics_match_the_reports(client):
    seed_all(client)
    s = client.get("/api/stats").json()
    assert s["total_reports"] == 7 and s["processed"] == 7 and s["failed"] == 0
    assert s["completed"] == 3        # clean, done-but-incomplete, contradictory (as reported)
    assert s["in_progress"] == 2      # two awaiting a return visit
    assert s["blocked_or_incomplete"] == 1
    assert s["status_not_stated"] == 1
    assert s["completed"] + s["in_progress"] + s["blocked_or_incomplete"] + s["status_not_stated"] == s["processed"]
    assert s["with_missing_info"] == 4
    assert s["open_actions"] == 7
    assert s["reports_with_open_actions"] == 5
    assert s["unreviewed"] == 7


def test_filters(client):
    seed_all(client)
    ids = lambda **p: {r["label"] for r in client.get("/api/reports", params=p).json()}  # noqa: E731
    assert ids(status="blocked") == {"Sunrise Bakery"}
    assert len(client.get("/api/reports", params={"status": "awaiting_return_visit"}).json()) == 2
    assert ids(technician="daniel kamau") == {"Lakeview Pharmacy"}
    assert "Greenfield Dental Clinic" not in ids(missing_info="true")
    assert ids(action_required="false") == {"Greenfield Dental Clinic"}
    assert ids(search="oven") == {"Sunrise Bakery"}
    assert ids(search="kuNdI NoNe") == set()
    assert client.get("/api/reports", params={"status": "failed"}).json() == []
    assert client.get("/api/technicians").json()[0] == "Achieng"


def test_date_filter_and_reported_date_edit(client):
    a = create_demo(client, sample_id="clean-installation", reported_on="2026-10-01")
    create_demo(client, sample_id="vague-note", reported_on="2026-10-02")
    got = client.get("/api/reports", params={"date_from": "2026-10-01", "date_to": "2026-10-01"}).json()
    assert [r["id"] for r in got] == [a["id"]]
    client.patch(f"/api/reports/{a['id']}", json={"reported_on": "2026-10-02"})
    assert len(client.get("/api/reports", params={"date_from": "2026-10-02", "date_to": "2026-10-02"}).json()) == 2


def test_report_not_found_and_delete(client):
    assert client.get("/api/reports/999").status_code == 404
    r = create_demo(client, sample_id="clean-installation")
    assert client.delete(f"/api/reports/{r['id']}").status_code == 204
    assert client.get(f"/api/reports/{r['id']}").status_code == 404
    assert client.get("/api/reports").json() == []


def test_reprocess_discards_manager_edits_and_restores_ai_output(client):
    r = create_demo(client, sample_id="clean-installation")
    client.patch(f"/api/reports/{r['id']}/fields/customer", json={"value": "Someone Else"})
    assert client.post(f"/api/reports/{r['id']}/reprocess").status_code == 202
    again = client.get(f"/api/reports/{r['id']}").json()
    assert fld(again, "customer")["value"] == "Greenfield Dental Clinic"
    assert again["transcript"]["original_text"] == r["transcript"]["original_text"]


def test_report_text_follows_the_required_structure(client):
    r = create_demo(client, sample_id="blocked-waiting-for-part")
    text = r["report_text"]
    order = [
        "JOB REPORT", "Customer:", "Technician:", "Date:", "Job reference:", "Location:", "Status:",
        "Executive summary:", "Work completed:", "Materials or equipment:", "Outstanding issues:",
        "Next actions:", "Missing information:", "Source confidence:",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert "Status: Blocked" in text
    assert "Customer: Sunrise Bakery" in text
    assert "Work completed:\n- Not provided" in text
