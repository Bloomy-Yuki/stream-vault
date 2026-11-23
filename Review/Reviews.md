
## Intro
---
the vault follows the Axolotl Standard, defined below:
> **The Axolotl Standard**
> the file is divided into sections, each section starts with "##" and is a markdown heading. these section contain cards, each card has to start as an element in a numbered list and ends with a colon, **example "2. Gravitation Law:"**.
> 
> and finally to the content of a card. That can be anything as long as:
>
> 1. it does not end with a colon OR
> 2. and it does not have a period in the first 5 places of the line OR 
> 3. has an "\\\\" at the end of the line 
> 
> With those rules, any content will be put in the Anki card containing the face as the title as the numbered list element and the back is the content.

The Agentic skill contained in this vault can be used to invoke a **python script** that converts markdown files written in the axolotl standard to **Anki-readable csv** files.

The files for each Anki deck (or here a .csv file) are stored at their perspective folders (as .md) and then the python script can be ran on them to obtain the converted (.csv) files.

