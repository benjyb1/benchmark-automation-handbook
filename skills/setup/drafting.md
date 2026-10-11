# Drafting the set-up files

How to read the owner's material and check it is ready come first; then the files. You write
these files directly in the project folder (before any cell is scored), then run `validate` and
fix every problem it lists. All files are JSON. Ids are non-empty text; dates are
`YYYY-MM-DD`; timestamps are UTC like `2026-10-07T09:30:00Z`. Keys not shown here are ignored.

## Reading the owner's material

- **Transcribe, never paraphrase.** Question wording, level meanings and coding rules go in word
  for word, typos included. Your own words go only in `short`, `evidence`, `plain`, and in rules
  the owner decides. Put `from` on everything: `"methodology, p.4"`, `"results spreadsheet,
  sheet Scores, row 12"`, `"pasted list, inbox/questions.txt, line 3"`, `"proposed by Claude"`.
- **A methodology PDF.** Read it page by page with your file-reading tool, noting page numbers.
  Look for the question list, the levels and their points, coding notes, which questions apply to
  which rows, the sections, how totals and bands are built, and how rounding is done.
- **A spreadsheet.** Read it with `python3` (`openpyxl` for .xlsx if installed; otherwise ask the
  owner to save it as CSV). List the sheets and headers first. Row names and their columns
  (country, brand, group) give the rows and labels. If it holds last edition's scores, use it
  while drafting only for the row list and, when the scoring is not written down, to rebuild it
  ("scoresheet.json"); never for any row's answer. The scores go to the held-back folder in
  step 4.
- **A pasted list.** Save it to a text file in `inbox/` first, so `from` can point to it.
- Where the material is silent (no points for a level, no rounding rule, a row with no country),
  leave the gap and ask in step 3, with a recommendation.

## The readiness check

Run it right after the drop-in, before drafting. It is never a verdict: every benchmark can be
set up, and the check says what that will take. For each gap, say what each fix costs the owner:
an hour or less (an export of something they hold), a few hours or days (rulings, notes,
uploads), or a change to the method (then say this edition will not compare like for like with
the last).

| Must-have | Ready when | When it is missing |
|---|---|---|
| The questions | Each is written down with its answer scale (yes or no, or levels) and what each level means | You draft them from the methodology, last edition's report or the column headings of last edition's results. With only an aim or a list of topics, offer to write them together: save their description to the inbox, draft a question with levels for each topic, and they confirm in step 3 |
| The rows | A list naming every row, with where it comes from and as at what date | You build it from last edition's results or report, or from the methodology's rule for who is in (for example "the ten largest retailers by sales"); they confirm the source and date |
| The scoring | It is written down how answers become points, totals and bands | You rebuild it from last edition's results ("scoresheet.json"). Without them, propose the simplest scheme the methodology allows. If no scheme fits, totals are not computed and every question is still scored |
| Evidence that can be found or supplied | For each question, the answer would show in something a researcher outside the organisation could find (its own reports, web pages and statements, a register, even behind a free login), or the owner can supply it | You list what they must upload; each becomes a data need in step 5. The kinds are below |
| A reference to check against | Last edition's results, question by question | Ask for the question-by-question sheet: most owners keep one even when they publish only totals, and it is the cheapest fix with the most value. With only totals or bands, the checks cover those. With none (a new benchmark), the trial is the reference: they check every trial answer by hand |

Look hardest at questions about what happens inside an organisation (its own figures, audits,
outcomes), questions whose answer is that something does not happen (no cages anywhere), and
anything checked by visiting. When the answer cannot be found:

- **Answers the organisations give the owner** (a questionnaire, a self-assessment, figures on
  request): the owner uploads them for each row; or the question is reworded to rest on what the
  organisation publishes (a change to the method).
- **Something observed** (a store visit, a menu, a shelf check): the owner uploads dated notes or
  photos, and they are scored like any document.
- **Paid or members-only data:** the owner uploads the extracts they hold.
- **Opinion** (a survey of what people think): no document shows it. The owner supplies their
  survey results, or those questions stay with them: leave them out of the questions and say in
  the score sheet's `plain` that the totals do not include them.

**Their answers about how they do it today.**

- **Where they find answers:** each tip goes into the where-to-look plan in step 2
  ("where_to_look.json").
- **Who checks:** say where that check happens here (the viewer shows each answer with its quote,
  the page it is on and a link). If someone else signs off the method, suggest they see the
  questions, the score sheet and the rules before the trial.
- **What they send the organisations:** if it is each organisation's findings from last edition,
  ask for copies. They are last edition's results question by question (held back in step 4,
  never added as documents), and the sources they cite go into last year's source list. Tell them
  each organisation's evidence for this edition can be pulled out for them to send once scored.

## Material that does not fit

Say what is different, propose the closest fit and what it gives up, and ask, with a
recommendation.

- **Several grids** (a retailer scorecard and a food-service one; a country index and a company
  index): one project per score sheet. Grids that share a score sheet and differ only in some
  rows or questions fit one project, with a label for the grid and `applies_to` on the questions
  that differ. Recommend one project when they are published as one ranking.
