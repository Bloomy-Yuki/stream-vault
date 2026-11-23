---
name: anki-flashcards
description: Convert an Axolotl-style Markdown flashcard file (numbered cards ending in a colon, "##" section headings) into an Anki CSV with Tags, Front and Back columns. Use for requests like "convert <module> to Anki", "make Anki decks", or "make an Anki CSV from this flashcard file".
---

# Markdown flashcards to Anki CSV

`scripts/md_to_anki.py` converts the vault's Axolotl-standard module files (see `Review/Reviews.md`).
It needs no pandas, asks no questions, and writes only the CSV.

## Source format (Axolotl standard)

- `## Heading` sets the tag for the cards below it.
- A card is a numbered line ending in a colon: `2. Gravitation law:`.
- Its content is every following line up to the next card, without `##` lines.
  Math stays as Markdown; `$...$` and `$$...$$` are converted to `\(...\)` and `\[...\]`.

## Run

```bash
python3 "<skill folder>/scripts/md_to_anki.py" "<vault>/Modules/Mymodules/<Module>/<Module>.md" --deck "<Deck name>"
```

- Source files live in their module folder, `Modules/Mymodules/<Module>/`.
- Output: `Anki<Deck name>.csv` in the same folder as the source file. Use `--out DIR` to choose another folder.
- Renumbering is applied in memory. The source `.md` is not changed.
- Scope: only the module file the user names.

## Verify

- The script prints `N cards -> <path>`. If the number of numbered cards in the source
  differs from the parsed cards, it prints a `WARNING` on stderr. Report the mismatch.
- Spot-check one card with math and one card with a `##` heading.

## Import into Anki

- Field separator: comma. Do not tick "first line is header" (the CSV has no header row).
- Map column 1 to Tags, column 2 to Front, column 3 to Back.
