#!/usr/bin/env python3
"""Hybrid OCR: text from a cheap model, figure boxes from Gemini.

Both models see the same page render, but they are asked different questions:

  1. figures model (Gemini): return ONLY the JSON figure list (boxes + descriptions)
  2. text model (luna): transcribe the page; it is given the figure list so it can
     place [[FIGn]] placeholders where the figures belong in the text

Cropping, linking and overlay drawing reuse the code in ocr_gemini.py.

Default: text = openai/gpt-6-luna, figures = google/gemini-3.8-flash.
API key: OPENROUTER_API_KEY environment variable, or a line in .env next to this script.
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf
from tqdm import tqdm

from ocr_gemini import (
    API_URL,
    FIG_BLOCK_RE,
    HERE,
    insert_figures,
    load_env_file,
    parse_pages,
    split_figures,
    split_inline_display_math,
)

TEXT_MODEL = "openai/gpt-6-luna"
FIGURES_MODEL = "google/gemini-3.8-flash"

# USD per million tokens (in, out); only used for the end-of-run cost summary.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.50),
    "google/gemini-3.8-flash": (0.825, 4.125),
}

LEFT_OVER_FIG_RE = re.compile(r"\[\[FIG\d+\]\]")
FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*[ \t]*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)

FIGURES_PROMPT = (
    "This is a page of handwritten German lecture notes (quantum mechanics / physics), "
    "possibly with printed slide content. List ONLY the figures (non-text graphics) on the page.\n"
    "Treat as a figure every non-text graphic, for example:\n"
    "- plots and graphs with axes, including hand-drawn sketches of functions, potentials V(x), "
    "wave functions, probability densities, Gaussians, step or well potentials;\n"
    "- schematic diagrams: particles with arrows, double-slit setups, detector setups, "
    "dot or point patterns (for example single hits on a screen);\n"
    "- drawings inside a frame or box (a box that contains a drawing or dot pattern is a figure);\n"
    "- pictures, photos, QR codes, icons.\n"
    "Each separately framed or separately labelled graphic is its own figure, even when several "
    "are stacked or side by side. Do NOT count as figures: plain text, formulas or equations "
    "(even when boxed), headings, tables of formulas, or arrows that only connect text.\n"
    "The box must be the tight bounding box of the WHOLE figure INCLUDING all handwritten labels, "
    "annotations, axis names and captions that belong to it. Prefer a slightly larger box over "
    "cutting a label off. Do not swallow neighbouring body text that is not part of the figure.\n"
    "Output exactly one JSON code block and nothing else:\n"
    "```json\n"
    '{"figures": [{"id": "FIG1", "box_2d": [ymin, xmin, ymax, xmax], '
    '"description": "short German description"}]}\n'
    "```\n"
    "box_2d is the tight bounding box of the whole figure including its labels, as integers 0-1000 "
    "relative to the full page (0,0 = top-left, 1000,1000 = bottom-right). "
    "If there are no figures, output {\"figures\": []}."
)

TEXT_PROMPT_LINES = (
    "This is a page of handwritten German lecture notes (quantum mechanics / physics), "
    "possibly with printed slide content.\n"
    "Transcribe all text on the page into Markdown. Keep the original German wording exactly; "
    "do not translate or correct spelling. Write mathematical formulas in LaTeX ($...$ inline, "
    "$$...$$ for display). Keep headings, lists and structure. Write unreadable words as [unleserlich]. "
    "Ignore page decorations.\n"
    "Do NOT describe the figures and do NOT transcribe the text inside them (labels and axis names "
    "stay in the image).\n"
    "Put a display formula ($$...$$) on its own line: if a label or other text comes before it on the "
    "same line (for example 'Title: $$x^2$$'), write the text, then a line break, then the formula.\n"
    "IMPORTANT: transcribe only what is visible on this page image. If the page is blank or nearly "
    "blank, output nothing. Never write text or formulas that are not visible on this page."
)


def build_text_prompt(figs):
    """Text prompt, with the figure list handed over so placeholders can be placed."""
    lines = [TEXT_PROMPT_LINES]
    if figs:
        listing = "\n".join(
            f"- [[{fig.get('id') or f'FIG{i}'}]] {fig.get('description') or ''}".rstrip()
            for i, fig in enumerate(figs, 1)
            if isinstance(fig, dict)
        )
        lines.append(
            f"The page contains {len(figs)} figure(s), listed here in the order they appear on the page:\n"
            f"{listing}\n"
            "Where each figure belongs in the text, put a line containing only its placeholder "
            "(for example [[FIG1]]). Use exactly these placeholders and no others."
        )
    else:
        lines.append("The page contains no figures: do not write any [[FIG...]] placeholder.")
    lines.append("Output the transcription only: no JSON block, no code fences, no commentary.")
    return "\n".join(lines)


def strip_code_fence(text):
    """Undo a model that wraps the whole answer in a ```markdown fence."""
    m = FENCE_RE.match(text.strip())
    return m.group("body").strip() if m else text.strip()


def drop_json_block(text):
    """Remove a JSON figure block the text model may have emitted on its own."""
    return FIG_BLOCK_RE.sub("", text).strip()


def call_model(api_key, model, prompt, image_png, retries, reasoning="", allow_empty=False):
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8000,
        # Only route to providers that do not store or train on the request data.
        "provider": {"data_collection": "deny"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(image_png).decode()
                        },
                    },
                ],
            }
        ],
    }
    if reasoning:
        payload["reasoning"] = {"effort": reasoning}
    body = json.dumps(payload).encode()
    last_err = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "ocrtest-hybrid",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                raise RuntimeError(f"API error: {str(data['error'])[:300]}")
            choice = data["choices"][0]
            text = (choice["message"].get("content") or "").strip()
            if not text and not allow_empty:
                raise RuntimeError(f"empty response (finish_reason={choice.get('finish_reason')})")
            return text, data.get("usage") or {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code not in (408, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, IndexError,
                json.JSONDecodeError) as e:
            last_err = repr(e)
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise RuntimeError(last_err)


def process_page(api_key, p, png, args, raw_dir, figdir):
    png_bytes = png.read_bytes()

    figs, figs_raw, figs_usage = [], "", {}
    try:
        figs_raw, figs_usage = call_model(
            api_key, args.figures_model, FIGURES_PROMPT, png_bytes, args.retries, args.reasoning
        )
        (raw_dir / f"page_{p:04d}.figs.txt").write_text(figs_raw + "\n", encoding="utf-8")
        _, figs = split_figures(figs_raw, p)
    except Exception as e:
        tqdm.write(f"page {p}: figures step failed ({e}); continuing without figures")

    text_raw, text_usage = call_model(
        api_key, args.text_model, build_text_prompt(figs), png_bytes, args.retries,
        args.reasoning, allow_empty=True,
    )
    (raw_dir / f"page_{p:04d}.text.txt").write_text(text_raw + "\n", encoding="utf-8")

    body = drop_json_block(strip_code_fence(text_raw))

    # Reconciliation: how many of the figures did the text model actually place?
    ids = [str(f.get("id") or f"FIG{i}") for i, f in enumerate(figs, 1) if isinstance(f, dict)]
    placed = [i for i in ids if f"[[{i}]]" in body]
    if figs and len(placed) != len(ids):
        tqdm.write(
            f"page {p}: text model placed {len(placed)}/{len(ids)} figure placeholders "
            f"({', '.join(i for i in ids if i not in placed)} unplaced -> appended at the end)"
        )

    body = insert_figures(body, figs, png, p, figdir)
    left = LEFT_OVER_FIG_RE.findall(body)
    if left:
        tqdm.write(f"page {p}: removed placeholder(s) without a figure: {sorted(set(left))}")
        body = LEFT_OVER_FIG_RE.sub("", body)
    return text_raw, figs_raw, split_inline_display_math(body), figs_usage, text_usage


def main():
    ap = argparse.ArgumentParser(description="Hybrid OCR: cheap text model + Gemini figures.")
    ap.add_argument("--pdf", default=str(HERE / "ocrtest.pdf"))
    ap.add_argument("--out", default=str(HERE / "ocrtest_hybrid"))
    ap.add_argument("--pages", default="", help="e.g. '1-3' or '1,4,7' (default: all)")
    ap.add_argument("--model", default=TEXT_MODEL, help="alias for --text-model")
    ap.add_argument("--text-model", default="", help=f"transcription model (default: {TEXT_MODEL})")
    ap.add_argument("--figures-model", default=FIGURES_MODEL, help="figure box model")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4, help="parallel pages (2 API calls each)")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--reasoning", default="", choices=["", "minimal", "low", "medium", "high"],
                    help="reasoning effort, applied to both calls (default: provider default)")
    ap.add_argument("--doc-name", default="hybrid.md", help="name of the combined Markdown file")
    ap.add_argument("--force", action="store_true", help="redo pages that already have output")
    args = ap.parse_args()
    args.text_model = args.text_model or args.model

    load_env_file(HERE / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set (export it, or put it in .env next to this script)")

    out = Path(args.out)
    pages_dir = out / "pages"
    md_dir = out / "md"
    figdir = out / "figures"
    raw_dir = out / "raw"
    for d in (pages_dir, md_dir, figdir, raw_dir):
        d.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    selected = parse_pages(args.pages, doc.page_count)
    todo = [p for p in selected if args.force or not (md_dir / f"page_{p:04d}.md").exists()]
    print(f"{len(selected)} pages selected, {len(selected) - len(todo)} already done, "
          f"{len(todo)} to run")
    print(f"  text:    {args.text_model}")
    print(f"  figures: {args.figures_model}")

    for p in tqdm(todo, desc="Render", unit="page"):
        png_path = pages_dir / f"page_{p:04d}.png"
        if args.force or not png_path.exists():
            doc[p - 1].get_pixmap(dpi=args.dpi).save(png_path)

    failed, stats = [], []

    def process(p):
        return process_page(api_key, p, pages_dir / f"page_{p:04d}.png", args, raw_dir, figdir)

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, p): p for p in todo}
            with tqdm(total=len(todo), desc="Hybrid OCR", unit="page") as bar:
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        _text, _figs, md, figs_usage, text_usage = fut.result()
                        (md_dir / f"page_{p:04d}.md").write_text(md + "\n", encoding="utf-8")
                        stats.append((args.text_model, text_usage))
                        stats.append((args.figures_model, figs_usage))
                    except Exception as e:
                        failed.append(p)
                        tqdm.write(f"page {p} FAILED: {e}")
                    bar.update(1)

    parts = []
    for p in range(1, doc.page_count + 1):
        f = md_dir / f"page_{p:04d}.md"
        if f.exists():
            text = split_inline_display_math(f.read_text(encoding="utf-8")).strip()
            f.write_text(text + "\n", encoding="utf-8")
            parts.append(f"<!-- page {p} -->\n\n{text}")
    combined = "\n\n".join(parts).replace("](../figures/", "](figures/")
    doc_path = out / args.doc_name
    doc_path.write_text(combined + "\n", encoding="utf-8")

    # Cost summary.
    totals = {}
    for model, usage in stats:
        if not isinstance(usage, dict):
            continue
        t = totals.setdefault(model, [0, 0])
        t[0] += int(usage.get("prompt_tokens") or 0)
        t[1] += int(usage.get("completion_tokens") or 0)
    if totals:
        cost = 0.0
        for model, (pin, pout) in sorted(totals.items()):
            price_in, price_out = PRICES.get(model, (0.0, 0.0))
            c = pin / 1e6 * price_in + pout / 1e6 * price_out
            cost += c
            print(f"  {model}: {pin} in / {pout} out tokens -> ${c:.4f}")
        print(f"Estimated cost: ${cost:.4f}")

    done = len(parts)
    print(f"Done: {done}/{doc.page_count} pages in {doc_path}")
    if failed:
        print(f"Failed pages (re-run the same command to retry): {sorted(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