- **Rows inside rows** (brands inside a group, regions inside a country): the row is the level
  the questions are answered at; the level above becomes a label, and a group label if it
  publishes for its members. If published scores roll the inner rows up into the outer ones, say
  the score sheet cannot do that roll-up: the outer figure is worked out from the inner rows
  afterwards, and the rebuild check covers the inner rows only.
- **Free-text answers** (a description or comment, not a level): propose levels whose meanings
  say how they judge it today, with `from: "proposed by Claude"`. Text that is not scored stays
  out of the questions; each answer's quote carries it anyway.
- **Scores without a scoring sheet:** rebuild it ("scoresheet.json").
- **Anything else** (a tracker of statuses with no total, a list of pledges): describe it in
  their terms and propose the nearest fit.

## project.json

`init` writes it; you fill it in.

```json
{"name": "Example Benchmark", "slug": "example-benchmark-2025", "edition": "2025",
 "unit_word": "company", "unit_word_plural": "companies", "cutoff": null,
 "labels": ["brand", "country", "parent_group"], "group_labels": ["brand", "parent_group"],
 "group_by": "brand", "stage": "setup",
 "checklist": {"questions": "draft", "units": "draft", "scoresheet": "draft", "group_labels": "draft",
               "cutoff": "missing", "where_to_look": "missing", "needs": "missing", "rules": "missing",
               "last_results": "missing", "methodology": "confirmed", "source_list": "not_needed",
               "held_documents": "missing", "last_report": "missing"},
 "provided": [{"file": "inbox/method.pdf", "role": "methodology", "note": ""}],
 "created": "2026-10-06T10:00:00Z"}
```

- `unit_word`: the owner's word for one row. `labels`: ordered, the most useful first.
- `group_labels`: the labels whose values name an organisation that may publish one document for
  several rows, narrow to wide. `group_by` is the first of them. Both must be in `labels`.
- `provided[].role`: `questions | units | methodology | last_results | source_list |
  held_document | last_report | other`.
- Leave `stage` alone; the trial and run commands set it.

## The checklist

Statuses: `missing | draft | confirmed | skipped | not_needed`. The first eight must each be
`confirmed`, `skipped` or `not_needed` before the trial.

| Item | `draft` when | `confirmed` when |
|---|---|---|
| questions, units | drafted | the owner confirms (viewer or chat); set every item's `status` too |
| scoresheet, group_labels | drafted | the owner confirms in chat |
| cutoff | | the owner gives the date (grill, first round) |
| where_to_look | drafted | the owner agrees the plan and every row is covered |
| needs | some still `needed` | each need is `supplied` or `waived` (`not_needed` if there are none) |
| rules | any rule open | every rule decided or skipped (`not_needed` if the grill found none) |
| the five extras | received, not yet handled | handled (`not_needed` if the owner has none) |

## questions.json

```json
{"questions": [
 {"id": "1.1", "short": "Welfare policy", "wording": "Has the company published ...?", "section": "P1",
  "answer": {"kind": "levels", "levels": [
     {"value": 0, "points": 0, "meaning": "No evidence of a policy"},
     {"value": 1, "points": 1, "meaning": "Policy, no in-country communication"},
     {"value": 3, "points": 3, "meaning": "Policy communicated in-country"}]},
  "no_evidence_value": 0,
  "rule": "Coding rule text, transcribed.",
  "applies_to": null,
  "variants": [{"when": {"country": ["France"]}, "wording": "...", "rule": "..."}],
  "evidence": "A published policy naming the species",
  "status": "draft", "from": "methodology, p.4"}
]}
```

- `id`: the owner's numbering where there is one. `short`: a few words of your own.
- `answer.kind`: `yes_no` (exactly the levels `"yes"` and `"no"`) or `levels` (whole-number
  values). A numeric measure (a reported share, a count) becomes levels with the ranges in their
  meanings.
- `no_evidence_value`: the level a cell takes when nothing is found; usually the lowest. Ask if
  the methodology does not say.
- `applies_to`: null for every row, or `{label: [values]}` ("only for France" becomes
  `{"country": ["France"]}`). `variants`: the first match replaces `wording` and/or `rule`.
- `evidence`: your plain description of what would show the answer; step 5 works from it.
- `section` must be a section id in the score sheet.

## units.json

```json
{"units": [{"id": "dk-acme", "name": "Acme Denmark",
            "labels": {"brand": "Acme", "country": "Denmark", "parent_group": "Acme Holdings"},
            "status": "draft", "from": "results spreadsheet"}]}
```

Ids are unique, lowercase letters, digits and dashes, and never change once set. Every label key
must be in the project's `labels`. Propose labels from the columns you find; ask about any row
whose value you cannot see.

## scoresheet.json

