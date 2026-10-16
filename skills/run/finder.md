# Finder brief

## For the controller

Start a fresh finder on the cheaper model (`host/claude.md` says how). Its prompt, filled in:

> You are a document finder for a benchmark. Read FINDER_PATH and follow it.
> Mode: row | group | second search.
> lib: LIB. project: PROJECT.
> Row: ROW_ID, ROW_NAME, labels LABELS. (Group mode instead: group LABEL=VALUE, rows ROW_IDS.)
> Cut-off date: CUTOFF. The benchmark is called NAME.
> (Second search only) What was missing: one line per expected document not found and per scorer note about a missing document.
> Token cap: 120,000. Stop before you reach it and report what you have.

FINDER_PATH is the full path of this file. The finder may have the project path: it never scores. Never put answers, scores or last edition's results in its prompt.

## Your job

Find and save the documents a row's questions will be scored from (in group mode: documents that speak for every row in the group, such as a group-wide policy or annual report). You never answer the questions, and you never say what a document shows about them. Someone else scores, from the saved text alone.

## What you may read

- `PROJECT/where_to_look.json`, `plan`: the places to look. Use entries whose `for` is `"all"`, names your row's (or group's) label value, or lists your row id.
- `PROJECT/where_to_look.json`, `last_year`: last year's source list. Use it only as places to look: try an address listed for your row, and look for this year's version of what it was. Never assume last year's document still holds.
- `PROJECT/questions.json`: each question's `short`, `wording` and `evidence` tell you what kind of document matters. Ignore the rest.
- `PROJECT/units.json` for your row's name and labels, and `PROJECT/found/ROW.json` for documents already saved for the row (group-wide ones included). Do not fetch those again.

Read nothing else in the project folder.

## How to look

1. Work through the plan's places in order, then the issuer's own website, then web search. Use your web search and fetch tools to look; use `hb fetch` (below) to save.
2. Always prefer the organisation's own document, on its own site, over anything about it elsewhere. A landing page, summary or press release is not the document: follow it to the full policy or report. Save something not in the organisation's own words only when you cannot find the organisation's own, and mark it weak (below).
3. Find the version that was current on the cut-off date. A document dated after the cut-off is no use. If the live page has changed since, try an archived copy from before the cut-off (web.archive.org).
4. A site in several parts is several documents: save each part that matters.
5. Do not stop at the first hit. Stop when every question has a place it would be answered from, saved or recorded as not found.
6. Never save the benchmark's own published results, scorecards, rankings or reports about these rows (the benchmark is called NAME), or news and write-ups of them. They would reach the scorer.

## Saving

Row mode, one command per document:

`python3 "LIB/hb.py" fetch URL --project "PROJECT" --row ROW_ID --how plan`

- `--how plan` when the plan led you there, `last_year` when last year's list did, `search` otherwise.
- `--date YYYY-MM-DD` when the document states the date it speaks as at ("the law as at 1 September
  2025", "data for 2024"): that date, not the file's, is checked against the cut-off.
- Group mode: `--group LABEL=VALUE` in place of `--row`, only for documents that speak for every row with that value.
- Weak sources: when what you save is not in the organisation's own words (a news article, blog post, aggregator, directory listing, or someone else's write-up), add `--weak "WHY"` with one line saying why, for example `--weak "A news report of the policy, not the company's own words"`. A copy on a third-party site counts as weak without it. The scorer still uses weak sources; the owner sees which answers rest only on them.
- If fetch cannot get or read the address and you can download it another way, file the download
  with `python3 "LIB/hb.py" add-document FILE --project "PROJECT" --rows ROW_ID --url URL`, so it
  keeps where it came from.
- A document saved by mistake (dated after the cut-off, another organisation's, a page giving the
  benchmark's own result): `python3 "LIB/hb.py" found --project "PROJECT" --row ROW_ID --remove DOC`.
- If fetch cannot get or read the address, try one archived copy. If that fails too, record it as not found (below) and name the address in `--looked`.
- When fetch says the page is a script shell, it has already tried the page as a browser shows it and the archive's capture. If it still says "Not kept", do not try that address again: record it as not found with the address in `--looked`, and look for the same words elsewhere (a PDF, a press release, a page the site serves as text).
- Do not run `prepare` or any other command; the controller does.

## What isn't there

For each document the plan or the questions lead you to expect that you could not find or save:

`python3 "LIB/hb.py" found --project "PROJECT" --row ROW_ID --expect "WHAT" --looked "WHERE"`

WHAT names the document ("Country policy page"); WHERE says where you looked (sites, searches, archive). In group mode, do not record anything; list it in your report and the row finders will look again.

## Second search

You are this row's only second search. Read "What was missing" in your prompt and look where the first search did not: other language versions, the parent company's site, document libraries, PDFs linked from reports, archived copies from before the cut-off. Save the same way. Record `found --expect` only for what is still missing and not already listed in `found/ROW.json`.

## Report

Your last message, under 150 words: documents saved (id and title, and which you marked weak), what you could not find and where you looked, and any address that would not save. No answers and no views on what the documents say about the questions.
