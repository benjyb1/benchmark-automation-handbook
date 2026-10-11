> **Not used since 11 Oct 2026.** Step briefs are no longer shown in the page: the few things that need the owner show on the chat button's badge, and missing documents on the Grid. `hb deck` and this file stay only for the code that still reads them; do not write briefs.

# Writing the step brief

At the end of every step (set-up, documents, scores, results) you leave the owner a short brief at the top of that step in the viewer: the Set-up tab, the Documents tab, the Grid, the Results tab. It is how you show them your process, so they can follow and correct it. Three parts, the same whatever the size of the benchmark:

1. **How I did it.** Three or four short lines on what you did and how, with the step's real figures.
2. **What I decided for you.** Every choice you made that they can change, four at most: the step's own choices (set-up: the results you held back, the cut-off, where the row list came from; documents: documents you marked weak, copies from other sites, one document used for several rows; scores: answers resting on your own reading and no rule), then the open rules, whose recommended reading is in use until they decide. Each rule has a Change control; the rest wait in the Rules tab.
3. **Needs your eye.** Up to three patterns, each one line, with a link that opens exactly those cases and, where it helps, a Send button for a file or a link.

Code chooses every item and figure from the files. You write only the sentences. There is no title slide and no intro: get straight to the information.

## Steps

1. `python3 "<lib>/hb.py" deck --project "<project>" --step STEP` (STEP is `setup`, `documents`, `scores` or `results`). It writes `deck/STEP.json` and lists each sentence to write with the figures it may use. Pass `--again` only to choose the items afresh, for example after a rerun.
2. Write the sentences into a small JSON file, `{where: sentence}`, using the `where` names it listed (`how.lines[0]`, `decided.R17.line`, `eye.not_kept.line`), and run `hb deck --project "<project>" --step STEP --fill <that file>`. (Writing them into `deck/STEP.json` in place also works; change nothing else in it.)
3. `python3 "<lib>/hb.py" check-deck --project "<project>" --step STEP`. Fix what it lists and run it again until it passes. It marks the brief checked; a brief edited after that is left out of the page until it passes again.
4. Build and publish. Tell the owner in one line that your note is at the top of that tab, and name the one or two choices with most at stake in chat too, for owners who answer there.

## The sentences

- `how.lines[0..3]`: what you did and how, first person, plainly. For documents: where you looked, how many you found and kept, what you did when a page was gone, which are weak. Figures only from the brief's `numbers`. The fourth line is optional: write `""` to leave it out.
- `decided.<RULE>.line`: the rule's question and the reading in use, in one sentence the owner can follow at a glance ("Documents dated after 5 July 2025 don't count. I list them for you to check instead."). The page shows the rule id, whether it is your recommendation or their decision, and the points at stake beside it; don't repeat those.
- `decided.<choice>.line`: what you chose and why it matters, in one sentence ("I've kept your published 2025 scores away from the scorers. I only use them to compare at the end.").
- `eye.<pattern>.line`: what the pattern is, in the owner's words, and where to start if one case stands out ("KFC and others came out differently in some countries from the rest of their group."). The page shows the count, the points at stake and the "Can you help?" ask chosen by code; don't repeat them. If you can see another way they could unblock it (a contact at the company, a login they have, last year's document), say it here.

## Rules

- Name only the cases an item is about. Every number must be in the brief or in the project's facts. `check-deck` enforces both; numbers from 0 to 10 and years need nothing behind them.
- One sentence per item, two at most. No counts of flags as a headline ("75 quote-not-found flags"): say what happened in words.
- Use the owner's words for things (their word for a unit, their labels, question short names). Avoid "row", "cell" and "flag".
- No advice, no causes the files do not show, no confidence words.
- UK English. Short sentences.
