import pytest
from pydantic import ValidationError

from app.schemas.research_schema import ArtifactCreate, ArtifactUpdate


def test_artifact_create_trims_required_fields_and_accepts_citations():
    item = ArtifactCreate(
        title="  WiFi CSI 笔记  ", content="  结论草稿  ",
        citations=[{"evidence_id": "K1", "source_id": "doc-1", "source_type": "document"}],
    )
    assert item.title == "WiFi CSI 笔记"
    assert item.content == "结论草稿"
    assert len(item.citations) == 1


def test_artifact_update_requires_positive_expected_version():
    with pytest.raises(ValidationError):
        ArtifactUpdate(title="改稿", content="内容", expected_version=0)


def test_artifact_create_rejects_blank_title():
    with pytest.raises(ValidationError):
        ArtifactCreate(title="  ", content="内容")