```json
{"sections": [{"id": "P1", "name": "Commitments", "percent": true, "rounding": "half_up_int"},
              {"id": "P2", "name": "Performance", "percent": true, "rounding": "half_up_int"}],
 "overall": {"method": "weighted_mean_of_section_percents", "weights": {"P1": 0.5, "P2": 0.5},
             "use_rounded_sections": true, "rounding": "half_up_int"},
 "bands": [{"of": "overall", "bands": [{"name": "Tier 1", "min": 86}, {"name": "Tier 2", "min": 76}]}],
 "plain": "Plain-words description of how scores are built.",
 "computable": true, "status": "draft"}
```

- `rounding`: `none | half_up_int | half_up_1dp` (halves always round up).
- `overall.method`: `sum_points` (with `percent` true, total over maximum times 100) or
  `weighted_mean_of_section_percents`.
- Bands are matched on the rounded score: the highest band whose `min` the score reaches.
- A row's section maximum is worked out per row from the questions that apply to it.
- If the scheme does not fit, write it in `plain` and set `computable: false`; the viewer then
  says totals cannot be computed. Tell the owner.
- **Rebuilt from last edition's results** (nothing written down): work out each level's points
  from the values each question takes, the sections' weights and rounding from how their figures
  make the overall, and the bands from where they fall (ask for the published thresholds). Say so
  in `plain`. The rebuild check in step 4 tests it against the same figures, so it cannot catch a
  wrong guess that happens to fit: ask the owner to read the plain description closely.

## rules.json

```json
{"rules": [
 {"id": "R1", "kind": "scope", "question_ids": ["1.1"], "rows": null,
  "ask": "Does a group-wide policy count for a national row when ...?",
  "options": [{"id": "a", "text": "Yes, if ..."}, {"id": "b", "text": "No, unless ..."}],
  "recommended": "a", "why": "One short sentence.",
  "stakes": null, "last_year_note": null,
  "status": "open", "decision": null, "raised_by": "setup"}
]}
```

- `kind`: `scoring | scope | evidence`. `rows`: null (every row the questions apply to) or row ids.
- `stakes`: leave null; `stakes` fills it.
- `status`: `open | decided | skipped`. `decision` stays null unless decided; when decided:
  `{"option": "a" or null, "text": "the rule as decided", "at": "<timestamp>", "via": "chat"}`.
- Scorers see the `ask` and either the decision text or, while open or skipped, the recommended
  option's text. Keep last edition's results out of all three, and out of `why`.

## where_to_look.json and needs.json

```json
{"plan": [{"for": {"brand": ["Acme"]}, "places": [{"what": "Brand's national website", "url": null, "note": ""}]},
          {"for": "all", "places": [{"what": "Latest annual report", "url": null, "note": ""}]}],
 "last_year": [{"row": "dk-acme", "url": "https://...", "title": "..."}]}
```

```json
{"needs": [{"id": "N1", "what": "Supplier audit summary", "why": ["1.1"], "rows": null,
            "status": "needed", "documents": []}]}
```

- `for`: `"all"`, `{label: [values]}` or `{"rows": [ids]}`. Prefer group label values, so a
  group's documents are found once. Every row must be covered by some entry.
- The owner's tips from step 1 go in as places, in their words, with `note` starting "Owner's
  tip" and naming the questions it serves if not all ("Owner's tip, for 3.1 to 3.4: they report
  in the format we ask for, in a table at the end; work backwards"). Places only, never what a
  place said last year.
- `last_year`: last year's source list, one entry per row and address. Places to look only.
- `needs[].status`: `needed | supplied | waived`; `documents` lists the ids that supplied it.

## Last edition's results (held back)

Write the normalised file to `inbox/` (the `hold-back` command moves it, and the owner's file, out
of the inbox):

```json
{"level": "section", "edition": "2024",
 "rows": {"dk-acme": {"P1": 87, "P2": 60, "overall": 74, "band:overall": "Tier 3"}},
 "original": null, "notes": ""}
```

- Keys per row: question ids, section ids, `overall`, `band:<of>` (a band name). Values are
  numbers, band names for band keys, or null where the owner's file has a gap.
- `level` is the finest level present: `question | section | total | band`. `hold-back` fills
  `original` itself.
- Map every row to a unit id. Tell the owner about rows in the results that are not in the row
  list, and leave them out. Copy figures exactly as published; never round or correct them.

## Chat edits (after any cell is scored)

Write a file in the same shape as viewer edits, with ids starting `c-` that you have not used
before, then run `apply-edits --file <it> --via chat`:

```json
{"edits": [{"id": "c-7", "target": "question", "target_id": "1.1", "field": "wording",
            "value": "...", "by": null, "at": "2026-10-09T14:00:00Z"}],
 "decisions": [{"id": "c-8", "rule": "R3", "option": "a", "text": null, "by": null,
                "at": "2026-10-09T14:00:00Z"}]}
```

- `target`: `question` (fields `wording | short | rule | applies_to | levels_points`, where
  `levels_points` is `{"<level value>": points}`), `unit` (fields `name | labels`), or `confirm`
  (field and `target_id` both `questions` or both `units`, value `true`).
- A decision needs an option or a `text` (the owner's own wording).
