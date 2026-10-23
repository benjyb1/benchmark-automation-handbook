# The grill: settle every guess before anything is scored

A fixed question list does not give fixed scores. Two careful scorers reading the same document
can land on different levels because the score is made at the boundary: whose document counts,
which part of it applies, how explicit a statement must be. If those calls are left to whoever
scores on the day, the benchmark drifts. The grill moves each call onto the owner, before any
scoring, as a short series of decisions that become rules every scorer applies.

## What to look for

Read every question's wording, levels and rule as a hostile scorer would: where could two
careful people, with the same document, choose different levels? Each such place is a rule. If
the owner has already given you documents for some rows, read one or two cold and note where
you hesitated. Sort each rule into one of three kinds.

**Scoring** (how evidence becomes a level)
- What separates each level from the next, in words a scorer can test.
- Vague words ("meaningful", "comprehensive", "clear", "commitment", "regularly",
  "transparent"): a definition with one example that counts and one that does not.
- How strong or explicit a statement must be: a firm commitment or an aspiration; a named target
  or a general intention; attached to the right product, animal, market or year.
- A narrower commitment against an "all" requirement (one product line, 70% by a date).
- Several reported figures and no rule for which counts; one sentence that could score two
  questions (give it to the more specific question).
- A figure the method needs that is not reported: score the lowest level, or infer it? (Inferring
  needs a stated rule; recommend the lowest level unless the methodology says otherwise.)
