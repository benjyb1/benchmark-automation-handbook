---
name: setup
description: Use when a charity owner wants to start a new benchmark, asks whether their benchmark can be run with the handbook, set up or change a benchmark's questions, rows, score sheet or rules before scoring, drops in a methodology, question list or last edition's results, or wants to add scope (new rows or questions) to an existing benchmark.
---

# Setting up a benchmark

## Overview

You guide the benchmark's owner, a researcher with good judgement and little technical
background, from a pile of material to a grid so specific that a scorer never has to guess. You
work in chat; the viewer page sits beside the chat and fills in as you go. You start from how
they do it today, and whatever they bring, you say what it will take; you never turn a benchmark
away. **The owner decides. You draft, measure what is at stake, and recommend.**

## Finding the tool and the folder

- `<plugin>` is the folder two levels above this skill's base directory (the harness states the
  base directory when the skill loads). `<lib>` is `<plugin>/lib`.
- `<project>` is the project folder agreed with the owner in step 1 (default `~/Handbook/<slug>`).
- Every command is run in full as `python3 "<lib>/hb.py" <command> --project "<project>"`, with
  full absolute paths (no `~`) in place of `<lib>` and `<project>`. Only `init` takes no
  `--project`.
- Publishing the viewer and reading the owner's viewer edits: follow `<plugin>/host/claude.md`.
  Read it before the first publish.
- The readiness check, how to read the owner's material (including material in unusual shapes)
  and the shapes of the files you write: `drafting.md` in this folder. How to run the grill:
  `grill.md` in this folder.

## Rules that hold throughout

1. **Ask everything at once.** When you need the owner, put every question in one message,
   numbered, each with your recommendation and a one-line reason (for a question about how they
   work today, what you will assume if they do not say). Never one question per turn.
2. **Plain UK English, in the owner's terms.** Say "the questions", "the rows", "the score
   sheet", never file names, "JSON" or command names. Short sentences.
3. **Data is missing or needs supplying.** Never describe information as "public" or "private".
4. **No confidence.** Never state, store or imply a confidence, a probability or "how sure I
   am", in chat, in a file or on the page.
5. **Last edition's results stay held back.** You may read `<slug>.held-back/results.json` (beside
   the project folder, not in it) during the grill, and tell the owner what it shows in chat or in
   a rule's `last_year_note`. Never put its values, or anything you inferred from them about a
   row, into questions, rule asks, options, reasons or decisions, the where-to-look plan, the
   data needs or any document. Never add last edition's results or last report as a document.
   Working out the score sheet from how its figures add up is allowed: that describes the method,
   not a row.
6. **Transcribe, never paraphrase.** Methodology wording goes in word for word, and everything
   you draft says where it came from (`from`).
7. **Never guess where to look.** If you cannot tell where a row's evidence would be, ask.
8. **Every change ends with a refresh.** Run `validate` and fix every problem it lists, then
   `build`, then publish as `host/claude.md` says:

```bash
python3 "<lib>/hb.py" validate --project "<project>"
python3 "<lib>/hb.py" build --project "<project>"
```

## Every turn

1. Once the viewer is published, start every turn by fetching the owner's pending viewer edits
   as `host/claude.md` says and applying them:
   `python3 "<lib>/hb.py" apply-edits --project "<project>" --file "<edits file>"`.
   Tell the owner in one line what was applied, and anything skipped with its reason.
2. Do what the owner asked, or the next set-up step.
3. If anything in the folder changed: validate, fix, build, publish.

**Making changes.** Check `python3 "<lib>/hb.py" status --project "<project>" --json`:
- `counts.cells_scored` is 0: write the files directly, following `drafting.md`.
- `counts.cells_scored` above 0: changes to a question's wording, short name, rule text,
  `applies_to` or points per level, a row's name or labels, confirmations and rule decisions go
  through a chat-edits file (shape in `drafting.md`) and
  `python3 "<lib>/hb.py" apply-edits --project "<project>" --file "<chat edits file>" --via chat`.
  It marks the affected cells out of date or no longer applying and recomputes points. Tell the
  owner which cells changed and offer to re-run them (the run skill does re-runs). For a change
  apply-edits cannot carry (a level's meaning, a question's section), tell the owner first, make
  it directly, then list the scored cells it touches and offer to re-run them.

## The six steps

Work through them in this order. Run `status` at the start of every session and after each step:
its last line says what comes next.

### 1. Drop in what you have

