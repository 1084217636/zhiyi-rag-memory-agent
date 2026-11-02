import pytest

from eval.grounded_metrics import compare_runs, score_answers, score_slices, validate_rows


def test_grounded_metrics_report_coverage_and_failure_slices():
    rows = [
        {
            "case_id": "supported", "case_type": "supported", "answerable": True,
            "refusal": False, "gold_evidence_ids": ["a", "b"],
            "cited_evidence_ids": ["a", "wrong"], "claim_support": [True, False],
            "expected_tools": ["knowledge_search"], "called_tools": ["knowledge_search"],
            "answer_correct": False, "latency_ms": 100,
        },
        {
            "case_id": "timeout", "case_type": "tool_failure", "answerable": False,
            "refusal": True, "tool_failed": True, "tool_failure_disclosed": True,
            "unsafe_action_executed": False, "tenant_data_leaked": False,
        },
    ]
    report = score_answers(rows)
    assert report["citation_precision"] == 0.5
    assert report["citation_recall"] == 0.5
    assert report["supported_claim_rate"] == 0.5
    assert report["tool_selection_accuracy"] == 1.0
    assert report["correct_refusal_rate"] == 1.0
    assert report["tool_failure_disclosure_rate"] == 1.0
    assert report["annotation_coverage"]["latency"] == 1
    assert score_slices(rows)["tool_failure"]["cases"] == 1


@pytest.mark.parametrize("bad", [
    {"case_id": "1", "answerable": "yes", "refusal": False},
    {"case_id": "1", "answerable": True, "refusal": False, "claim_support": ["yes"]},
    {"case_id": "1", "answerable": True, "refusal": False, "cost_usd": -1},
    {"case_id": "1", "answerable": True, "refusal": False, "tool_failure_disclosed": True},
])
def test_invalid_annotations_fail_before_scoring(bad):
    with pytest.raises(ValueError):
        score_answers([bad])


def test_paired_comparison_rejects_different_question_sets():
    current = [{"case_id": "a", "answerable": True, "refusal": False}]
    baseline = [{"case_id": "b", "answerable": True, "refusal": False}]
    with pytest.raises(ValueError, match="identical case_id"):
        compare_runs(current, baseline)


def test_paired_comparison_uses_same_cases_and_reports_delta():
    current = [{"case_id": "a", "answerable": True, "refusal": False,
                "answer_correct": True}]
    baseline = [{"case_id": "a", "answerable": True, "refusal": False,
                 "answer_correct": False}]
    validate_rows(current, require_ids=True)
    comparison = compare_runs(current, baseline)
    assert comparison["paired_cases"] == 1
    assert comparison["delta"]["answer_correct_rate"] == 1.0
    assert comparison["delta"]["mean_total_tokens"] is None


def test_paired_comparison_does_not_compare_different_gold_or_coverage():
    current = [{"case_id": "a", "case_type": "rag", "answerable": True,
                "refusal": False, "gold_evidence_ids": ["doc-1"], "answer_correct": True}]
    baseline = [{"case_id": "a", "case_type": "rag", "answerable": True,
                 "refusal": False, "gold_evidence_ids": ["doc-2"], "answer_correct": False}]
    with pytest.raises(ValueError, match="gold_evidence_ids"):
        compare_runs(current, baseline)
    baseline[0]["gold_evidence_ids"] = ["doc-1"]
    baseline[0].pop("answer_correct")
    assert compare_runs(current, baseline)["delta"]["answer_correct_rate"] is None
