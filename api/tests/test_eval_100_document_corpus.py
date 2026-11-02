"""The expanded retrieval set must really have 100 distinct source files."""

import json
from pathlib import Path


def test_retrieval_gold_covers_100_independent_documents():
    root = Path(__file__).resolve().parents[1] / "eval" / "fixtures"
    corpus = root / "corpus_100"
    manifest = json.loads((root / "corpus_100_manifest.json").read_text(encoding="utf-8"))
    gold = json.loads((root / "gold" / "retrieval_100docs.json").read_text(encoding="utf-8"))
    files = sorted(corpus.glob("*.md"))
    source_ids = {item["source_id"] for item in manifest}
    assert len(files) == len(manifest) == len(gold) == len(source_ids) == 100
    assert {path.stem for path in files} == source_ids
    assert len({item["page_id"] for item in manifest}) == 100
    assert len({item["question"] for item in gold}) == 100
    for case in gold:
        assert len(case["relevant_doc_ids"]) == 1
        assert case["relevant_doc_ids"][0] in source_ids
    for path in files:
        content = path.read_text(encoding="utf-8")
        assert len(content) >= 100
        assert "https://zh.wikipedia.org/wiki/" in content


def test_memory_fact_gold_covers_all_100_question_variants():
    gold = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "gold"
    questions = json.loads((gold / "memory_retrieval_100.json").read_text(encoding="utf-8"))
    anchors = json.loads((gold / "memory_retrieval_100facts.json").read_text(encoding="utf-8"))
    assert len(questions) == 100
    assert {item["query_group"] for item in questions} == set(anchors)
    assert all(len(value) >= 2 for value in anchors.values())
