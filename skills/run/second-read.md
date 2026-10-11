# Second read

Only for cells flagged "Group answered differently": rows of one group that cite a document in common gave different values for the same question. Past runs found these differences came from rule readings and documents, not misreads, so the point is to read the group's rows side by side under one reading and see whether the difference holds.

## Build the kits

Take one item from `second-reads --json` (one question Q, one group, its rows). For each row:

`python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RUN --questions Q --second`

Each kit gets `second/brief.md`, `second/questions.json` and the row's documents. Finish one item (read and record) before building kits for the next: building a kit empties that row's kit folder.

## Start the reader

A fresh agent on the session's model, never one of the rows' first scorers. The prompt, filled in, with nothing added:

> Give a second reading of question Q for N rows of a benchmark that belong to one group. Their folders:
> - KIT_1 (ROW_NAME_1)
> - KIT_2 (ROW_NAME_2)
> In each folder read second/brief.md and follow it, working only inside these folders, and write that folder's second/answers.json. Read the rows side by side: they share documents, so the same words in the same document should get the same answer unless the question's coding rule treats the rows differently. Where your answers differ between rows, say in each note what in the documents makes them differ. Do not use the web.
> Token cap: 60,000.
> Reply in one line per row: the value and the document it rests on.

Never include the first answers, which rows disagreed, the project folder's path, or anything `scorer.md` keeps from a scorer.

## After the read

Record each row with `second-reads --record` (SKILL.md, "Group answered differently"), then compare each `KIT/second/answers.json` with `answers/ROW.json`:

- **Settled:** every second read agrees with its first answer, and wherever the rows' values differ the reader's notes put it down to a document only one row has. Nothing more to do.
- **Survived:** a second read disagrees with a first answer, or rows citing the same passage still get different values. Raise one rule question for Q over the item's rows (`raise-rule --by run`, or `--by trial` in a trial): ask how the coding rule applies to that evidence, give the readings in play as the options, and recommend the one the rule's words best support. The cells keep their first answers and carry the flags until the owner decides; the decision then reruns them.
