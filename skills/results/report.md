# Writing the report

The report is about a page, 350 to 500 words, saved as `report/report.md`. The owner reads it in the viewer's Results tab, and may share it. It says what the numbers are. It does not explain causes the files do not show, and it gives no advice.

## What to read

- `report/facts.json`, once, in full. Every figure comes from here.
- `project.json` for the name, edition, cut-off, `unit_word_plural` and the label names. Use the owner's words for things: their word for a unit, their labels, their question short names. Avoid "row", "cell" and "label" in the prose.
- `scoresheet.json` `plain`, for one sentence on how scores are built.
- `rules.json`, for the words of any open or skipped rule.

## Sections, in this order

Start with a title line, `# <name> <edition>`, then these `##` headings.

1. **What was assessed.** How many units, what they are grouped by, how many questions, the cut-off, and one sentence on how the score sheet turns answers into scores.
2. **Coverage.** How many units were scored of how many expected, and the answers by state: answered, no evidence found, not applicable, not yet run. If anything is missing, the first sentence says so.
3. **Results.** Units in each band and the mean overall score. Then the means for each value of the grouping label, and for any other label the facts hold. Then up to three patterns, each one sentence with its numbers. A pattern is a gap between groups, a question nearly everyone met or nearly everyone missed, or a band holding most units. Take them from the facts. Do not search the answers for more.
4. **Compared with last edition.** Only when the facts have a comparison. Say what level it was made at, how many agree for each key (N of M), and how many disagreements fall under each reason. Say that the Comparison view labels each reason as "from the files" or as Claude's reading. Do not guess why last year differed.
5. **Open rules and missing data.** Always include it, even when it is empty ("No open rules."). Cover open and skipped rules (how many, and what each one asks, in words), how many answers carry the flag for Claude's own judgement, units with no documents, documents expected but not found, data the owner was asked for and has not supplied, and the counts of the other flags in plain words: quote not found in the document, document after the cut-off, two reads disagree, group answered differently.

Use only `##` headings, paragraphs and at most one short bullet list. No tables, links, images, bold or HTML.

## Numbers

- Write each number exactly as it appears in `facts.json`: same digits, same decimal places.
- Never work one out. No differences, shares, ratios, totals or "points ahead". If you want the contrast, give both figures.
- "N of M": both numbers must be in the facts.
- Name a unit only when the facts give its figure beside it.
- Write counts as digits so the check can see them. The check lets the words zero to ten through unseen, so keep words for loose uses such as "one of the questions". A count in words must still equal the facts.
- Dates and years pass the check. A day or month number may not. If the check objects to one, write "July 2025" or reword.

## Style

Plain UK English. Short sentences, active voice, concrete nouns. Past tense for what was done, present for what the results show.

No em dashes. Use a full stop, a comma, a colon or brackets.

Leave out:
- Inflated or promotional words: significant (nothing was tested), dramatic, stark, remarkable, strong, impressive, key, crucial, pivotal, landscape, delve, underscore, highlight, showcase, foster, notably, robust, comprehensive.
- "Not only X but Y" and "it is not X, it is Y".
- A tacked-on clause that tells the reader what a number means: ", reflecting a wider trend", ", highlighting the need for".
- Lists of three made to sound complete, rhetorical questions, and openers or closers such as "Overall", "In summary" or "This section".
- Vague sources: "experts say", "it is widely seen".
- Advice, forecasts, praise or blame of any unit, and any word about confidence.
- Hedges stacked on hedges. If the files do not show it, do not say it.

Say "higher", "lower", "most" or "few" with the number beside it.

## Example of the register

Invented names and numbers. Never copy a figure from here.

> ## Coverage
> 40 of 42 companies were scored. Two have no documents yet, so their 12 answers are not run. Of 480 scored answers, 431 had evidence and 49 found none.

## Before you run the check

1. Every sentence traces to the facts, `project.json`, `scoresheet.json` or `rules.json`.
2. No dash used as a break in a sentence. This prints `0 dashes` when the report is clean:
   `python3 -c "import sys; t=open(sys.argv[1], encoding='utf-8').read(); print(t.count(chr(0x2014)) + t.count(chr(0x2013)), 'dashes')" "<project>/report/report.md"`
3. None of the words or constructions above.

Then run `check-report`. If it fails, each number it lists is either a typo, a figure you worked out, or a figure the facts do not hold. Replace it with the facts figure, or reword the sentence without it. Never edit `facts.json`. Run it again until it passes.
