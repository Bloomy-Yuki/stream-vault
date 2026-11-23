---
name: grade-input
description: Grade homework submissions from typed text, scanned pages, or PDFs in any subject. Use an optional assignment, rubric, or reference answer; when these are absent, assess the work on its merits and clearly state uncertainty. Produces constructive feedback and a score. Use for requests like "grade my homework", "check this assignment", "give feedback on my solution", or "run the homework grader".
---

# Grade homework

The skill accepts typed work and scanned/handwritten work. Image and PDF pages are
transcribed first; typed text is read directly. The grader can use an assignment
question, rubric, and/or reference answer. If none is supplied, it uses subject-
appropriate judgment and explains when the available context is not enough for a
reliable score.

Use `scripts/grade_homework.py`; do not reimplement the pipeline. The old
`scripts/sketch_feedback.py` name remains as a compatibility entry point.

## Inputs

Use one folder per attempt under a root folder:

```text
Homework/
  attempt-1/
    submission.md          (optional typed work; alternatively submission.txt)
    page1.png              (optional scans; PNG/JPG, naturally sorted)
    page2.jpg
    submission.pdf         (optional PDF; every page is rendered and transcribed)
    question.md            (optional assignment/question; alternatively question.txt)
    rubric.md              (optional grading criteria; alternatively rubric.txt)
    reference.md           (optional reference answer/solution)
    reference.txt          (optional: a reference file path, or reference text)
    transcription.md       (generated from scans; review/edit before grading)
    feedback.md            (generated feedback)
```

Typed and scanned submissions can be combined. When both are present, both are
included. If `transcription.md` already exists, it is reused instead of repeating
OCR; edit it to correct recognition errors. A `reference.txt` containing a path
is resolved relative to the attempt folder unless it is absolute. External
question/rubric/reference text files can also be passed with CLI options.

## Before running

1. Check `OPENROUTER_API_KEY` in the environment or
   `~/.config/grade-homework/.env`. The previous
   `~/.config/sketch-feedback/.env` location is also supported for compatibility.
   If no key is available, stop and ask the user. Never print or paste the key.
2. Confirm the selected files are the intended submission. Typed text and every
   image/PDF page are sent to OpenRouter. The request asks OpenRouter to route to
   providers marked as not collecting data, but this is not a guarantee; mention
   this for confidential material.
3. Check that any question, rubric, or reference belongs to this assignment.

## Run

```bash
uv run "<skill folder>/scripts/grade_homework.py" \
  --root "<vault>/Assignments/Homework" --attempt attempt-1
```

- Omit `--attempt` to process every attempt subfolder under `--root`.
- Optional `--question PATH`, `--rubric PATH`, and `--reference PATH` provide
  external Markdown/plain-text context and take precedence over files in the
  attempt folder.
- `--subject "Biology"` can clarify the discipline.
- `--text-model` and `--grade-model` override the model defaults shown by `--help`.
- `--reasoning high` may be slower and more expensive; use it only if requested.
- Existing `feedback.md` is preserved unless `--force` is used. `--force` also
  repeats OCR and overwrites `transcription.md`, so it discards transcription
  corrections.

## Workflow

1. Run the grader. For scanned work, review `transcription.md` before relying on
   `feedback.md`.
2. Correct any transcription errors, then delete `feedback.md` and rerun without
   `--force` to grade the corrected text.
3. With typed-only submissions, edit the source `submission.md` or
   `submission.txt`, delete `feedback.md`, and rerun.
4. Do not put feedback into the original homework file unless the user asks.

## Feedback and limitations

Feedback includes a score out of 10, correct/effective parts, issues and
corrections, a suggested answer where useful, and grading notes. It accepts
equivalent approaches and does not assume proof-style work or a particular
subject. Without a question, rubric, or reference answer, correctness may be
uncertain; the grader is instructed to say so rather than invent context. OCR
can also misread content, so always check the transcription for scanned work.
