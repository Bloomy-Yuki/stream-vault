# AGENTS.md

This is an Obsidian vault (`.obsidian/`, see `Main.md`) for course notes plus a `skills/` folder of Python/shell helper scripts.

## Layout

- `skills/<name>/SKILL.md` is the source of truth for each skill. Edit files there.
- `.opencode/skills/`, `.claude/skills/`, and `.agents/skills/` contain relative symlinks to `skills/<name>`. Do not edit through them. A new skill needs a symlink in each of the three folders.
- `Modules/Mymodules/<Module>/` holds course notes and PDFs. `Assignments/Homework/<attempt>/` holds homework attempts. `Review/Reviews.md` defines the Axolotl flashcard format.
- `Files/` is the default OCR output folder.

## Commands

- Only test suite: `python3 -m unittest discover -s skills/grade-input/tests`. It passes as of this writing.
- Grader help: `uv run skills/grade-input/scripts/grade_homework.py --help` (uses inline dependencies; needs `pymupdf`).
- OCR: `sh skills/ocr-handwritten-pdf/scripts/run.sh ocr --pdf <file> --out <dir>` and `... format --out <dir>`. Uses `uv` (Python 3.12) if available, otherwise `python3` with `requirements.txt`.
- Anki: `python3 skills/anki-flashcards/scripts/md_to_anki.py <file.md> --deck "<name>"`. No dependencies.
- The OCR and format scripts only have `py_compile` checks; no test suite.

## Secrets and privacy

- Grading and OCR send typed text and page images to OpenRouter. Confirm the material is safe to send before running.
- `OPENROUTER_API_KEY` comes from the environment or `~/.config/grade-homework/.env` (grader) or `~/.config/ocr-notes/.env` (OCR). Never print it or store it in the vault.
- Models are set in `grade_homework.py` (`TEXT_MODEL`, `GRADE_MODEL`) and in `ocr-handwritten-pdf/SKILL.md`. The OCR model IDs (`openai/gpt-6-luna`, `google/gemini-3.8-flash`) were not verified against OpenRouter.

## Behaviour gotchas

- Grader: an existing `feedback.md` is kept unless `--force`. `--force` also re-runs OCR and overwrites `transcription.md`, discarding manual fixes. Check transcriptions before grading scanned work.
- Do not write feedback into the original homework file unless the user asks.
- OCR output must stay inside the vault, next to `figures/`, so relative image links resolve. Never delete `figures/`.
- Anki CSV is written next to the source `.md`. The source is not modified.
- Don't edit user course notes or generated transcriptions unless asked.

## Git

- `.gitignore` excludes `.obsidian/workspace*.json`, plugin `data.json` files, `Files/`, `Assignments/MyAssignments/`, `Modules/Mymodules/`, and PDFs/EPUBs under `Modules/`. Course notes under `Modules/Mymodules/` are not in the repo; the `My` rule matches case-insensitively.
