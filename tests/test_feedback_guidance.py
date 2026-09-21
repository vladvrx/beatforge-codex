from beatforge.feedback_guidance import derive_feedback_adjustment, qa_next_actions


def report(chart, tags=(), **changes):
    return {"schemaVersion": 1, "chartHash": chart, "tester": "local", "difficulty": "Expert", "source": "human", "tags": list(tags), "createdAt": "2026-09-12T01:00:00Z", **changes}


def test_duplicates_and_sections_do_not_inflate_readiness():
    records = [report("one", ["too_dense"]) for _ in range(6)]
    records.append(report("one", ["too_dense"], startBeat=4, endBeat=8))
    result = derive_feedback_adjustment(records, {})
    assert result["evidenceCount"] == 1
    assert result["ready"] is False
    assert result["changedFields"] == {}


def test_latest_report_replaces_old_vote_and_other_testers_are_excluded():
    records = [report("one", ["too_dense"]), report("one", ["too_sparse"], createdAt="2026-09-12T02:00:00Z"), report("two", ["too_sparse"]), report("three", ["too_sparse"]), report("four", ["too_dense"], tester="other")]
    result = derive_feedback_adjustment(records, {"density": 1.0}, difficulty="Expert")
    assert result["ready"] and result["evidenceCount"] == 3
    assert result["suggestedPlan"]["density"] == 1.15


def test_suggestion_is_bounded_and_preserves_manual_controls_and_input():
    plan = {"density": 0.55, "style": "tech", "noBombs": True, "dominantInstrument": "bass", "sectionOverrides": [{"id": "section-001", "density": 1.4}]}
    records = [report(str(i), ["too_dense", "awkward", "repetitive"]) for i in range(3)]
    result = derive_feedback_adjustment(records, plan)
    suggested = result["suggestedPlan"]
    assert suggested["density"] == 0.5
    assert suggested["intensity"] == 0.9
    assert suggested["candidateCount"] == 3
    assert all(suggested[key] == plan[key] for key in ("style", "noBombs", "dominantInstrument", "sectionOverrides"))
    assert plan["density"] == 0.55


def test_automated_or_wrong_difficulty_reports_are_not_human_evidence():
    records = [report(str(i), ["too_dense"], source="automated") for i in range(3)]
    records += [report("easy", ["too_dense"], difficulty="Easy")]
    assert derive_feedback_adjustment(records, {}, difficulty="Expert")["evidenceCount"] == 0


def test_mixed_signals_make_no_density_adjustment_and_timing_gets_review_action():
    records = [report("one", ["too_dense"]), report("two", ["too_sparse"]), report("three", ["off_beat"])]
    result = derive_feedback_adjustment(records, {})
    assert result["changedFields"] == {}
    assert result["nextActions"] and "timing" in result["nextActions"][0]


def test_qa_prioritizes_known_blockers_and_never_marks_a_chart_playtested():
    actions = qa_next_actions({"errors": [{"code": "HANDCLAP", "message": "Hands collide"}]}, {"status": "needs_anchors"})
    assert [item["action"] for item in actions] == ["repair_structure", "review_timing"]
    assert qa_next_actions({"errors": []}, {"status": "timing_verified"})[0]["action"] == "human_playtest"
