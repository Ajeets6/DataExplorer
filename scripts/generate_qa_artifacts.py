import asyncio
import os
import sys
from pathlib import Path

from dataexplorer.artifacts import (
    ArtifactService,
    ArtifactToolPptxRenderer,
    DocxRenderer,
)
from dataexplorer.models import (
    AccessContext,
    ArtifactChart,
    ArtifactChartSeries,
    ArtifactSection,
    ArtifactSource,
    ArtifactSpec,
    ArtifactTable,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".artifacts" / "qa"


def spec(kind: str) -> ArtifactSpec:
    return ArtifactSpec(
        kind=kind,
        filename=f"quarterly-operating-brief.{kind}",
        title="Quarterly operating brief",
        subtitle="Governed performance summary",
        audience="Executive leadership",
        purpose="Support the quarterly operating review",
        classification="internal",
        sections=[
            ArtifactSection(
                title="Performance improved",
                summary="Median resolution time improved across the measured quarter.",
                bullets=[
                    "June closed four hours faster than April.",
                    "The trend improved in every observed month.",
                ],
                chart=ArtifactChart(
                    title="Median resolution hours",
                    categories=["April", "May", "June"],
                    series=[ArtifactChartSeries(name="Hours", values=[12, 10, 8])],
                ),
                source_ids={"ops"},
                speaker_notes="Lead with the sustained month-over-month improvement.",
            ),
            ArtifactSection(
                title="Controlled next steps",
                summary="The operating plan links each action to an accountable owner.",
                table=ArtifactTable(
                    title="Thirty-day operating plan",
                    columns=["Action", "Owner", "Due"],
                    rows=[
                        ["Review escalation routing", "Service Operations", "15 July"],
                        ["Validate knowledge coverage", "Knowledge Management", "22 July"],
                    ],
                ),
                source_ids={"plan"},
                speaker_notes="Confirm named owners before distribution.",
            ),
        ],
        sources=[
            ArtifactSource(
                source_id="ops",
                label="Approved operations dataset",
                locator="warehouse://operations/monthly-resolution",
                as_of="2026-06-30",
            ),
            ArtifactSource(
                source_id="plan",
                label="Approved operating plan",
                locator="dms://operating-plan/q3",
                as_of="2026-07-01",
            ),
        ],
    )


async def render(kind: str, renderer: object) -> Path:
    service = ArtifactService(output_root=OUTPUT, renderers={kind: renderer})
    requester = AccessContext(user_id="qa-author", tenant_id="qa")
    approver = AccessContext(
        user_id="qa-reviewer",
        tenant_id="qa",
        groups={"content-approvers"},
    )
    draft = await service.create(spec(kind), requester)
    approved = await service.decide(
        draft.artifact_id,
        approved=True,
        reason="QA source and layout review",
        access=approver,
    )
    rendered = await service.render(approved.artifact_id, requester)
    return Path(rendered.output_path or "")


async def main() -> None:
    node = Path(os.environ["ARTIFACT_TOOL_NODE"])
    node_modules = Path(os.environ["ARTIFACT_TOOL_NODE_MODULES"])
    kinds = set(sys.argv[1:] or ["docx", "pptx"])
    if "docx" in kinds:
        print(await render("docx", DocxRenderer()))
    if "pptx" in kinds:
        print(
            await render(
                "pptx",
                ArtifactToolPptxRenderer(
                    node_executable=node,
                    node_modules=node_modules,
                    script_path=ROOT / "src" / "dataexplorer" / "pptx_renderer.mjs",
                ),
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
