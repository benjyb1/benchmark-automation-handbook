---
name: run
description: Use when the owner of a handbook project says go after the trial (or skips it), asks to score or carry on scoring the rows, asks to pause, stop, or run only some rows or questions, when a run stopped part way and needs resuming, or when cells need scoring again after a rule decision or an edit.
---

# The run

Two phases. Phase 1 finds the documents for every row in scope and stops there if the owner asked. Phase 2 scores each row blind from a kit, checks the answers, follows up the doubts and republishes the viewer. The owner stays in control: before every row you read their pause, stop and scope requests, and each phase ends in an output they can stop at, check and download. Progress lives in the project folder, so a run can stop after any row and pick up again. The trial skill uses these same steps on a slice.

## Set up the session

- `<lib>` is the `lib` folder inside the plugin folder (two levels above this skill's folder). `<project>` is the project folder agreed with the owner. Every command is `python3 "<lib>/hb.py" <command> --project "<project>" ...`. Exit 0 is done, 1 is a problem the command explains (read it and fix the cause, never work round it), 2 is a mistake in the command.
- Read `host/claude.md` (in the plugin folder, two levels above this skill's folder) once. It says how to start a sub-agent on a chosen model, read the tokens it used, read pending viewer edits, and publish. This skill does not repeat it.
- At the start of every turn, apply pending viewer edits, run control requests and the owner's corrections, and take in any documents the owner sent from the page, as `host/claude.md` says.
- "Build and publish" below means `python3 "<lib>/hb.py" build --project "<project>"`, then publish as `host/claude.md` says.
- This folder's other files: `finder.md` (the finder's brief), `scorer.md` (the scorer's prompt), `second-read.md` (second reads).

## Rules for the whole run

1. **Scorers and second readers are blind.** Their prompt names only kit paths (`scorer.md`, `second-read.md`). Never give them the project folder's path, last edition's results, notes about last year, last year's source list, the comparison, the report, or another row's answers.
2. **Every sub-agent prompt states its token cap**: finder 120,000 per row (or group), scorer 200,000 per row, second reader 60,000 per question and group. After each scorer, record its receipt with `run usage --receipt`; after each other sub-agent, record the tokens the host reports with `run usage --tokens`. If the host reports none, record nothing and tell the owner the estimate will lack that step (a group finder is the exception: see "Group-wide documents").
3. **Scores change only through commands.** Never edit `answers/`, `checks/` or `runs/` by hand, and never alter a value a scorer gave.
4. **No confidence.** Never give, ask for or describe how sure anyone is. A scorer's `unsure` says why an answer is a best guess (a rule or a document); it is followed up, not reported as a measure.
5. Plain UK English in chat. Say "missing data" for what the owner has to supply. One line of progress per row.
6. **When something blocks you, ask whether the owner can unblock it.** A download fails, a page is gone or behind a login, a document is named but nowhere to be found: say so in that row's progress line with the link, and ask whether they can download it or send it. Carry on with the other rows meanwhile. The step's brief gathers these at the end of the phase with a Send button on each; what they send is filed as `host/claude.md` says ("Take in documents the owner sent from the page").

## Start or resume

1. Apply pending viewer edits and run control requests, then `python3 "<lib>/hb.py" status --project "<project>" --json`. If it lists `problems`, run `validate` and fix them first. If `next` is a set-up item, use the setup skill. If no full run has started yet, and either no trial has finished or the owner has not said go in chat after the trial's estimate, use the trial skill, unless the owner skipped the trial: a skip counts as the go. A go given in this conversation after the estimate counts. In a new session where `hb status` says the next step is `run` and no full run has started, ask once: 'Start the full run?' A yes is the go; do not go back to the trial skill.
2. `python3 "<lib>/hb.py" control --project "<project>" --json` (see "Run control"). If the run is paused or set to stop now and the owner has just asked to carry on, `python3 "<lib>/hb.py" control set --project "<project>" --resume`. If they have not asked, tell them it is paused and ask whether to carry on. Tell the owner what is in scope when it is not everything.
3. Look at `runs/*.json` in the project folder. If a `full` run has `finished: null`, resume it and use its `id` as RUN. Otherwise start one and keep the `id` it prints as RUN:
   `python3 "<lib>/hb.py" run start --project "<project>" --kind full --json`
4. `python3 "<lib>/hb.py" todo --project "<project>" --json` gives `{"rows": [{"row", "questions"}], "cells": n}`: for each row in scope, the questions in scope that apply to it whose cell is missing, `not_run` or out of date, in `units.json` order. A row it does not list is done (or outside the scope). Do not read `questions.json`, `units.json` or `answers/` to work this out. Trial rows keep their trial answers and are scored on their other questions.
5. Order the rows still to do by their `group_by` label value, groups in the order they first appear in `todo`, so each group finishes together. Use the same order in both phases. Tell the owner: "N of M rows done; starting with X." (M is `counts.rows` from `status`; N is M less the rows `todo` lists.)
6. `python3 "<lib>/hb.py" todo --project "<project>" --find --json` gives `{"rows": [ids], "count": n}`: the rows in scope whose documents have not been looked for yet. If it lists any, start at Phase 1. If not, Phase 1 is done: if `stop_after` is `find` and the owner has just asked to go on to scoring, `python3 "<lib>/hb.py" control set --project "<project>" --stop-after none` (or `score` if they want to stop after scoring), then go to Phase 2.

## Run control

The owner can pause the run, stop it now, stop it after the row in progress, run only some rows or questions, and choose where it stops: after finding documents, after scoring, or at the end. Their choices live in `control.json`; never edit it by hand.

**Check before every row, in both phases, and between the phases:**

1. Read pending run control requests from the viewer and apply them with `python3 "<lib>/hb.py" control apply --project "<project>" --file FILE`, as `host/claude.md` says. If it applied anything, build and publish so the viewer's run bar shows it, and tell the owner in one line what changed. Pass on any reason it gives for a skipped request.
2. `python3 "<lib>/hb.py" control --project "<project>" --json` gives `{"paused", "stop_now", "stop_after_row", "scope": {"rows", "questions"}, "stop_after", "reruns", ...}`.
   **Re-runs:** each item in `reruns` (`{id, rule, rows, questions}`) is the owner asking, after deciding that rule in the viewer, for those answers to be scored again now. Apply the owner's decisions first (`apply-edits`), then for each row build a kit with `--questions` set to the listed questions, score it with `hb score`, ingest and check as usual, and finish with `python3 "<lib>/hb.py" control set --project "<project>" --rerun-done ID`. Then build and publish, and tell the owner in one line which answers changed. A re-run touches only the listed rows and questions, never a full run.
   - `paused` or `stop_now` is true: stop now. Do not start the next row or phase.
   - `stop_after_row` is the row just finished, or a row this phase has already finished: stop now. (The viewer names the row it thinks is in progress; the request reaches you once that row is over.)
   - `scope` changed: take the next row from `todo --json` (Phase 2) or `todo --find --json` (Phase 1) again rather than from your earlier list.
3. When you stop: build and publish, then tell the owner in two or three lines where the run stopped (the phase, rows done out of rows in scope), that nothing is lost, and that they can say "carry on" or press Resume in the viewer.

**The owner says it in chat:** change `control.json` with `control set`, then build and publish, and tell the owner the change takes effect at the next check (before the next row). Map what they say:

| The owner says | Command (`python3 "<lib>/hb.py" control set --project "<project>" ...`) |
|---|---|
| "pause", "hold on" | `--pause` |
| "stop now" | `--stop-now` |
| "stop after this one" | `--stop-after-row ROW` (the row in progress) |
| "carry on", "resume" | `--resume` (and `--stop-after none` when going on past a stop point) |
| "only do Denmark" | `--rows R1,R2,...`: the rows whose label has that value (read `units.json` for this) |
| "only questions 1.1 and 2.3" | `--questions 1.1,2.3` |
| "all the rows again", "every question" | `--all-rows`, `--all-questions` |
| "stop once the documents are found" | `--stop-after find` |
| "stop once they are scored" | `--stop-after score` |
| "go to the end" | `--stop-after none` |

`python3 "<lib>/hb.py" control --project "<project>"` says the current settings in plain sentences when the owner asks.

## Phase 1: find the documents

### Group-wide documents (once per run)

For each label in `project.json` `group_labels`, and each value of it held by two or more rows in scope, unless the run's `group_usage` already has `LABEL=VALUE`:

1. Run control check.
2. A finder on the cheaper model, in group mode (`finder.md` gives the prompt).
3. `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --group LABEL=VALUE --step find --tokens N`. If the host reported no token figure, record `--tokens 0` anyway: the group's entry in `group_usage` is how a resume knows the group is done, and without it the group is searched again.

Then `python3 "<lib>/hb.py" prepare --project "<project>"`.

### Each row's documents

Take the rows from `todo --find --json` once, in the order from "Start or resume" step 5, and work through that list (a row the finder leaves with nothing at all is not searched again in this phase). For each row:

1. Run control check.
2. **Find.** A finder on the cheaper model, in row mode (`finder.md`). Then:
   `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step find --tokens N`
3. **Prepare.** `python3 "<lib>/hb.py" prepare --project "<project>"`. If it says a document has almost no text (a page that draws its words with a script), record it for the row with `found --expect "WHAT" --looked "URL (saved copy has no text)"`, so the second search looks for a readable copy. A scorer cannot quote a page that is not in the copy.
4. Tell the owner in one line: the row, documents saved, anything expected but not found.

### End of Phase 1

**Write the documents brief**, as `deck.md` says (`--step documents`). It sits at the top of the Documents tab: how you searched, the documents you marked weak, copies from other sites and documents used for several rows, the open rules with most at stake, and what needs their eye (documents found but not kept, anything expected that was not found). Then ask in chat whether to carry on to scoring or stop here to check the sources in the Documents tab, where they can download them; the run bar offers the same.

Run control check. If `stop_after` is `find`, stop here: tell the owner the documents are found for every row in scope, that they can review and download the sources before anything is scored, and that they say "carry on" (or choose "carry on to the end" in the viewer) to start scoring.

## Phase 2: score each row

Take the rows from `todo --json` in the order from "Start or resume" step 5. A row that `todo --find` still lists (the scope grew after Phase 1) gets Phase 1's "Each row's documents" steps first. For each row:

1. Run control check. Apply pending viewer edits too; if one decides a rule, see "Reruns".
2. **Kit.** `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RUN`, adding `--questions Q1,Q2` (the row's `questions` from `todo`) when only some of the row's questions are still to score. The last line printed is the kit path, KIT.
3. **Score.** `python3 "<lib>/hb.py" score --kit KIT`: a fresh scorer in a clean headless session, as `scorer.md` says. It prints the scorer's one-line reply, the tokens it processed and the receipt's path. Then:
   `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step score --receipt KIT/receipt.json`
   If `score` says the claude command line is not installed, start the scorer with the host's sub-agent tool instead (`scorer.md`, "If hb score cannot run") and record its figure with `--tokens N`.
4. **Ingest.** `python3 "<lib>/hb.py" ingest-answers --project "<project>" --row ROW --run RUN --file KIT/answers.json`
   On exit 1 nothing is stored. Send the listed problems back to the same scorer once (or to a fresh scorer with the same kit and the problem list) and ingest again. Do not rebuild the kit: that deletes the answers. If it still fails, tell the owner which row and why, and go on. Ingest reads `KIT/capture.json` beside the answers, which binds each answer to the kit's copy of its document, so give it the file where the scorer wrote it, not a copy elsewhere.
5. **Check.** `python3 "<lib>/hb.py" check --project "<project>"`
6. **Follow up**, as below.
7. **Build and publish.** Tell the owner in one line: the row, cells answered, cells with no evidence, anything raised.
8. **Pace.** After every fifth row done in this run: `python3 "<lib>/hb.py" estimate --project "<project>" --during`. Exit 3 means the run is more than 25% over the estimate: stop, show the owner the message, and ask whether to carry on or stop here. Wait for the answer. If it says there is no estimate (the owner skipped the trial), then after the fifth row only: add up the tokens recorded in `runs/RUN.json` (`usage` and `group_usage`), divide by the rows done for a figure per row, multiply by the rows left in scope, and tell the owner that rough figure and that it comes from N rows of this run, not from a trial. Ask whether to carry on and wait for the answer.

Do not overlap steps. Start the next row's kit only after this row's check and publish have finished. Run the commands that write (`prepare`, `kit`, `ingest-answers`, `check`, `raise-rule`, `found`, `control`, `build`) one at a time.

## Follow-ups after each row's check

### Rule doubts become rule questions

Read `answers/ROW.json` for cells whose `unsure.why` is `rule` and whose `handled` is false.

- Put together doubts that ask the same thing, on this row and earlier ones. One rule per distinct doubt.
- If a rule in `rules.json` already asks it and covers this row, do not raise another. Link it to the cells that doubted it, on this row and any earlier row it covers: `python3 "<lib>/hb.py" link-rule --project "<project>" --rule R --rows R1,R2 --questions Q`. That adds the rule to each cell, marks the cell as Claude's own judgement while the rule is open or skipped, and clears the doubt. It lists any cell it skipped and why; exit 1 means nothing was linked, so read why.
- Otherwise raise one rule over every row the doubt could touch (usually every row the question applies to, so later rows' kits carry the reading):
  `python3 "<lib>/hb.py" raise-rule --project "<project>" --questions Q --rows R1,R2,R3 --ask "TEXT" --option a="TEXT" --option b="TEXT" --recommended a --by run --why "TEXT"`
  The ask is one plain question the owner can answer. The options are the readings in play, from the coding rule and the scorer's note. Recommend the reading the rule's words best support, with one sentence why. Never use last edition's results or notes about last year to choose.
- Tell the owner each new rule in one line. They decide in chat or in the viewer, whenever they like.

### Document doubts get one second search per row

`python3 "<lib>/hb.py" second-finds --project "<project>" --json` lists `{"row", "why", "questions"}`. Each listed row gets one second search, ever:

1. A fresh finder on the cheaper model, in second-search mode (`finder.md`), told what was missing: the row's `expected_not_found` entries in `found/ROW.json` and the `unsure.note` of each listed question. Then:
   `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step second_find --tokens N`
2. If it saved anything: `python3 "<lib>/hb.py" prepare --project "<project>"`, then score the affected cells again (the listed questions plus the row's `no_evidence` cells) as in Phase 2 steps 2 to 4: `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RUN --questions Q1,Q2`, `python3 "<lib>/hb.py" score --kit KIT`, `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step score --receipt KIT/receipt.json`, `python3 "<lib>/hb.py" ingest-answers --project "<project>" --row ROW --run RUN --file KIT/answers.json`.
3. `python3 "<lib>/hb.py" found --project "<project>" --row ROW --second-search-done`, whether or not it found anything.
4. Put what is still missing on the missing data list: one entry per gap in `needs.json`, `{"id": "N<next number>", "what": "...", "why": ["<question ids>"], "rows": ["ROW"], "status": "needed", "documents": []}`. Then `python3 "<lib>/hb.py" validate --project "<project>"` and fix every problem it lists. Tell the owner in one line what is missing and for which row; the setup skill takes it in when they supply it.
5. `python3 "<lib>/hb.py" check --project "<project>"`

### A document that changed since scoring gets a rescore

`check` flags a cell `document_changed` when the saved copy of a document it cites is no longer the capture the answer was scored from (a re-fetch, a re-read with `prepare --doc`, or a document the owner sent that replaced it). Its quotes are checked against the capture the scorer read, so this is not a quote problem; the cell is marked out of date and `todo --json` lists it. Score those cells again as "Reruns" says, with no new finder. Tell the owner in one line which rows and that the new copy is what they will be scored from.

### Group answered differently gets a second read

`python3 "<lib>/hb.py" second-reads --project "<project>" --json` lists `{"question", "group", "rows"}`. Take an item once every row of that group in this run has been scored (at the end of the run, take whatever is left). One item at a time, because building a kit empties that row's kit folder:

1. For each row in the item: `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RUN --questions Q --second`. Keep each KIT.
2. One fresh second reader on the session's model with every kit of the item, prompt as `second-read.md`.
3. For each row: `python3 "<lib>/hb.py" second-reads --project "<project>" --record --row ROW --run RUN --file KIT/second/answers.json`
4. Share the reader's tokens equally over the item's rows, for each row:
   `python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step second --tokens SHARE`
5. `python3 "<lib>/hb.py" check --project "<project>"`. Then judge whether the difference survived (`second-read.md`, "After the read"). If it did, raise one rule question for that question over those rows, as for rule doubts. Each cell keeps its first answer; a disagreeing second read shows as the "Two reads disagree" flag.

## End of Phase 2 and finish

When `todo --json` lists nothing (every row and question in scope is done), clear what is left of the second searches and second reads, then `python3 "<lib>/hb.py" check --project "<project>"`.

**Write the scores brief**, as `deck.md` says (`--step scores`). It sits at the top of the Grid: how you scored, the answers resting on your own reading and no rule, the open rules most answers rest on, and the answers carrying flags worth the owner's eye (weak source, quote not found, group answered differently, after the cut-off, reads disagree). Then ask in chat whether to carry on to the results or stop here; every answer can be opened on the Grid and downloaded.

Run control check. If `stop_after` is `score`, stop here: the run stays open. Tell the owner the rows in scope are scored and they can check them before anything else happens.

Otherwise `python3 "<lib>/hb.py" run finish --project "<project>" --run RUN`. Tell the owner: cells done out of expected (from `run finish`), rule questions still open and missing data still needed (from `status`), and any rows or questions left outside the scope. The results always wait for the owner: offer the results skill next, never start it unasked.

## Reruns after a rule decision or an edit

Deciding a rule, or editing a question's wording or rule after scoring, marks the cells resting on it out of date. Only those cells are scored again.

1. Apply the change. From the viewer: pending edits, as `host/claude.md` says. From chat: write a file such as `{"edits": [], "decisions": [{"id": "c-r4-1", "rule": "R4", "option": "a", "text": null, "by": null, "at": "2026-10-07T09:30:00Z"}]}` (`option` null with the rule in words in `text` when the owner chose neither option; the id starts `c-` and is not already in `edits-applied.json`), then `python3 "<lib>/hb.py" apply-edits --project "<project>" --file FILE --via chat`. Build and publish.
2. `python3 "<lib>/hb.py" todo --project "<project>" --json` lists the cells to redo: those out of date, and any `not_run` cell that started to apply after a label edit. In a full run it also lists rows not yet scored: leave those, the run reaches them.
3. During a full run, finish the current row first. Rows not yet scored need nothing: their kits carry the decision.
4. `python3 "<lib>/hb.py" run start --project "<project>" --kind rerun --questions Q1,Q2 --rows R1,R2 --json`; keep the id as RERUN.
5. For each affected row, as in Phase 2 steps 1 to 7 (the run control check first) with RERUN as the run: `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RERUN --questions Q1,Q2` (that row's cells to redo), `python3 "<lib>/hb.py" score --kit KIT`, `python3 "<lib>/hb.py" run usage --project "<project>" --run RERUN --row ROW --step score --receipt KIT/receipt.json`, `python3 "<lib>/hb.py" ingest-answers --project "<project>" --row ROW --run RERUN --file KIT/answers.json`, `python3 "<lib>/hb.py" check --project "<project>"`, any second reads it triggers, build and publish. No new finder: the documents have not changed.
6. `python3 "<lib>/hb.py" run finish --project "<project>" --run RERUN`, then carry on with the full run if one is open.

## Corrections from the owner

On any answer the owner can press "Correct this" in the viewer (or say it in chat) to give evidence (a quote, and where it is: a document saved for the row and its page, a link, or an upload), the answer it should be, or both. The owner's word is final; you check it cheaply and say whether you agree.

1. Apply them: `apply-corrections --file FILE`, as `host/claude.md` says. For a correction made in chat, write the same shape yourself, `{"corrections": [{"id": "c-<row>-<question>-1", "row": ROW, "qid": Q, "value": VALUE or null, "reason": "TEXT" or null, "evidence": {"quote": "EXACT WORDS" or null, "doc": DOC or null, "page": N or null, "link": URL or null, "asset": null, "file": null}, "by": null, "at": "<now>"}]}` (a file they attached in chat: save it and put its path in `evidence.local`). The code does the checks: the quote word for word on its source, the source the company's own and dated before the cut-off. A correction that fails gets your verdict from the code at once ("Claude disagrees because the quote is not word for word on page 4") and needs no re-read.
2. For each re-read it lists, one question and one row, never more. During a run, do it between rows.
   `python3 "<lib>/hb.py" run start --project "<project>" --kind rerun --questions Q --rows ROW --json` (keep the id as RERUN; one run can cover every re-read listed),
   `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RERUN --questions Q --look DOC:PAGE` (the `--look` it printed, when the correction named a page; the brief points the scorer to that page but never shows the owner's answer, reason or quote, so the scorer stays blind),
   `python3 "<lib>/hb.py" score --kit KIT`,
   `python3 "<lib>/hb.py" run usage --project "<project>" --run RERUN --row ROW --step score --receipt KIT/receipt.json`,
   `python3 "<lib>/hb.py" apply-corrections --project "<project>" --record ID --run RERUN --file KIT/answers.json`.
   Do not use `ingest-answers` for a re-read: `--record` stores the scorer's answer as your verdict. When it disagrees, the scorer's note is the reason the owner reads; if the note does not say plainly why in one sentence an owner would follow, add `--why "ONE SENTENCE"` written from the note and the page, never from last year.
3. `python3 "<lib>/hb.py" run finish --project "<project>" --run RERUN`, `check`, then build and publish.
4. Tell the owner in one line per correction: confirmed, or that you disagree and why, and that their answer stands either way. They can keep their answer (`keep: true`, flagged "changed by owner, Claude disagreed"), or send new evidence and a different answer, as many times as they like; every round stays in `corrections.json`.

## If the owner asks for the evidence for a company, a country or a question

For example "pull out everything for Acme Denmark". During a run, do it between rows; rows not scored yet say so.

1. Choose the rows by id (`--rows dk-acme,se-acme`), by a label value (`--label country=Denmark`) or `--all`. Add `--questions 2.1,3.4` for only those questions.
2. `python3 "<lib>/hb.py" evidence --project "<project>" --label country=Denmark` writes an Excel workbook, one sheet per row. Add `--format html` for one page fit to send to the organisation scored. The last line it prints is the file's path.
3. Give the owner the path and its first line. Never add anything to the file: no last edition's results, notes about last year or scorer notes, and no words of your own.

## Common mistakes

| Mistake | Instead |
|---|---|
| Giving a scorer the project folder "so it can find things" | The kit path only. The project folder holds notes on last year and the comparison. |
| Running `kit` again after the scorer wrote its answers | Ingest first. `kit` empties the folder. |
| A second read as soon as one row in a group differs | Wait until the group's rows are scored, then read them side by side. |
| A new rule for the same doubt on each row | One rule, over every row it touches. |
| A new rule for a doubt an existing rule already covers | `link-rule` that rule to the cells. |
| Reading `answers/` to work out what is left | `todo --json`. |
| A second search for a row whose `second_search` is set | One per row. Put the gap on the missing data list. |
| Skipping `run usage` because the figure looks small | Record every sub-agent; the estimate and pacing depend on it. |
| Telling the owner how confident a score is | Point to the evidence, the flags and the rule questions. |
| Leaving a source as it was when the owner says it is weak, or that it is not | `mark-weak --doc D --why "one line"` (or `--clear`), then `check`, build and publish. |
| Starting the next row without the run control check | Requests, `control apply`, `control --json`, before every row and between phases. |
| Carrying on after a pause because the row list was already made | A pause, stop now or stop after a finished row stops the run at the next check. |
| Editing `control.json` by hand, or ignoring the scope | `control set`; take the rows from `todo` and `todo --find`. |
| Starting the results because scoring finished | Stop at the end of Phase 2 and offer the results skill. |
| Re-reading a corrected cell with the owner's answer in the prompt, or ingesting the re-read | A kit with `--questions Q --look DOC:PAGE` only, then `apply-corrections --record`. |
| Changing an answer the owner corrected because you disagree | The owner's answer stays; say why you disagree. |
