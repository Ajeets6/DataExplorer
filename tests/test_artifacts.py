from pathlib import Path
from zipfile import ZipFile

import pytest

from dataexplorer.artifacts import ArtifactPolicyError, ArtifactService, DocxRenderer
from dataexplorer.models import (
    AccessContext,
    ArtifactChart,
    ArtifactChartSeries,
    ArtifactSection,
    ArtifactSource,
    ArtifactSpec,
)


def sample_spec(kind: str = "docx") -> ArtifactSpec:
    return ArtifactSpec(
        kind=kind,
        filename=f"quarterly-brief.{kind}",
        title="Quarterly operating brief",
        subtitle="Governed performance summary",
        audience="Executive leadership",
        purpose="Support the quarterly operating review",
        sections=[
            ArtifactSection(
                title="Performance improved",
                summary="Resolution time improved across the measured period.",
                bullets=["The improvement is visible in all three months."],
                chart=ArtifactChart(
                    title="Median resolution hours",
                    categories=["April", "May", "June"],
                    series=[ArtifactChartSeries(name="Hours", values=[12, 10, 8])],
                ),
                source_ids={"ops"},
            )
        ],
        sources=[
            ArtifactSource(
                source_id="ops",
                label="Approved operations dataset",
                locator="warehouse://operations/monthly-resolution",
            )
        ],
    )


def test_artifact_spec_rejects_unsupported_claims() -> None:
    with pytest.raises(ValueError, match="requires source_ids"):
        ArtifactSpec(
            kind="docx",
            filename="unsupported.docx",
            title="Unsupported",
            audience="Executives",
            purpose="Demonstrate validation",
            sections=[ArtifactSection(title="Claim", summary="Unsupported claim")],
        )


async def test_docx_requires_independent_approval_and_renders(tmp_path: Path) -> None:
    service = ArtifactService(output_root=tmp_path, renderers={"docx": DocxRenderer()})
    requester = AccessContext(user_id="author", tenant_id="acme")
    approver = AccessContext(
        user_id="reviewer",
        tenant_id="acme",
        groups={"content-approvers"},
    )
    draft = await service.create(sample_spec(), requester)
    with pytest.raises(ArtifactPolicyError):
        await service.render(draft.artifact_id, requester)
    with pytest.raises(ArtifactPolicyError):
        await service.decide(
            draft.artifact_id,
            approved=True,
            reason="Self review",
            access=requester.model_copy(update={"groups": {"content-approvers"}}),
        )
    approved = await service.decide(
        draft.artifact_id,
        approved=True,
        reason="Sources and figures reconciled",
        access=approver,
    )
    rendered = await service.render(approved.artifact_id, requester)
    output = Path(rendered.output_path or "")
    assert rendered.status == "rendered"
    assert output.exists()
    assert rendered.output_sha256
    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "word/document.xml" in names
        assert not any(name.endswith("vbaProject.bin") for name in names)


async def test_cross_tenant_artifact_access_is_blocked(tmp_path: Path) -> None:
    service = ArtifactService(output_root=tmp_path, renderers={"docx": DocxRenderer()})
    owner = AccessContext(user_id="author", tenant_id="acme")
    outsider = AccessContext(
        user_id="reviewer",
        tenant_id="other",
        groups={"content-approvers"},
    )
    draft = await service.create(sample_spec(), owner)
    with pytest.raises(ArtifactPolicyError):
        await service.decide(
            draft.artifact_id,
            approved=True,
            reason="Not my tenant",
            access=outsider,
        )


class FakePublisher:
    async def publish(self, source: Path, object_name: str) -> str:
        assert source.exists()
        return f"gs://governed/{object_name}"


async def test_approved_artifact_can_publish_to_governed_object_storage(tmp_path: Path) -> None:
    service = ArtifactService(
        output_root=tmp_path,
        renderers={"docx": DocxRenderer()},
        publisher=FakePublisher(),
    )
    requester = AccessContext(user_id="author", tenant_id="acme")
    approver = AccessContext(
        user_id="reviewer", tenant_id="acme", groups={"content-approvers"}
    )
    draft = await service.create(sample_spec(), requester)
    await service.decide(
        draft.artifact_id,
        approved=True,
        reason="Approved sources",
        access=approver,
    )
    rendered = await service.render(draft.artifact_id, requester)
    assert rendered.output_path.startswith(f"gs://governed/acme/{draft.artifact_id}/")
