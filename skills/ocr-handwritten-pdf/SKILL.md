---
name: ocr-handwritten-pdf
description: Turn a scanned or handwritten PDF (lecture notes, problem sets, slides with annotations) into an Obsidian Markdown note with LaTeX formulas and cropped figures. Renders each page, transcribes text with a cheap vision model, detects figure boxes with Gemini, saves the drawings as PNGs next to the note, and can run a formatting pass. Use for requests like "OCR this PDF", "put these handwritten notes into my vault", "extract the figures from this scan", or "make a Markdown note from this lecture scan".
---

# OCR a handwritten PDF into a Markdown (Obsidian) note

Hybrid pipeline: a cheap text model transcribes the page (German wording kept, math as LaTeX) while Gemini returns only tight bounding boxes for figures. The text model receives the figure list and writes `[[FIG1]]` placeholders, which are replaced by cropped PNG links. An optional formatting pass adds headings, lists, tables and callouts without changing words or formulas.

- Text model: `openai/gpt-6-luna` (cheap, fast)
- Figure model: `google/gemini-3.8-flash`
- Provider routing sets `data_collection: deny`
- Pages are resumable: existing `md/page_NNNN.md` files are skipped unless `--force`

## Before running

1. **Locate the skill.** Find the absolute path of the folder that contains this `SKILL.md`. The scripts are in `scripts/` next to it.
2. **Check the API key.** The script reads `OPENROUTER_API_KEY` from the environment or from `~/.config/ocr-notes/.env` (override with `OCR_ENV_FILE`). If it is missing, stop and ask the user for the key. do not put the key in the vault.
3. **Confirm privacy when it matters.** Each page image is uploaded to OpenRouter and forwarded to the serving provider. If the document may be confidential, say so before sending it.
4. **Ask about scope when unclear:** page range, output folder, and whether the formatting pass is wanted. Cost is small (about $0.08–0.20 for 32 pages), but ask.

## Choose the destination

Images only render in Obsidian when the note and its `figures/` folder live
inside the vault. Never write output outside the vault unless the user asks.

- Vault root: the Obsidian vault that will contain the note (the folder with `.obsidian/`).
- Default output folder: `Files/<doc-slug>`, or a module folder such as
  `Modules/Mymodules/<Module>/<doc-slug>` when the user names one or the PDF is clearly course material.
- The final note sits **inside** that folder, next to `figures/`, so relative image links resolve. Do not move the note up a level after the run.

## Run the OCR

```bash
SKILL="<absolute path to this skill folder>"
OUT="<vault>/Files/<doc-slug>"

sh "$SKILL/scripts/run.sh" ocr \
  --pdf "/absolute/path/to/notes.pdf" \
  --out "$OUT" \
  --doc-name document.md \
  --reasoning low \
  --workers 4
```

- `--reasoning low` is the cheapest and fastest setting and is accurate enough for handwriting; drop it (provider default) if figure boxes come out sloppy.
- `--pages 1-3` or `--pages 1,4,7` for a subset; `--force` to redo pages.
- Re-running the same command resumes an interrupted run and retries failed pages (exit code 1 lists them).
- Wrapper behaviour: prefers `uv run` with pinned dependencies
  (`pymupdf`, `pillow`, `tqdm`, Python 3.12); falls back to `python3` from
  `requirements.txt`. No venv setup needed.

## Optional formatting pass

```bash
sh "$SKILL/scripts/run.sh" format --out "$OUT"
```

Adds headings, bold terms, lists, tables and GitHub alerts, then verifies that formulas, image links and words are unchanged (`--tolerance 0.02`). Pages that fail the check keep their original text and are listed at the end; rerun the same command to retry. Exit code 1 means some pages were rejected.

## Turn the output into the note

Output layout inside `$OUT`:

| Path | Content |
| --- | --- |
| `document.md` | all pages combined, `<!-- page N -->` markers |
| `document_formatted.md` | formatted version (after the format pass) |
| `figures/` | cropped drawings, linked as `figures/page_0007_fig1.png` |
| `overlays/` | page renders with red figure boxes, for checking |
| `md/`, `formatted/` | per-page Markdown |
| `pages/`, `raw/` | page PNGs and raw model answers, for debugging |

1. **Create the note** from `document_formatted.md` when it exists, otherwise
   from `document.md`. Give it a readable title based on the PDF/course, e.g.
   `$OUT/Quantenmechanik I.md`. Keep `document*.md` untouched as the raw
   machine output, or remove it once the note is verified.
2. **Prepend frontmatter** (adjust values):

   ```yaml
   ---
   source_pdf: /absolute/path/to/notes.pdf
   pages: 32
   ocr: hybrid (openai/gpt-6-luna + google/gemini-3.8-flash)
   created: 2026-10-21
   tags:
     - lecture-notes
     - ocr
   ---
   ```

3. **Keep the transcription honest.** Do not "fix" formulas, foreign language wording or figure references while wrapping the note. If the user wants corrections, do that as a separate, clearly marked pass.
4. **Move debug folders out of the way:** `mkdir -p "$OUT/.ocr"` and move `md/`, `formatted/`, `pages/`, `raw/`, `overlays/`, `document*.md` into it, unless the user wants them visible. Never delete `figures/`.
5. Keep `figures/` readable and stable; renaming or flattening it breaks links.

## Verify before reporting done

- Every `![](...)` link in the note resolves to a file in `figures/`
  (`grep -o '](figures/[^)]*)'` and test each path).
- No leftover `[[FIG` placeholders in the note.
- Page count matches the PDF (`<!-- page N -->` markers; blank pages are
  legitimately empty).
- Figures are not cut off: spot-check a few of `overlays/*.png`.
- Formula rendering: Obsidian renders `$...$` and `$$...$$` natively; a
  transcription error is a model limitation, not a formatting bug — report it
  rather than silently editing math.

## Notes and limits

- Handwriting accuracy depends on the model; the output is a machine
  transcription, so mention proofreading in the final message.
- Stacked or side-by-side figures can be split unreliably; check the overlay.
- Text inside figures stays in the image by design; only body text is
  transcribed.
- These scripts are copies of the upstream `ocr` pipeline (`ocr_hybrid.py`,
  `ocr_gemini.py`, `format_md.py`). When the upstream project changes, re-copy the
  scripts and keep this file in sync.