Ask, in one message: the benchmark's name and edition; what they call one row (a company, a
country, a brand in a market); where to keep the project folder (recommend `~/Handbook/<slug>`,
and never inside iCloud Drive, OneDrive, Dropbox or Google Drive, because syncing can damage
files while they are being written); and whatever they have, above all **the questions** and
**the list of rows**, then how scores are worked out, last edition's results (question by
question if they keep them), the methodology, last year's list of sources, documents they
already hold, and last edition's report. Any form is fine: files, spreadsheets, a pasted list.

Make a slug from the name and edition (lowercase letters, digits and dashes), then:

```bash
python3 "<lib>/hb.py" init --root "<the folder that holds projects, by default your home folder/Handbook>" --name "<name>" --slug "<slug>"
```

If it warns about a synced folder, tell the owner and recommend a folder outside it. Build and
publish at once: the viewer opens on the Set-up tab and the owner watches it fill.

Copy every file the owner gives into `<project>/inbox/` as given; write a pasted list to a text
file there. Record each in `project.json` `provided` with its role, and set the checklist
(`drafting.md`, "The checklist").

**Readiness, and how they do it today.** Read everything they gave you, then send one message
(`drafting.md`, "The readiness check", has the checks, the fixes and what to do with the
answers):

1. First, how they do it today, so the set-up fits their way of working: where they usually find
   each answer (for example "the table at the end of the ESG report"), who checks the scores, and
   what they send the organisations they score. Leave out anything they have already told you.
2. Then the five must-haves: the questions, the rows, the scoring, evidence that can be found or
   supplied, and a reference to check against. One line each on what you have; for each gap,
   what is missing in plain words and the steps to fix it, saying which steps you will do.
3. Anything that does not fit the usual shapes: what is different and the fit you propose
   (`drafting.md`, "Material that does not fit").

Never refuse a benchmark or call it not ready: say what it will take. A gap does not stop
set-up: step 2 drafts what the material supports, and each gap is settled at its own step (the
questions, rows and scoring in step 3, a reference in step 4, evidence in step 5). When they
answer, go on to step 2. When a missing piece arrives later, say in a line which gap it closes.

### 2. Claude drafts

From the inbox and what they told you in step 1, draft the questions (wording, levels, points,
section, which rows each applies to, variants, what evidence would show it), the score sheet,
the rows with their labels, and the group labels, following `drafting.md`, including every fix
you said you would do. Put each of their tips about where to look into the where-to-look plan
now, in their words. Everything is `draft` and says where it came from. Where the material is
silent (points for a level, rounding, a row's country), do not fill the gap quietly: leave it for
step 3. Validate, fix, build, publish.

### 3. Correct, then confirm

In one message: point the owner to the Set-up tab, say what you drafted in a few lines, list
everything you proposed rather than transcribed, and ask the open questions, each with a
recommendation. Then ask them to confirm four things, each explicitly: **the questions, the rows,
the group labels and the score sheet**. Explain group labels plainly: the labels whose values
name an organisation that may publish one document for several rows, narrow to wide (for
example brand, then parent group).

Questions and rows can be confirmed in the viewer or in chat; group labels and the score sheet in
chat. On a chat confirmation, set the item statuses and the checklist item to `confirmed`. Nothing
after this step runs until all four are confirmed.

### 4. Optional extras

Handle each extra as soon as it arrives, once step 3 is done, and always before steps 5 and 6.

- **Last edition's results.** Normalise them into the held-back shape (`drafting.md`), then:
  ```bash
  python3 "<lib>/hb.py" hold-back --project "<project>" --file "<normalised file>" --original "<the owner's file>"
  python3 "<lib>/hb.py" rebuild-check --project "<project>"
  ```
  **If any row does not match, set-up stops here.** Show the owner each mismatch (row, which
  figure, what the score sheet gives, what was published) and the likely causes: rounding, section
  weights, a question's points, which questions apply to a row, a band threshold. Fix the score
  sheet or the normalised results with the owner (a changed score sheet goes back to `draft` and
  is confirmed again), and re-run until every row matches. If the score sheet cannot compute
  totals, tell the owner the check cannot run and why.
- **Past editions** (results from before last edition): ask for them when the owner has them,
  because they show each row's trend and help spot big moves nobody can explain. Normalise each
  one the same way with its `edition` set (the year it covers) and hold it back with the same
  `hold-back` command. The latest edition stays the one compared with; an older one is kept for
  the trends; a newer one takes over and the old one is kept. Run `rebuild-check` again only when
  the latest edition changed. Past editions are held back like last edition's results: never use
  them in questions, rules, the where-to-look plan or the data needs.
- **Methodology** arriving after the draft: re-read it, correct the drafts, and ask the owner to
  confirm again whatever changed.
- **Last year's source list:** into the where-to-look plan's `last_year` entries, as places to
  look, never as answers.
- **Documents the owner already holds:**
  ```bash
  python3 "<lib>/hb.py" add-document "<file>" --project "<project>" --rows "<row>,<row>" --title "<title>"
  python3 "<lib>/hb.py" prepare --project "<project>" --doc "<document id it printed>"
  ```
- **Last edition's report:** keep it in the inbox. Read it only to understand the method.

Set each extra's checklist item to `confirmed` once handled (`hold-back` does this for the
results). An extra that arrives later is handled the same way at that point; then revisit steps
5 and 6 if it changes anything.

