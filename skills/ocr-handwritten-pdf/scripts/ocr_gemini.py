#!/usr/bin/env python3
"""OCR a scanned PDF page by page with a vision LLM via OpenRouter.

Default model: google/gemini-3.8-flash.
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
from PIL import Image, ImageDraw
from tqdm import tqdm

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.8-flash"
HERE = Path(__file__).resolve().parent

PROMPT = (
    "This is a page of handwritten lecture notes, "
    "possibly with printed slide content.\n"
    "1) Transcribe all text on the page into Markdown. Keep the original wording exactly; "
    "do not translate or correct spelling. Write mathematical formulas in LaTeX ($...$ inline, "
    "$$...$$ for display). Keep headings, lists and structure. Write unreadable words as [UNREADABLE]. "
    "Ignore page decorations.\n"
    "2) FIGURES. Scan the whole page, including margins and small sketches, and treat as a figure "
    "every non-text graphic, for example:\n"
    "- plots and graphs with axes, including hand-drawn sketches of functions of any kind \n"
    "- schematic diagrams: make sure to capture the structure ofschematics in accurate manner, use LaTeX or Unicode if necessary\n"
    "- drawings inside a frame or box (a box that contains a drawing or dot pattern is a figure);\n"
    "- pictures, photos, QR codes, icons.\n"
    "Each separately framed or separately labelled graphic is its own figure, even when several "
    "are stacked or side by side. Do NOT count as figures: plain text, formulas or equations "
    "(even when boxed), headings, tables of formulas, or arrows that only connect text.\n"
    "Do NOT transcribe the text inside a figure (labels and axis names stay in the image). "
    "Where a figure belongs in the text, put a line containing only [[FIG1]], [[FIG2]], etc. "
    "in the order the figures appear on the page. A figure that has no place in the text is still listed.\n"
    "Put a display formula ($$...$$) on its own line: if a label or other text comes before it on the "
    "same line (for example 'Title: $$x^2$$'), write the text, then a line break, then the formula.\n"
    "IMPORTANT: transcribe only what is visible on this page image. If the page is blank or nearly "
    "blank, output no transcription and an empty figure list. Never write text, formulas or "
    "figures that are not visible on this page.\n"
    "3) After the transcription, output exactly one JSON code block listing the figures:\n"
    "```json\n"
    '{"figures": [{"id": "FIG1", "box_2d": [ymin, xmin, ymax, xmax], '
    '"description": "short description"}]}\n'
    "```\n"
    "box_2d is the tight bounding box of the whole figure INCLUDING every handwritten label, "
    "annotation, axis name and caption that belongs to it. Prefer a slightly larger box over "
    "cutting off a label; do not swallow neighbouring body text. Use integers 0-1000 "
    "relative to the full page (0,0 = top-left, 1000,1000 = bottom-right). "
    "If there are no figures, output {\"figures\": []}. Output nothing else."
)

FIG_BLOCK_RE = re.compile(r"```json\s*(\{.*\})\s*```", re.DOTALL)

# Models sometimes put LaTeX in a JSON string without escaping the backslash.
# A lone backslash before a letter is either invalid JSON ("\psi") or silently
# becomes a control character ("\rightarrow" -> CR + "ightarrow"), so in the
# repair path every backslash that is not one of the real JSON escapes is doubled.
LATEX_ESCAPE_RE = re.compile(r'(?<!\\)\\(?![\\"/]|u[0-9a-fA-F]{4})')


def loads_lenient(text):
    """json.loads, but tolerate LaTeX like "$\\psi$" that was not escaped."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(LATEX_ESCAPE_RE.sub(r"\\\\", text))


# A line with text before a display formula, e.g. "Title: $$x^2$$", becomes two lines.
INLINE_DISPLAY_RE = re.compile(r"^(?P<pre>[^\n$]*\S)[ \t]*(?P<math>\$\$[^\n]+?\$\$)[ \t]*$", re.M)


def split_inline_display_math(text):
    """Rewrite 'Title: $$x$$' as 'Title:' followed by a line with '$$x$$'."""
    return INLINE_DISPLAY_RE.sub(lambda m: f"{m.group('pre').rstrip()}\n{m.group('math')}", text)


def load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_pages(spec, n_pages):
    if not spec:
        return list(range(1, n_pages + 1))
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        elif part:
            pages.add(int(part))
    bad = sorted(p for p in pages if not 1 <= p <= n_pages)
    if bad:
        sys.exit(f"Pages out of range 1-{n_pages}: {bad}")
    return sorted(pages)


def call_openrouter(api_key, model, image_png, retries, reasoning=""):
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
                    {"type": "text", "text": PROMPT},
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
        # Reasoning models (e.g. Gemini 3.x) otherwise spend a lot of thinking
        # tokens on every page; "low" is much cheaper with no visible loss here.
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
                "X-Title": "ocrtest",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                raise RuntimeError(f"API error: {str(data['error'])[:300]}")
            choice = data["choices"][0]
            text = (choice["message"].get("content") or "").strip()
            if not text:
                raise RuntimeError(f"empty response (finish_reason={choice.get('finish_reason')})")
            return text
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


