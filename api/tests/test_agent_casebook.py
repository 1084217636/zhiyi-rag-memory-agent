from eval.build_agent_casebook import build_casebook


def test_casebook_covers_existing_gold_and_named_failure_scenarios():
    cases = build_casebook()
    assert len(cases) == 49
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert sum(case["case_type"] == "rag" for case in cases) == 22
    assert sum(case["case_type"] == "memory" for case in cases) == 19
    assert {"no_answer", "tool_failure", "tenant_boundary", "prompt_injection"}.issubset(
        {case["case_type"] for case in cases}
    )
