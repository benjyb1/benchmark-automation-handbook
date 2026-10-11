---
name: trial
description: Use when a handbook project's set-up is complete and nothing has been scored yet, or when the owner asks for a trial, a test run, a cost estimate before the full run, to skip the trial, or to widen or repeat the trial.
---

# The trial

One question, on a small set of rows that between them cover every value of every label, run exactly as the full run will be, second reads included. The owner checks the answers in the viewer, and the trial's measured usage gives the full run's cost as a range. Its documents and answers are kept. Nothing more is scored until the owner says go. The trial is the owner's choice: they can skip it.

## Before you start

- Read the run skill now (`../run/SKILL.md` from this skill's folder). `<lib>`, `<project>`, `host/claude.md`, "build and publish" and the rules for the whole run are as it says, and the trial uses its steps.
- Apply pending viewer edits as `host/claude.md` says, then `python3 "<lib>/hb.py" status --project "<project>" --json`. If `next` is a set-up item, go back to the setup skill. If `next` is `run`, a trial has already run: ask the owner whether to widen it (step 7) or start the full run.
- If some rules are still open or skipped, tell the owner: the trial uses their recommended readings and those cells carry the "Claude's own judgement" flag.

## 0. Offer the trial, or skip it

Unless the owner asked for the trial, offer it in two lines with its purpose: a cost estimate before the full run, and an early check of the answers on a few rows before every row is scored. Ask whether to run it or skip it.

If the owner says skip: tell them there is no cost estimate yet, and that pacing starts after five rows of the full run (the run skill then gives a rough figure from those rows and asks whether to carry on). The skip is the go: use the run skill. They can still ask for a trial later, before the full run starts.

## 1. Suggest a question

Pick one question from `questions.json` and `scoresheet.json` that:

- is answered from documents that have to be found, not from one obvious page every row has;
- carries real points: a high top level, or a wide gap between levels;
- applies to most rows, so the slice can cover every label value.

Never use last edition's results to choose the question or the rows. Tell the owner your pick in two or three lines: the question, why it makes a good test, and one alternative.

## 2. Choose the rows

`python3 "<lib>/hb.py" cover --project "<project>" --question Q --json` gives `{"question", "rows"}`. Show the rows by name, how many there are, and which value of each label they cover. Coverage wins over size: usually about 10 rows. The owner may swap rows, ask for more (`--size N`), or widen to every row the question applies to.

## 3. Confirm

Say what will happen in three lines: documents found for these rows for all questions (they are kept for the full run), this one question scored, second searches and second reads where needed. Wait for a clear yes in chat.

## 4. Run the slice

`python3 "<lib>/hb.py" run start --project "<project>" --kind trial --questions Q --rows R1,R2,R3 --json`; keep its `id` as RUN.

Follow the run skill's Phase 1 ("Group-wide documents", "Each row's documents"), then Phase 2 (its numbered steps and "Follow-ups after each row's check") for these rows, with these differences:

- The rows are the trial's rows from step 2, not the rows `todo` lists. The run control check still comes before every row: a pause, stop now or stop after a row works as in the full run. The scope and the stop point are for the full run.
- Finders look for each row's documents for every question, as in the full run, so the finding cost is real and the documents carry over.
- Every kit is for the trial question only: `python3 "<lib>/hb.py" kit --project "<project>" --row ROW --run RUN --questions Q`. Rescoring after a second search is of Q only.
- Rule questions are raised with `--by trial`.
- Skip `estimate --during`: there is no estimate yet.

When every trial row is done: `python3 "<lib>/hb.py" check --project "<project>"`, build and publish, then `python3 "<lib>/hb.py" run finish --project "<project>" --run RUN`.

## 5. The owner checks

Ask the owner to look at the trial question's column in the viewer and open a few cells: the quote, the page and the link to the source. Ask what looks wrong.

If last edition's results were given (`last_results` draft or confirmed in the checklist): `python3 "<lib>/hb.py" compare --project "<project>"`, build and publish, and point the owner to the comparison view in Results. If there is too little to compare on one question, say so. Last edition's results are for the owner only: never put them in rule text, kits or prompts.

Rule questions the trial raised are in the viewer's Rules tab. Ask the owner to decide them now if they can, in chat or in the viewer, and apply each decision as the run skill's "Reruns" step 1 says. Trial cells resting on a decided rule become out of date and are scored again in the full run.

## 6. Estimate

`python3 "<lib>/hb.py" estimate --project "<project>" --json`. Show in chat:

"The full run should use about POINT tokens, between LOW and HIGH, for ROWS rows and CELLS cells."

Then each assumption it gives, one line each. If it has a `note` instead of figures (no usage recorded), say there is no figure yet and why. Build and publish so the estimate shows in the viewer.

## 7. Wait for go

Ask the owner: go, widen the trial, or change something. Do nothing more until they answer.

- **Go:** use the run skill.
- **Widen:** pick the added rows (`cover --size N` again, or rows the owner names, and drop any row a finished trial run already covers), start a new trial run on the added rows only, and repeat steps 4 to 6. The estimate comes from the latest finished trial, so keep the added set to about 10 rows or more.
- **Change something:** use the setup skill, then trial again if the change touches the trial question.

## Common mistakes

| Mistake | Instead |
|---|---|
| Choosing rows or the question by last edition's scores | `cover` and the question list only. |
| Finding documents for the trial question alone | Find for every question, as the full run will; the cost must be real. |
| Skipping second searches or second reads to save time | The trial runs every step, or the estimate is wrong. |
| Giving the estimate as one number | Point, range and assumptions. |
| Starting the full run because the trial looked fine | Wait for the owner's go in chat. |
| Insisting on the trial when the owner wants to skip it | Say there is no cost estimate yet and that pacing starts after five rows, then use the run skill. |
