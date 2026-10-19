# Scorer prompt

The scorer is blind because of what it can reach: the kit holds only this row's questions, the rulings that apply to it, and the row's saved page text, and it sits outside the project folder. The kit's own `brief.md` has every instruction the scorer needs (searching the pages, the answer states, evidence, `unsure`, the exact shape of `answers.json`). This file is what you put in the prompt, and what you must leave out.

## Start the scorer

One fresh scorer per row: `python3 "<lib>/hb.py" score --kit KIT`, KIT being the path `kit` printed. It starts a clean headless Claude session on the account's model, with none of the owner's connectors, plugins, skills or hooks and only the tools a scorer needs, gives it the prompt below, and saves the session's receipt (the tokens it processed, cache reads included) as `KIT/receipt.json`. Measured on three Pecking Order rows, a scorer started from inside the owner's session carried 73,000 tokens of that session's own preamble into every one of its 25 to 30 turns; the clean session starts at 31,000.

### If hb score cannot run

When `score` says the claude command line is not installed, start the scorer with the host's sub-agent tool instead (`host/claude.md` says how; give it no web tools if the host allows that). The prompt, filled in, with nothing added:

> Score one row of a benchmark. Your folder is KIT. Read KIT/brief.md and follow it exactly: work only inside KIT, answer every question it lists, and write KIT/answers.json. Do not use the web.
> Token cap: 200,000. If you get near it, write the answers you have to KIT/answers.json and stop.
> Reply in one line: how many questions you answered, how many have no evidence, and how many you marked unsure.

For a rescore after a second search or a rule decision, the prompt is the same: the kit already holds only the questions to redo.


## Never in the prompt

- The project folder's path, or any path inside it.
- Last edition's results, scores, ranks or bands; notes about last year; last year's source list.
- Another row's answers, what other rows in the group answered, the comparison, the report, the viewer link.
- This row's earlier answers, when scoring cells again.
- Any request for a confidence figure.

## Afterwards

- The brief has the scorer run the kit's `check_quotes.py` on its answers before it finishes, so a quote that is not word for word on a page is fixed while the page is still to hand. Its output is the scorer's business; nothing of it goes to the owner.
- If the scorer stopped at its budget or turn cap (`score` says so, with the questions left), ingest what it wrote and go on to the next row. The row waits on the page for the owner to carry on, upload a document or leave it, as the run skill says. The budget comes from the kit's `limits.json`; nothing about it goes in the prompt beyond the token cap line.
- A scorer that needed a document the kit does not have names it in `unsure.missing` and moves on (the brief tells it to search once, then stop). Ingest puts it on the row's missing list for the second search and the owner.
- If it says it could not read a page file or a document has no page files, that is a document problem, not a scoring one: the row goes through the second search like any other document doubt.