def split_figures(text, page):
    """Separate the transcription from the trailing JSON figure list."""
    matches = list(FIG_BLOCK_RE.finditer(text))
    if not matches:
        tqdm.write(f"page {page}: no figure list returned")
        return text.strip(), []
    m = matches[-1]
    body = (text[:m.start()] + text[m.end():]).strip()
    try:
        figs = loads_lenient(m.group(1)).get("figures", [])
    except (json.JSONDecodeError, AttributeError):
        tqdm.write(f"page {page}: figure list is not valid JSON")
        figs = []
    return body, figs if isinstance(figs, list) else []


def insert_figures(body, figs, page_png, page, figdir):
    """Crop each figure from the page render, save it, and link it in the Markdown.

    Also writes an overlay image with the boxes drawn on the page for review.
    """
    overlay_dir = figdir.parent / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(page_png) as img:
        width, height = img.size
        pad = 10
        overlay = img.convert("RGB").copy()
        draw = ImageDraw.Draw(overlay)
        for i, fig in enumerate(figs, 1):
            if not isinstance(fig, dict):
                continue
            fid = str(fig.get("id") or f"FIG{i}")
            desc = str(fig.get("description") or "Abbildung").strip().replace("]", "")
            try:
                ymin, xmin, ymax, xmax = [min(1000.0, max(0.0, float(v))) for v in fig["box_2d"]]
            except (KeyError, TypeError, ValueError):
                tqdm.write(f"page {page}: {fid} has no valid box_2d, skipped")
                continue
            if ymax <= ymin or xmax <= xmin:
                tqdm.write(f"page {page}: {fid} has an empty box, skipped")
                continue
            left = max(0, int(xmin / 1000 * width) - pad)
            top = max(0, int(ymin / 1000 * height) - pad)
            right = min(width, int(xmax / 1000 * width) + pad)
            bottom = min(height, int(ymax / 1000 * height) + pad)
            name = f"page_{page:04d}_fig{i}.png"
            img.crop((left, top, right, bottom)).save(figdir / name)
            draw.rectangle([left, top, right, bottom], outline=(255, 0, 0), width=4)
            draw.text((left + 6, top + 4), f"{i}:{fid}", fill=(255, 0, 0))
            link = f"![{desc}](../figures/{name})"
            placeholder = f"[[{fid}]]"
            if placeholder in body:
                body = body.replace(placeholder, link)
            else:
                body += f"\n\n{link}"
        overlay.save(overlay_dir / f"page_{page:04d}.png")
    return body


def main():
    ap = argparse.ArgumentParser(description="OCR a PDF with a vision LLM (OpenRouter).")
    ap.add_argument("--pdf", default=str(HERE / "ocrtest.pdf"))
    ap.add_argument("--out", default=str(HERE / "ocrtest_ocr"))
    ap.add_argument("--pages", default="", help="e.g. '1-3' or '1,4,7' (default: all)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4, help="parallel API requests")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--reasoning", default="", choices=["", "minimal", "low", "medium", "high"],
                    help="reasoning effort ('' = provider default; 'low' is much cheaper)")
    ap.add_argument("--force", action="store_true", help="redo pages that already have output")
    args = ap.parse_args()

    load_env_file(HERE / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set (export it, or put it in .env next to this script)")

    out = Path(args.out)
    
    pages_dir = out / "pages"
    md_dir = out / "md"
    figdir = out / "figures"
    pages_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    figdir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(args.pdf)
    selected = parse_pages(args.pages, doc.page_count)
    todo = [p for p in selected if args.force or not (md_dir / f"page_{p:04d}.md").exists()]
    print(f"{len(selected)} pages selected, {len(selected) - len(todo)} already done, "
          f"{len(todo)} to run with {args.model}")

    for p in tqdm(todo, desc="Render", unit="page"):
        png_path = pages_dir / f"page_{p:04d}.png"
        if args.force or not png_path.exists():
            doc[p - 1].get_pixmap(dpi=args.dpi).save(png_path)

    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def process(p):
        png = pages_dir / f"page_{p:04d}.png"
        raw = call_openrouter(api_key, args.model, png.read_bytes(), args.retries, args.reasoning)
        (raw_dir / f"page_{p:04d}.txt").write_text(raw + "\n", encoding="utf-8")
        body, figs = split_figures(raw, p)
        return split_inline_display_math(insert_figures(body, figs, png, p, figdir))

    failed = []
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, p): p for p in todo}
            with tqdm(total=len(todo), desc="OCR", unit="page") as bar:
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        text = fut.result()
                        (md_dir / f"page_{p:04d}.md").write_text(text + "\n", encoding="utf-8")
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
    (out / "document.md").write_text(combined + "\n", encoding="utf-8")

    done = len(parts)
    print(f"Done: {done}/{doc.page_count} pages in {out / 'document.md'}")
    if failed:
        print(f"Failed pages (re-run the same command to retry): {sorted(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
