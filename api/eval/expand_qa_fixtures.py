"""Create a reproducible 100-row paraphrase regression set from reviewed gold items.

The generated rows are correlated by source question/fact group. They measure
query phrasing robustness; they are not presented as 100 independent facts.
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "gold"
FORMS = [
    "{q}",
    "请根据我上传的资料回答：{q}",
    "资料里有没有说明：{q}",
    "我想核对文档中的信息，{q}",
    "从知识库内容看，{q}",
]
EXTRA_FORMS = [
    "请从用户长期记忆里回答：{q}",
    "基于以往对话，{q}",
    "用户曾经提到过吗：{q}",
    "回顾用户之前告诉系统的内容，{q}",
    "关于用户个人信息，{q}",
]


def expand(source: str, target: str, limit: int = 100) -> None:
    rows = json.loads((ROOT / source).read_text(encoding="utf-8"))
    output = []
    for index, row in enumerate(rows):
        for form_index, form in enumerate(FORMS):
            output.append({
                **row,
                "case_id": f"{Path(source).stem}-{index + 1:02d}-{form_index + 1}",
                "query_group": f"{Path(source).stem}-{index + 1:02d}",
                "question": form.format(q=row["question"]),
            })
            if len(output) == limit:
                (ROOT / target).write_text(
                    json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                return
    while len(output) < limit:
        index = len(output) - len(rows) * len(FORMS)
        row = rows[0]
        output.append({
            **row,
            "case_id": f"{Path(source).stem}-extra-{index + 1:03d}",
            "query_group": f"{Path(source).stem}-01",
            "question": EXTRA_FORMS[index % len(EXTRA_FORMS)].format(q=row["question"]),
        })
    (ROOT / target).write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    expand("retrieval.json", "retrieval_100.json")
    expand("memory_retrieval.json", "memory_retrieval_100.json")
