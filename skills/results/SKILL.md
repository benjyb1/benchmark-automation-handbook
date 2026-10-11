---
name: results
description: Use when scoring has finished and the owner wants the report or the results, wants last edition compared with this one, asks why scores differ from last year's, or when a rerun or a rule decision has changed answers and the report is out of date.
---

# Results

Turn scored answers into a one-page report and, when last edition's results were given, a comparison with a reason for every disagreement. Then publish. This skill explains scores. It never changes one.

## Setting up

- `<lib>` is the `lib` folder inside the plugin folder (two levels above this skill's folder). `<project>` is the project folder agreed with the owner.
- Every command is `python3 "<lib>/hb.py" <command> --project "<project>"`.
- Publishing and pending viewer edits are done as `host/claude.md` says. It sits in the `host` folder two levels above this skill's base directory.

## Rules that hold throughout

- Every number in the report comes from `report/facts.json`. A script writes that file. Never edit it.
- Claude's prose appears in three places only: the report, open-rule questions, and reasons you assign to disagreements. No health summaries, no per-cell confidence, no commentary anywhere else.
- Never change an answer, a score, a question, a rule or anything in the held-back folder. Last edition's values are for the owner only: never copy them into a rule, a question or a kit.
- Say the main results to the owner in chat. Do not point them at the report and stop.

## Steps

1. **Check the state.** Run `status`. If cells are still not run, or the owner has just decided a rule that changes answers, finish that with the run skill first. Report on partial results only if the owner asks, and say so in the report's coverage section. If `stage` is `trial`, the report covers the trial slice only and says so.
2. **Refresh the flags.** Run `check`, so the flag counts in the facts are current.
3. **Compare, if last edition's results were given.** They are there when the folder `<slug>.held-back` sits beside the project folder and `status` shows `last_results` as `draft` or `confirmed`. If not, skip this step and write no comparison.
   - Run `compare`. It writes `compare/compare.json` and gives every disagreement a reason it can prove from the files. The rest are `unexplained`.
   - Before writing reasons, deal with the big moves as "Big moves" below says.
   - Fill every `unexplained` one as `reasons.md` says, then run `validate` and fix every problem it lists.
4. **Write the facts.** Run `facts`. Read `report/facts.json` once, in full.
5. **Write the report** to `report/report.md` as `report.md` says.
6. **Check it.** Run `check-report`. It lists every number in the report that is not in the facts. Fix each, run it again, and repeat until it passes. Do not go on before it passes.
7. **Write the results brief** as `../run/deck.md` says (`--step results`): it sits at the top of the Results tab, with the rules behind the biggest moves and the big moves since last edition as what needs their eye. Then build and publish: run `build`, then publish as `host/claude.md` says.
8. **Tell the owner in chat.** Coverage first, then the main results in two or three plain sentences using numbers from the facts, then what is open: rules waiting for a decision, missing data, disagreements left unexplained. Say where to look: the Results tab, and the Comparison view when there is one.

## Big moves

`compare` lists in `big_moves` the disagreements with the latest past edition that the files leave unexplained (or put down to a missing document) and that are large. Each has a `why` (`big_change`, `nothing_found` or `band_jump`), a `trend` (`breaks` when this run falls outside the row's range over every past edition, `within`, or `too_short` with fewer than two past editions) and a plain `text`. The viewer shows them first, as "Check these first". Tell the owner how many there are, those that break the trend first.

For each big move marked `nothing_found` ("scored well last time, nothing found this time"), offer the owner one second document search for that row before you write reasons. If they agree and the row's `second_search` in `found/<row>.json` is not done, start a rerun for it (`run start --kind rerun --rows ROW --questions Q,...`) and run the search as the run skill's "Document doubts get one second search per row" says, then `run finish`. Tell the finder only which questions had no evidence: never last edition's figures, and never that the row scored well before. Score the affected cells again, run `check` and `compare` again, then write the reasons. A row whose second search is done gets no other; say so.

## Running it again

After a rerun or a rule decision, start from step 1. `compare` keeps a reason you set when the two values behind it are unchanged and clears it when they changed, so read `compare/compare.json` again and fill only what is `unexplained`. The facts and the report are rewritten every time. Never reuse a number from an earlier report.

## If the owner asks for the evidence for a company, a country or a question

For example "pull out everything for Acme Denmark" or "the evidence for 2.1 in every country".

1. Choose the rows by id (`--rows dk-acme,se-acme`), by a label value (`--label country=Denmark`) or `--all`. Add `--questions 2.1,3.4` for only those questions.
2. Run `evidence` with them, for example `evidence --label country=Denmark`. It writes an Excel workbook, one sheet per row; add `--format html` for one page fit to send to the organisation scored. The last line it prints is the file's path.
3. Give the owner the path and its first line. Never add anything to the file: no last edition's results, notes about last year, comparison or scorer notes, and no words of your own.

## A report drafted in the page

When there is no report or it is out of date, the Results tab offers a Generate report button. It asks Claude for a draft from the figures the page was built with and the rules in `report.md`, checks every number with the rule `check-report` uses, and lets the owner edit the draft and save it. A saved draft waits in the page's `report` collection; `host/claude.md` says how to read it. When there is one, adopt it in place of steps 4 to 6:

1. Run `facts`. The page drafted from the figures as they were at the last build, and `facts` writes the same figures unless something changed since.
2. Write the draft's `markdown` to `report/report.md` exactly as it is. It is the owner's text now: do not rewrite or restyle it.
3. Run `check-report`. If it lists numbers, change only those, to the figure in `report/facts.json`, and tell the owner each change. A number the facts no longer hold usually means the figures moved after the page was built: say so.
4. Go on from step 7 (build and publish), then clear the collection as `host/claude.md` says.

If the owner asks for the report in chat instead, write it as the steps above say.

## Charts the owner asks for

When the owner asks for a chart ("show the overall scores by country"), write a Vega-Lite spec for it and add it. It appears under "Your charts" on the Results tab, drawn from the project's own figures in the page's colours, with a table beside it.

1. Run `chart list`. It shows the charts there are and the columns each dataset has in this project.
2. Pick the dataset. Every one has `row` (the id) and `name`.
   - `rows`: one line per unit. One column per label, named as the label; one per score-sheet section, named by its id, holding the percent; `overall`; and `band_<of>` for each band set, holding the band's name. Scores are empty for a unit with questions still to score.
   - `cells`: one line per unit and question that applies. `question`, `state` (`Answered`, `No evidence`, `Not applicable`, `Not run`), `value`, `points`, `flags` (flag words joined by commas, empty when none).
   - `compare`: one line per unit and key compared with last edition. `key` (a section id, `overall`, `band:<of>` or a question id), `ours`, `theirs`, `agree` (true or false), `reason` (in words, empty when they agree).
   - `history`: one line per unit, key and edition. `key`, `edition`, `value`, `source` (`past` or `this run`).
3. Write the chart file in the scratchpad, not in the project: `{"id", "title", "description", "dataset", "spec"}`. The id is lower case with dashes. The title and a one-sentence description go in the file, not in the spec. Leave out `width`, colours and fonts: the page sizes and colours every chart.
4. Run `chart add --file <that file>`. It refuses a spec with `data`, `url` or `datasets` anywhere, a spec with no `mark`, and a field the dataset does not have (names a transform makes with `as` are fine). Fix what it lists and add again. Adding the same id again replaces the chart.
5. Build and publish, then tell the owner where to find it.

Never put numbers in a spec: no data values and no figures typed into a title, label or note. The page fills in the data. Numbers that only shape the chart, such as a scale from 0 to 100, are fine. To take a chart away, run `chart remove --id <id>`, then build and publish.

Example:

```json
{"id": "overall-by-country", "title": "Overall score by country",
 "description": "Mean overall score of the fully scored units in each country.", "dataset": "rows",
 "spec": {"mark": "bar",
          "encoding": {"y": {"field": "country", "type": "nominal", "title": null},
                       "x": {"aggregate": "mean", "field": "overall", "type": "quantitative",
                             "title": "Mean overall score", "scale": {"domain": [0, 100]}}}}}
```

## What each file is

| File | Written by | Your part |
|---|---|---|
| `compare/compare.json` | `compare` | Only the `reason`, `note`, `cells` and `by` of `unexplained` disagreements |
| `report/facts.json` | `facts` | Read only |
| `report/report.md` | you | All of it |

## Common mistakes

- Working out a difference, share or ratio for the report. It will not be in the facts and the check fails. Use a figure that is there, or use words.
- Writing a number from memory. The facts change on every run. Read the file.
- Editing `facts.json` to make the check pass. If a number you want is not there, leave it out and tell the owner it is not available.
- Overriding a reason the files proved. Those are final.
- Naming a cause for a score that no file shows.
- Publishing before `check-report` passes.
