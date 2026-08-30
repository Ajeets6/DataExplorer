import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const artifactToolPath = path.join(
  process.env.ARTIFACT_TOOL_NODE_MODULES ?? process.env.NODE_PATH ?? "",
  "@oai",
  "artifact-tool",
  "dist",
  "artifact_tool.mjs",
);
const { Presentation, PresentationFile } = await import(pathToFileURL(artifactToolPath).href);

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

function addText(slide, name, text, position, fontSize, options = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name,
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize,
    typeface: "Arial",
    color: options.color ?? "#000000",
    bold: options.bold ?? false,
    alignment: options.alignment ?? "left",
    verticalAlignment: options.verticalAlignment ?? "top",
  };
  return shape;
}

function addFooter(slide, number, classification) {
  addText(slide, `classification-${number}`, classification.toUpperCase(),
    { left: 42, top: 670, width: 300, height: 24 }, 13, { color: "#666666" });
  addText(slide, `page-${number}`, String(number),
    { left: 1170, top: 670, width: 68, height: 24 }, 13, { alignment: "right" });
}

function sourcesFor(section, spec) {
  const lookup = new Map(spec.sources.map((source) => [source.source_id, source]));
  return [...section.source_ids].map((id) => lookup.get(id)).filter(Boolean);
}

function addSectionSlide(presentation, section, spec, number) {
  const slide = presentation.slides.add();
  slide.background.fill = "#FFFFFF";
  addText(slide, `title-${number}`, section.title,
    { left: 42, top: 36, width: 1196, height: 72 }, 38, { bold: true });
  if (section.chart) {
    slide.charts.add("line", {
      position: { left: 42, top: 132, width: 700, height: 470 },
      categories: section.chart.categories,
      series: section.chart.series.map((series, index) => ({
        name: series.name,
        values: series.values,
        line: {
          style: "solid",
          fill: ["#3D8DFF", "#31A77A", "#8A8F98", "#6DCBF4"][index],
          width: 4,
        },
      })),
      hasLegend: section.chart.series.length > 1,
      legend: { position: "bottom", overlay: false },
      dataLabels: { showValue: true, position: "above" },
      yAxis: { majorGridlines: { style: "solid", fill: "#EDEDED", width: 1 } },
    });
    addText(slide, `summary-${number}`, section.summary,
      { left: 790, top: 150, width: 410, height: 190 }, 24, { bold: true });
    addText(slide, `bullets-${number}`, section.bullets.map((item) => `• ${item}`).join("\n"),
      { left: 790, top: 370, width: 410, height: 210 }, 18);
  } else if (section.table) {
    addText(slide, `summary-${number}`, section.summary,
      { left: 42, top: 118, width: 1196, height: 70 }, 20);
    const table = slide.tables.add({
      name: `table-${number}`,
      left: 42,
      top: 210,
      width: 1196,
      height: 390,
      rows: 1 + section.table.rows.length,
      columns: section.table.columns.length,
      values: [section.table.columns, ...section.table.rows.map((row) => row.map(String))],
    });
    table.styleOptions = { headerRow: true, bandedRows: true };
    table.borders.assign({ style: "solid", fill: "#B8BCC4", width: 1 });
  } else {
    addText(slide, `summary-${number}`, section.summary,
      { left: 42, top: 150, width: 920, height: 170 }, 28, { bold: true });
    addText(slide, `bullets-${number}`, section.bullets.map((item) => `• ${item}`).join("\n"),
      { left: 42, top: 350, width: 1000, height: 230 }, 21);
  }
  const notes = [section.speaker_notes, "[Sources]",
    ...sourcesFor(section, spec).map((source) => `${source.label}: ${source.locator}`)]
    .filter(Boolean).join("\n");
  slide.speakerNotes.textFrame.setText(notes);
  slide.speakerNotes.setVisible(true);
  addFooter(slide, number, spec.classification);
  return slide;
}

async function main() {
  const [specPath, outputPath, qaDirectory] = process.argv.slice(2);
  if (!specPath || !outputPath || !qaDirectory) throw new Error("expected spec, output, and QA paths");
  const spec = JSON.parse(await fs.readFile(specPath, "utf8"));
  const presentation = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  const cover = presentation.slides.add();
  cover.background.fill = "#FFFFFF";
  addText(cover, "cover-kicker", spec.classification.toUpperCase(),
    { left: 42, top: 42, width: 300, height: 40 }, 24, { color: "#3D8DFF", bold: true });
  addText(cover, "cover-title", spec.title,
    { left: 42, top: 180, width: 1050, height: 250 }, 64, { bold: true, verticalAlignment: "bottom" });
  addText(cover, "cover-subtitle", spec.subtitle,
    { left: 42, top: 500, width: 850, height: 100 }, 26);
  cover.speakerNotes.textFrame.setText(
    `[Sources]\nGenerated from governed ArtifactSpec ${spec.template_version}`,
  );
  cover.speakerNotes.setVisible(true);
  addFooter(cover, 1, spec.classification);
  spec.sections.forEach((section, index) => addSectionSlide(presentation, section, spec, index + 2));

  await fs.mkdir(qaDirectory, { recursive: true });
  const layouts = [];
  for (const [index, slide] of presentation.slides.items.entries()) {
    const number = index + 1;
    await writeBlob(`${qaDirectory}/slide-${number}.png`,
      await presentation.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    const layoutPath = `${qaDirectory}/slide-${number}.layout.json`;
    await fs.writeFile(layoutPath, await layout.text());
    layouts.push(layoutPath);
  }
  await writeBlob(`${qaDirectory}/montage.webp`,
    await presentation.export({ format: "webp", montage: true, scale: 1 }));
  const inspection = await presentation.inspect({
    kind: "slide,textbox,shape,table,chart,notes",
    maxChars: 50000,
  });
  await fs.writeFile(`${qaDirectory}/inspection.ndjson`, inspection.ndjson);
  await fs.writeFile(`${qaDirectory}/manifest.json`, JSON.stringify({
    passed: true,
    slide_count: presentation.slides.items.length,
    layouts,
    montage: `${qaDirectory}/montage.webp`,
  }, null, 2));
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(outputPath);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
