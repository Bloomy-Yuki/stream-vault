#!/usr/bin/env python3
"""Improve the readability of the OCR Markdown with a text LLM via OpenRouter.

Reads the per-page Markdown from <out>/md/, asks a text model to apply Markdown
formatting only, and checks that no content was lost or changed. Pages that fail
the check keep their original text. Writes <out>/formatted/page_NNNN.md and
<out>/document_formatted.md.

Default model: openai/gpt-6-luna. API key: same as ocr_gemini.py.

Privacy: each page's Markdown text (not the page image) is sent to OpenRouter and
then to the provider that serves the model. Requests set provider.data_collection
to "deny", so OpenRouter only routes to providers that do not store or train on
the data. This is a routing filter based on OpenRouter's data-policy information,
not a guarantee. The provider that served each page is recorded in
<out>/formatted/providers.json so the routing can be audited.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ocr_gemini import API_URL, HERE, load_env_file, parse_pages

DEFAULT_MODEL = "openai/gpt-6-luna"

FORMAT_PROMPT = """You improve the readability of a Markdown transcription of handwritten German physics lecture notes (quantum mechanics). The input is the OCR output for one page. Your only job is to apply Markdown formatting.

Hard rules:
1. Do not add, remove, translate, reword, or correct any word, number, or symbol. Do not add titles, labels, comments, or explanations that are not in the input.
2. Formulas are already replaced by placeholder tokens of the form @@M0@@, @@M1@@, and so on. Keep every token exactly as written, once each, in the same order. Never edit, remove, duplicate, or move a token across a line other than to place it on its own line. Do not write any $ symbol yourself.
3. Keep every image link ![...](...) exactly as written. Keep any "<!-- page N -->" comment exactly as written.
4. Keep circled numbers such as ①, ②, ③ exactly as they are. Do not replace them with plain digits.
5. Output only the formatted Markdown. No code fence around the whole answer, no commentary.