### 5. What data is needed

Complete the where-to-look plan (`drafting.md`): for each group label value, label value or row,
the places a finder should look, from the owner's tips (already in it from step 2), the
methodology and last year's source list. If you cannot tell where to look for some rows, ask.
Then, for each question, ask yourself what evidence would answer it and whether the plan can
reach it. Anything it cannot reach, starting with what the readiness check said they must upload
(figures an organisation gives only on request, the owner's own survey results or visit notes, a
paid database, a questionnaire the rows fill in), becomes a data need, and appears on the
checklist.

Ask the owner, in one message, to supply each need, saying which questions and rows it serves.
When they upload a file, add it with `add-document` for the rows it covers, `prepare` it, and
mark the need `supplied` with the document id. If they cannot get it, mark it `waived` and say
plainly that those cells will be scored on whatever else can be found, often as no evidence.

### 6. The grill

Follow `grill.md`. In short: find every point a scorer would otherwise have to guess, write each
as an open rule, run `python3 "<lib>/hb.py" stakes --project "<project>"`, and put them to the
owner in rounds of about five, most points at stake first, the cut-off date always in the first
round. Push to settle every rule; accept a skip. Refresh after every round.

## Hand-off

When `status` says the next step is the trial, set-up is complete.

**Write the set-up brief**, as `../run/deck.md` says (`--step setup`): it sits at the top of the Set-up tab, with what you held back, the cut-off and where the row list came from, the open rules with most at stake, and the data still missing. In the lines on how you did it, say what you proposed rather than took from their material (drafted questions, a rebuilt score sheet, a fit for an unusual shape). Ask in chat whether it looks right. They can stop here: the set-up is saved in the project folder and on the page.

In the same message, offer the trial, with its purpose: one question on about ten rows, run
exactly as the full run will be, to give a cost estimate for the full run and an early check of
the answers before anything else is scored. They can skip it. Recommend it, most of all when
last edition's results were not given: the trial is then the only early check. Hand over to the
handbook's trial skill with their answer: it runs the trial or, on a skip, moves to the run. Do
not start the trial without the owner's go-ahead.

## Adding scope later

"Add retail, here are the questions" gets the readiness check on what they sent, and the
question of where they find answers for the new part, then steps 2 to 6 on the new part only:
new rows with the new label value (add the label to the project's labels if it is new),
questions that apply only there (`applies_to`) or variant wording or rules for existing
questions, the confirmations for what changed, data needs for the new part, and a grill on the
new questions. Leave confirmed items alone. New rows and new questions add cells that are simply
not run yet, so they can be written directly; changes to existing rows' labels or existing
questions follow "Making changes".

## Common mistakes

| Mistake | Instead |
|---|---|
| Asking one question per turn | One message, every question numbered, a recommendation each |
| Calling a benchmark not ready, or turning it away | Say what each gap will take and which steps you will do |
| Drafting before asking how they do it today | Ask where they find answers, who checks, what they send; tips go in the plan |
| Forcing unusual material into the usual shapes without saying | Say what is different and propose a fit |
| Paraphrasing the methodology "for clarity" | Transcribe it; put your gloss in a rule the owner decides |
| Filling a gap with a plausible guess | Ask, with a recommendation |
| Going on to data needs or the grill with a rebuild mismatch | Stop; fix the score sheet or the results first |
| Quoting last year's values in a rule's ask, options or decision | Only in `last_year_note` and in chat |
| Guessing a row's website or document | Ask the owner where to look |
| Writing directly to files after cells are scored | Use a chat-edits file and `apply-edits --via chat` |
| Building without validating, or skipping the publish | Validate, fix every problem, build, publish |
| Forgetting the viewer edits at the start of a turn | Fetch and apply them before anything else |
| Saying "public data" or "private data" | Say what is missing and what the owner needs to supply |
| Telling the owner how confident you are | Give the options, the stakes and your recommendation |