- A numeric measure scored by ranges: which figure, which year, rounded how.
- Proving that something is absent (no ag-gag law, no cages anywhere): what a scorer must have
  read before saying so (the whole chapter, the code's index), and that a quote of an unrelated
  clause is not proof. When nothing found would score the best level, say so in the rule: a missed
  document then scores best.
- A rule names the questions it is about. Never attach one rule to every question unless it truly
  governs every one: scorers apply what they are given, and a blanket rule overrides levels it
  was never meant to touch (ALDF: a rule on partial species coverage rewrote question 1).

**Scope** (whose documents and which parts count)
- What one row is, when a parent and its brands, or a brand and its franchisees, could be one row
  or several (usually settled in step 3; ask only if a case is still open).
- Whose documents count: a global or parent group policy for a national row; franchise versus
  brand; a sister organisation; an organisation's publishing arm.
- Beyond the main document: appendices, companion reports, web pages, press releases; and whether
  a companion must date from the same period.
- A policy that covers only part of a row: one region, one sub-brand, one product range, one group
  of people. Does it count for the whole row?
- What belongs to a group, a list or a brand: a member or merely mentioned alongside; whether
  sub-lines are separate brands; when something is split in two, must every part meet the rule.
- Evidence that is a sample (product listings, shelf checks, pages that change daily): how it is
  drawn, where and when.

**Evidence** (what counts, as at when, and where to find it)
- **The cut-off date. Always ask it, in the first round, whatever else is going on.** Store the
  answer in the project's `cutoff`.
- A document dated after the cut-off but before the report: counts, or not?
- An undated web page: counts if it was live on the cut-off date, or on the day it was found?
- A deadline that has passed unmet, or data older than a few years: still counts?
- What counts as evidence: reports, web pages, press releases, social media posts, news
  articles, third-party trackers; images, tables and charts; packaging photos.
- Languages: does a document only in English count for a market whose language is not English?
- Where to look, for any row or group where the plan is unclear. Never guess this.

Ask about every theme above for every benchmark, even when the answer looks obvious. Where one
clearly does not arise, do not make a rule: list those themes in one line in the first round
("I don't think these arise here: ... Tell me if any do.").

## Writing a rule

Write each one into `rules.json` as an open rule (shape in `drafting.md`): the questions it
touches, the rows (null for every row those questions apply to), the ask, two or three options,
your recommended option and a one-sentence reason, `raised_by: "setup"`.

- **Write the options from the methodology first**, before you look at last edition's results.
  Each option must be a reading a scorer could apply to any row without re-interpreting it.
- **Recommend the reading a third party could apply consistently**, and say why in one sentence.
  A recommendation is a suggestion the owner accepts or rejects, never a decision made for them.
- **An option that settles a value carries it in `sets`** (`drafting.md`): the cut-off date, or
  what its questions take when nothing is found. Deciding the option then writes it.
- Then run `python3 "<lib>/hb.py" stakes --project "<project>"`. It fills each rule's stakes: the
  cells it touches and the points that could move across them.

## Last year's note (owner only)

When last edition's results are held back, look at them for each rule: do they show how last
year applied the point? Only rows where the options would give different answers tell you
anything. Patterns in the results themselves are what you can use, for example "every row in the
same parent group had the same value on 2.1 (12 of 12), which looks like option (b)". If the
results are only totals or bands, or show nothing either way, say so or leave the note null.

Write what you find into the rule's `last_year_note` and show it to the owner in the question.
It goes nowhere else: never into the ask, the options, the reason or the decision. Recover the
rule the owner applied; do not pick whichever reading would match last year best. If you would
switch readings from row to row to match, you are fitting, not recovering.

**Contradictions go first, whatever their stakes.** When two sources that should define the rule
point different ways (the methodology says one thing and last year's results show another, or
two parts of the methodology disagree), stop and put it to the owner. The options are usually:
follow the written rule (last year's differing rows then count as known departures); follow last
year's practice (the written rule changes, and you transcribe the new wording); or a third
reading. Say what each does to the scores.

## Putting the questions

Rounds of about five. Order: contradictions, then the cut-off (first round only), then the rest
by points at stake, highest first. All the round's questions in one message, numbered:

> **2. A parent group's policy for a national row** (scope; up to 96 points across 32 cells)
> Several rows belong to a parent group that publishes one group-wide policy. Does it count for a
> national row that publishes nothing of its own?
> - **(a) Yes, if it names the country or says it covers every market.** *(Recommended: it is
>   the plainest reading of "the company's operations in the country", and a scorer can check it.)*
> - **(b) Yes, always.** National rows score as their group does.
> - **(c) No.** Only the national row's own documents count.
>
> Last year: every row in the same group had the same value on 2.1, which looks like (b).

End the message by saying how to answer ("for example: 1a, 2c, 3 skip, 4: your own wording").
Give the stakes in plain words; never a confidence.

## Recording the answers

- **An option chosen:** `status: "decided"`, `decision: {"option": "a", "text": ..., "at": <now>,
  "via": "chat"}`. Write `text` as a test a scorer can apply without re-interpreting it: the
  condition, what it means for the level, and an example if that helps. No row names unless the
  rule is about those rows, and nothing from last edition's results.
- **The owner's own wording:** `option: null`, their rule in `text`, tidied only for clarity, and
  read back to them.
- **A skip:** say once what skipping means (scorers use your recommended reading, the cells carry
  a small flag, and the owner can decide it later in the viewer or in chat), and ask whether they
  would rather decide now. If they still skip, set `status: "skipped"` and keep the recommended
  option. Never pressure beyond that.
- After cells have been scored, a decision goes through a chat-edits file and
  `apply-edits --via chat` instead (see "Making changes" in `SKILL.md`).
- An answer about where to look updates the where-to-look plan, and may add a data need.
- The cut-off answer goes into the project's `cutoff`; set its checklist item to `confirmed`.

Refresh after every round (validate, fix, build, publish), so the Rules tab shows the decisions.

## When to stop

Keep going in rounds until every rule is decided or skipped and a fresh hostile read of each
question finds nothing new. Then set the checklist's `rules` to `confirmed`. If a third round is
still finding new rules on the same question, the wording itself is too loose to settle call by
call: take the wording back to the owner as a question edit.

Rules found later, by the trial or the run, are raised by those skills and handled the same way.

**Afterwards.** Say in chat each rule decided or skipped, in a line, with the points it settles. Rules left open that govern many answers show on the chat button's badge by themselves.