Make the page easy to scan. Lecture notes usually have a title, topic sections, key results, definitions, and remarks, so use headings, bold, and blockquotes for those. Work through the full Markdown toolbox below and apply every element whose content is present on the page; skip only elements the page has no content for. Do not use an element where the content has no such part:
- Headings (#, ##, ###, ####) for the page title, sections and subsections, with a consistent hierarchy and at most one # per page.
- Bold (**...**) for defined terms and key results. Italics (*...*) for emphasis and for foreign words or symbols being introduced. ***Bold italics*** only for the single most important statement on the page.
- Strikethrough (~~...~~) only for text the input already marks as crossed out.
- Bullet lists (-) with correct indentation for nested items; use * or + only to distinguish deeper nesting levels.
- Numbered lists (1., 2., ...) in the input's own order, keeping the input's start number when it does not begin at 1.
- Task lists (- [ ] and - [x]) only when the input already enumerates steps or items with ticks or crosses.
- Blockquotes (>) for remarks and Merksätze; nest them (> >) when a remark sits inside another quoted block.
- GitHub alerts (> [!NOTE], > [!IMPORTANT], > [!WARNING], > [!TIP]) for the main result of a section, for warnings, and for remarks. Use an alert for the main result or a warning only when the content is clearly one of these.
- Keep a short label that is already in the text (for example "Beachte:" or "Einstein:") bold, and do not add new labels.
- Tables (pipe syntax) only for content that is clearly tabular, with a header row and alignment markers (:---, :---:, ---:) when the input suggests alignment.
- Horizontal rules (---) between major sections.
- Inline code (`...`) only for literal identifiers or file names. Never for math.
- Meaningful line breaks: keep line breaks that carry meaning (separate handwriting lines, enumerations, a verse-like layout). Join only line breaks that split one sentence or one list item.
- Blank lines between blocks, and none inside a list item's continuation (indent the continuation instead), so the Markdown renders cleanly.

Fix only Markdown structure. Do not invent structure that the input does not have."""

MATH_RE = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)
IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
WORD_RE = re.compile(r"[^\W_]+")


def strip_fence(text):
    """Remove a code fence the model may have wrapped around the whole answer."""
    text = text.strip()
    m = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", text, re.S)
    return m.group(1).strip() if m else text


def mask_math(text):
    """Replace each formula with a placeholder token the model is told to keep unchanged."""
    spans = []

    def repl(m):
        spans.append(m.group(0))
        return f"@@M{len(spans) - 1}@@"

    return MATH_RE.sub(repl, text), spans


def unmask_math(text, spans):
    """Put the original formulas back. Returns None if any token is missing or duplicated."""
    for i in range(len(spans)):
        if text.count(f"@@M{i}@@") != 1:
            return None
    for i, expr in enumerate(spans):
        text = text.replace(f"@@M{i}@@", expr)
    return text


def math_key(expr):
    return re.sub(r"\s+", "", expr).strip("$")


def check(original, formatted, tolerance):
    """Return a list of problems, empty if the formatted text keeps the same content."""
    problems = []
    # Compare the formula content only: whitespace and $ vs $$ delimiters may change.
    orig_math = Counter(math_key(m) for m in MATH_RE.findall(original))
    new_math = Counter(math_key(m) for m in MATH_RE.findall(formatted))
    if orig_math != new_math:
        problems.append("math changed")
    if Counter(IMG_RE.findall(original)) != Counter(IMG_RE.findall(formatted)):
        problems.append("image links changed")
    orig_words = Counter(w.lower() for w in WORD_RE.findall(original))
    new_words = Counter(w.lower() for w in WORD_RE.findall(formatted))
    total = sum(orig_words.values()) or 1
    diff = sum(((orig_words - new_words) + (new_words - orig_words)).values())
    if diff / total > tolerance:
        problems.append(f"words changed ({diff} of {total} tokens differ)")
    return problems


def call_text(api_key, model, text, retries):
    """Return (answer, serving_provider, usage)."""
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 16000,
        # Same privacy routing as ocr_gemini.py.
        "provider": {"data_collection": "deny"},
        "reasoning": {"enabled": False},  # formatting needs no reasoning; this cuts latency
        "messages": [
            {"role": "system", "content": FORMAT_PROMPT},
            {"role": "user", "content": text},
        ],
    }
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
            out = (choice["message"].get("content") or "").strip()
            if not out:
                raise RuntimeError(f"empty response (finish_reason={choice.get('finish_reason')})")
            return out, data.get("provider") or "unknown", data.get("usage") or {}
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


def format_text(api_key, model, original, retries, tolerance):
    """Format one page. Returns (text, problems, provider, usages).

    text is None when no acceptable result was produced (the caller keeps the OCR text).
    Retries when the model loses a formula placeholder or changes the content, because
    both failures are sporadic rather than systematic.
    """
    masked, spans = mask_math(original)
    provider, usages = None, []
    problems = ["formula placeholder lost"]
    for _ in range(3):
        answer, provider, usage = call_text(api_key, model, masked, retries)
        usages.append(usage)
        candidate = unmask_math(strip_fence(answer), spans)
        if candidate is None:
            problems = ["formula placeholder lost"]
            continue
        candidate += "\n"
        problems = check(original, candidate, tolerance)
        if not problems:
            return candidate, [], provider, usages
    return None, problems, provider, usages


def main():
    ap = argparse.ArgumentParser(description="Improve the Markdown formatting of OCR output.")
    ap.add_argument("--out", default=str(HERE / "ocrtest_ocr"),
                    help="OCR output folder that contains md/ (default: ocrtest_ocr)")
    ap.add_argument("--pages", default="", help="e.g. '1-3' or '1,4,7' (default: all)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=4, help="parallel API requests")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--tolerance", type=float, default=0.02,
                    help="allowed share of changed word tokens before a page is rejected")
    ap.add_argument("--force", action="store_true", help="redo pages that already have output")
    args = ap.parse_args()

    load_env_file(HERE / ".env")
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set (export it, or put it in .env next to this script)")

    out = Path(args.out)
    md_dir = out / "md"
    fmt_dir = out / "formatted"
    fmt_dir.mkdir(parents=True, exist_ok=True)
    print("Privacy: page text is sent to OpenRouter and the serving provider with "
          "data_collection=deny. Served-by log: formatted/providers.json")

    page_files = sorted(md_dir.glob("page_*.md"))
    if not page_files:
        sys.exit(f"No page_*.md files in {md_dir}. Run ocr_gemini.py first.")
    all_pages = [int(f.stem.split("_")[1]) for f in page_files]
    selected = parse_pages(args.pages, max(all_pages))
    selected = [p for p in selected if p in all_pages]
    todo = [p for p in selected if args.force or not (fmt_dir / f"page_{p:04d}.md").exists()]
    print(f"{len(selected)} pages selected, {len(selected) - len(todo)} already done, "
          f"{len(todo)} to run with {args.model}")

    rejected = []
    providers = {}

    def process(p):
        original = (md_dir / f"page_{p:04d}.md").read_text(encoding="utf-8")
        if not WORD_RE.search(re.sub(r"<!--.*?-->", "", original)):
            return original, [], None  # blank page: nothing to format
        text, problems, provider, _ = format_text(
            api_key, args.model, original, args.retries, args.tolerance
        )
        return (original if text is None else text), problems, provider

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, p): p for p in todo}
            with tqdm(total=len(todo), desc="Format", unit="page") as bar:
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        text, problems, provider = fut.result()
                        providers[p] = provider
                        if problems:
                            rejected.append(p)
                            tqdm.write(f"page {p}: rejected, original kept ({'; '.join(problems)})")
                        else:
                            (fmt_dir / f"page_{p:04d}.md").write_text(text, encoding="utf-8")
                    except Exception as e:
                        rejected.append(p)
                        tqdm.write(f"page {p} FAILED: {e}")
                    bar.update(1)

    if providers:
        log = fmt_dir / "providers.json"
        old = json.loads(log.read_text(encoding="utf-8")) if log.exists() else {}
        old.update({f"page_{p:04d}": v for p, v in sorted(providers.items())})
        log.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")

    parts = []
    for p in all_pages:
        f = fmt_dir / f"page_{p:04d}.md"
        src = f if f.exists() else md_dir / f"page_{p:04d}.md"
        text = src.read_text(encoding="utf-8").strip()
        parts.append(f"<!-- page {p} -->\n\n{text}")
    combined = "\n\n".join(parts).replace("](../figures/", "](figures/")
    (out / "document_formatted.md").write_text(combined + "\n", encoding="utf-8")

    print(f"Done: {out / 'document_formatted.md'}")
    if rejected:
        print(f"Pages with original text (check failed or error): {sorted(rejected)}. "
              "Re-run the same command to retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
