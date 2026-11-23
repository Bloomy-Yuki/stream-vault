#!/usr/bin/env python3
"""Convert an Axolotl-style Markdown flashcard file into an Anki CSV.

Usage: md_to_anki.py INPUT.md --deck NAME [--out DIR]

Axolotl standard: "##" headings set the tag of the cards below them. A card is a
numbered line ending in a colon ("2. Gravitation law:"); its content is every line
up to the next card, without "##" lines. Output columns: Tags, Front, Back.
No header row is written: map column 1 to Tags, 2 to Front, 3 to Back on import.
"""
import argparse
import csv
import os
import re
import sys

#checks validity of line numbers
def renumber(lines):
    #makes lines
    out = []
    index = 1

    for line in lines:
        stripped = line.strip()
        #check for the correct lines, beginning with "number. " and ending with ":"
        if stripped.endswith(":") and re.match(r"^\d+\.", stripped):
            #removes number and puts rest in content
            content = re.sub(r"^\d+\.\s*", "", line)
            #appends correct number to content
            out.append(f"{index}. {content}")
            #nextline
            index += 1
        else:
            out.append(line)
    return out


#checks if a line is a title
def is_title(line):
    return "." in line[:4] and ":" in line[-2:] and "\\" not in line[-2:]


#replaces LaTeX with Mathjax
def replace_double_money(lines):
    #even or odd
    n = 0

    for idx in range(len(lines)):
        for i in range(len(lines[idx]) - 1):
            if lines[idx][i:i + 2] == "$$":
                #even takes \[
                if n == 0:
                    lines[idx] = lines[idx].replace("$", "\\", 1).replace("$", "[", 1)
                    n = 1
                #odd takes \]
                else:
                    lines[idx] = lines[idx].replace("$", "\\", 1).replace("$", "]", 1)
                    n = 0

#replaces LaTeX with Mathjax
def replace_single_money(lines):
    for idx in range(len(lines)):
        for ch in lines[idx]:
            if ch == "$":
                lines[idx] = lines[idx].replace("$", "\\(", 1).replace("$", "\\)", 1)

#fill in cards, tags, and titles
def extract_cards(lines):
    tag = "none"
    starts = []
    tags = []
    for idx, line in enumerate(lines):
        #fills tags and titles

        if "##" in line:
            tag = line[2:].strip()
        if is_title(line):
            starts.append(idx)
            tags.append(tag)

    cards = []
    for k, start in enumerate(starts):
        #end index is the start of the next card
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        #take everything in between except the tags
        body = [line for line in lines[start + 1:end] if "##" not in line]
        #apend to cards
        cards.append((tags[k], lines[start], "\n".join(body).strip("\n")))
    return cards


def main():
    parser = argparse.ArgumentParser(description="Axolotl Markdown flashcards -> Anki CSV")
    parser.add_argument("input", help="source .md file")
    parser.add_argument("--deck", required=True, help="deck name. output is Anki<deck>.csv")
    parser.add_argument("--out", help="output directory (default: folder of the input file)")
    args = parser.parse_args()

    #opens the file
    with open(args.input, encoding="utf-8") as f:
        lines = [line.rstrip() for line in f]

    #renumbers the lines
    lines = renumber(lines)
    #for trouble shooting
    numbered = sum(
        1 for line in lines
        if line.strip().endswith(":") and re.match(r"^\d+\.", line.strip())
    )

    replace_double_money(lines)
    replace_single_money(lines)
    cards = extract_cards(lines)

    if len(cards) != numbered:
        print(f"WARNING: {numbered} numbered cards in source, {len(cards)} parsed", file=sys.stderr)

    #make dir for output
    out_dir = args.out or os.path.dirname(os.path.abspath(args.input))
    os.makedirs(out_dir, exist_ok=True)

    path = os.path.join(out_dir, f"Anki{args.deck}.csv")

    #write .csv
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for tag, front, back in cards:
            writer.writerow([tag, front, back])
    print(f"{len(cards)} cards -> {path}")


if __name__ == "__main__":
    main()
