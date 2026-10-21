# Host: Claude (the Code tab)

This is the only file that is specific to Claude. The skills say "build and publish", "ask the host for pending edits", "start a sub-agent" and "record usage"; this file says how. Another AI would get a sibling file with the same four sections.

In the commands below, `<lib>` is the plugin's `lib` folder and `<project>` is the project folder (see the skills).

## The page on the owner's own computer (`hb serve`)

The page can also run in the owner's normal browser, served from their computer, with no published artifact: `"<lib>/hb" serve --project "<project>"`. It prints the address and opens the browser; with `--no-browser` it only prints it. Run a second time for a project already being served, it opens the running page and exits. Started with no project, it opens the last one used.

There, the page does by itself what the sections below ask of you: what the owner saves (edits, rule decisions, run bar requests, corrections, documents sent from a brief, a report) is applied at once with the same commands, and the page reloads its data. A re-run request is scored on the owner's computer straight away (`kit --questions`, `score`, `ingest-answers`, `check`, `build`, one row at a time), with progress in the run bar. The corner chat asks Claude on the owner's plan through `claude -p`. So when the project is served this way:

- Don't publish it as an artifact, and don't read edits with ArtifactData: the page keeps them in `<project>/page-data/`, one file per document, and they are applied already.
- Tell the owner they can close the tab whenever they like; nothing is lost. To come back they open the same address while it is running, or start it again (the launcher app does this).
- Stopping it (Ctrl-C, or closing its window) also stops any scoring it started.

## Where the session runs

Open the Code tab session in the project folder, or in its parent (for a new benchmark, `~/Benchmark tool`). The Artifact tool only reads the page and its data files from the session's working directory or its scratchpad, and the viewer lives in `<project>/viewer/`.

If the session is somewhere else, build a copy into the scratchpad and publish from that copy:

```
python3 "<lib>/hb.py" build --project "<project>" --out "<scratchpad>/viewer" --json
```

The copy holds `page.html` and every data file at its published path, so use `<scratchpad>/viewer` wherever the steps below say `<project>/viewer`.

## Publish the viewer

Run after every step that changes the folder.

1. Build: `python3 "<lib>/hb.py" build --project "<project>" --json`. The JSON has `page`, `files`, `changed` (data files to send), `removed` (data files to delete) and `first_publish`. If the build stops with a problem, fix it and build again; do not publish an old build.
2. Call the Artifact tool (action `publish`, the default):
   - `file_path`: the absolute path of `<project>/viewer/page.html`. Never publish `index.html`; that is only a local preview.
   - `files`: every name in `changed`, mapped as `"<name>": "viewer/<name>"`, and every name in `removed` mapped to `null`. Names look like `evidence/<row>.json` and `downloads.json`. Files you leave out stay as they were.
   - `root`: the absolute path of `<project>`, so `viewer/<name>` resolves from there. Leave it out when the session's working directory is the project folder itself. For a scratchpad copy, use `root` = the copy and map `"<name>": "<name>"`.
3. First publish (`first_publish` is true in the build's JSON). Before it, load the `artifact-capabilities` skill and check the `db` rules below against it. If they differ, use the skill's shape and tell the owner. Also pass `icon: "checklist"` and
   ```
   capabilities: {"db": {"rules": [{"path": "edits", "read": "view", "write": "admin"},
                                   {"path": "decisions", "read": "view", "write": "admin"},
                                   {"path": "control", "read": "view", "write": "admin"},
                                   {"path": "report", "read": "view", "write": "admin"},
                                   {"path": "supplied", "read": "view", "write": "admin"},
                                   {"path": "corrections", "read": "view", "write": "admin"}]},
                  "user": {}, "downloads": true, "sample": {}, "assets": {}}
   ```
   `db` keeps the owner's edits and rule decisions, the run bar's control requests (`control`) and a report drafted in the page (`report`); the rules let only the owner write them. `user` says who made an edit, `downloads` lets the viewer save Excel and CSV files, `sample` lets the page ask Claude (the Generate report button), `supplied` with `assets` lets the owner send a document or a link from a step's brief when something is blocking you, and `corrections` holds the owner's "Correct this" on an answer (evidence, the right answer, or both). A page with `assets` can't be shared by public link, only within the owner's organisation; if the owner needs a public link, leave `assets` out and say that files then come through chat (links still work from the page). If the tool refuses any of this, stop and tell the owner what it said. Do not publish again with weaker settings.
   A page first published before these were added has no `control`, `report`, `sample`, `supplied`, `corrections` or `assets`. Publish it once with `url` and this whole `capabilities` block (a block replaces the stored one, so leave nothing out), then go back to leaving it out.
4. Every later publish: pass `url` = `viewer_url` and leave out `capabilities` and `icon`.
5. More than 254 data files in `changed`: one publish carries at most 255 files, the page being one. Send the first 254 with the page (and the capabilities and icon, if this is the first publish). Then publish again with `url` = the returned URL, the page again and the next 254, and so on. Put the `removed` names in the last call.
6. When the tool returns a URL, set `viewer_url` in `project.json` to it, because a later session needs it to update the same page (read the file, add the key, write it back with the usual JSON settings). On the first publish give the owner the link and say the page updates in place.
7. Only after every call succeeded, run `python3 "<lib>/hb.py" published --project "<project>"`. A failed or refused publish is not recorded, so the next build offers the same files again.

If a republish is refused:
- Because this session has not seen the artifact's files: call the Artifact tool with action `list`, `scope: "files"` and `url`, then publish again.
- Because the session has not read the artifact: the refusal hands over the live page, which counts as the read. Publish again with the same arguments. The page is large; do not open the saved copy, because `viewer/` in the project is the source of truth.
- Never use `force`.

## Read pending edits

Do this at the start of every turn while the project has a `viewer_url`. During a run, also do it before every row: that is how the run control check reads the run bar's requests.

1. Load the ArtifactData tool if it is deferred.
2. Call ArtifactData with action `list`, `url` = `viewer_url` and `collection` `edits`, then again with `collection` `decisions`, then with `collection` `control`, then with `collection` `corrections`. Follow every page until each collection is complete.
3. Control first, because a pause or a stop matters before anything else. Write a JSON file in the scratchpad (not in the project): `{"requests": [...]}`, each item the document's own fields with `id` set to the document's id. Run `python3 "<lib>/hb.py" control apply --project "<project>" --file "<that file>"`. Send everything each time: requests already handled are passed over, and one naming a row or question the project does not have is skipped with a reason to pass on to the owner. The run bar shows "Waiting for Claude" until the build after this records the ids.
4. Write one JSON file in the scratchpad: `{"edits": [...], "decisions": [...]}`. Each item is the document's own fields with `id` set to the document's id. Send everything each time, not only what looks new: ids already applied are skipped.
5. Run `python3 "<lib>/hb.py" apply-edits --project "<project>" --file "<that file>" --via viewer`, then build and publish if this or `control apply` applied anything. The "Edits waiting for Claude" banner clears once the ids are recorded.
   A decision with `"skip": true` is the owner pressing "Skip, keep Claude's reading": apply-edits marks that open rule `skipped` (no cell changes). Tell the owner in one line which rules they skipped; don't ask them to decide those now.
6. Corrections: write one JSON file in the scratchpad, `{"corrections": [...]}`, each item the document's own fields with `id` set to the document's id. For a document whose `evidence.asset` is set, first fetch the upload with the Artifact tool (action `read`, `url` = `viewer_url`, `path` = the asset id) and put the saved file's path in that item's `evidence.local`. Run `python3 "<lib>/hb.py" apply-corrections --project "<project>" --file "<that file>"`. Send everything each time: ids already handled are passed over. It checks each quote word for word against its source (fetching a link or filing the upload for the row first), checks the source is the company's own and dated before the cut-off, and puts the owner's answer in the cell, because the owner's word is final. It then lists the re-reads still to do: run them as the run skill's "Corrections from the owner" says, then `check`, build and publish. The panel says "Your correction, waiting for Claude" until that build records the round.
7. All four collections empty, or nothing new in them: say so and move on. Do not rebuild. Documents stay in the database after they are applied; `edits-applied.json`, the `applied` list in `control.json` and `corrections.json` are the record.

## If the viewer cannot save edits

If the page shows "Edits here need the page to be open in Claude; make changes in chat instead", or ArtifactData cannot read `edits` and `decisions`, the database is not available. Then edits and rule decisions exist only in chat. Ask the owner what to change, write the same `{"edits": [...], "decisions": [...]}` shape with ids starting `c-`, and run `apply-edits --via chat`. Tell the owner once that edits made in the viewer will not reach you until this is fixed. The run bar then says what to type in chat instead of offering buttons; pause, stop and scope from chat go through `hb control set` (the run skill's "Run control").

## Adopt a report saved in the page

The Results tab's Generate report button lets the owner draft the report in the page, check its numbers and save it. "Save as the report" writes one document to the `report` collection: `{id, markdown, at, by}`.

At the start of every turn while the project has a `viewer_url`, after reading pending edits:

1. Call ArtifactData with action `list`, `url` = `viewer_url` and `collection` `report`. Follow every page.
2. Empty: move on. Otherwise take the document with the latest `at` and adopt its `markdown` as the results skill says ("A report drafted in the page"): `facts`, write it to `<project>/report/report.md`, `check-report`, then build and publish.
3. Only after that publish succeeded, delete every document in `report` (ArtifactData action `delete` for each id, or one `batch`). Older drafts go too: the newest one wins. The page shows a saved draft as waiting for Claude until the collection is empty.
4. Tell the owner in one line that their report is in, and name any number you had to change.

If ArtifactData cannot read `report`, drafts saved in the page do not reach you. The owner can press "Copy the draft" and paste it in chat; adopt the pasted text the same way.

## Take in documents the owner sent from the page

The Grid's "missing" button by each company asks the owner to help where something blocks you (a document you couldn't keep, something not found), and the comparison can ask for a better copy of a weak source. Its Send button writes one document to the `supplied` collection: `{id, rows, pattern, replaces, what, file, asset, link, note, by, at}`. `asset` is an uploaded file's id, `link` a web address; either may be null, not both.

At the start of every turn while the project has a `viewer_url`, after reading pending edits:

1. Call ArtifactData with action `list`, `url` = `viewer_url` and `collection` `supplied`. Follow every page. Empty: move on.
2. For each document: with an `asset`, fetch it with the Artifact tool (action `read`, `url` = `viewer_url`, `path` = the asset id); it is saved locally and the result says where. Run `python3 "<lib>/hb.py" add-document "<that file>" --project "<project>" --rows <rows, joined with commas> --title "<what>"`. With a `link` (and no asset), run `python3 "<lib>/hb.py" fetch "<link>" --project "<project>" --row <row> --how provided` for each row. Read the `note`: it may say what the document is or where to look in it.
3. Then `prepare`. If the rows were scored already, rescore their questions as the run skill's "Reruns" says, since the new document may change answers.
4. Build and publish. Only after that publish succeeded, delete each document you filed from `supplied` (ArtifactData `delete`, or one `batch`). Leave any you could not file, and tell the owner why (the link did not load, the file was unreadable).
5. Tell the owner in one line per row what you filed and which answers it changed.

A file the owner attaches in chat is filed the same way: `add-document` with the rows they name.

## Sub-agents

Scorers are not sub-agents of this session: `python3 "<lib>/hb.py" score --kit KIT` reads a small enough row in one tool-free `claude -p` call, and otherwise starts the scorer as its own headless `claude -p` session in safe mode (no MCP servers, plugins, skills, hooks or CLAUDE.md; scorer tools only) and saves its receipt. A sub-agent of this session would carry the session's whole preamble, your connectors included, into every turn: 73,000 tokens a turn when this was measured, against 31,000 for the clean session.

Start the others with the Agent tool. Its prompt must stand alone, because the sub-agent sees nothing of this conversation.

| Role | `model` | Token cap to put in the prompt |
|---|---|---|
| Finder (also the second finder for one row, and a group finder) | `"sonnet"` | 120k per row |
| Scorer, only when `hb score` cannot run (no claude command line) | leave out, so it uses the session's model | 200k per row |
| Second read | leave out | 60k per batch (one question and group) |

- The prompt names the brief to follow (`finder.md`, `scorer.md` or `second-read.md` in the skill's folder), the project, the row, the run and the cap, and says: "If you reach the cap, stop and report where you got to."
- A scorer's prompt names its kit path and the token cap only. Kits sit in the system temp folder, outside the project; if the host asks permission to read there, allow it.
- Pass `run_in_background: false` when the next step needs the result, which is the normal case in a run.

## Usage

A scorer's receipt holds the tokens its session processed, split into cache reads, fresh input and output. Record it straight away:

```
python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step score --receipt KIT/receipt.json
```

Each Agent-tool sub-agent result reports `subagent_tokens`. Record that straight away too:

```
python3 "<lib>/hb.py" run usage --project "<project>" --run RUN --row ROW --step find --tokens N
```

`--step` is `find`, `second_find`, `score` or `second`. A group finder uses `--group LABEL=VALUE --step find` instead of `--row`. If a result carries no figure, record nothing and tell the owner once: the estimate will then say it cannot give a figure, and the run counts rows only. Exception: a group finder with no figure is recorded with `--tokens 0`; the run skill says why. Never make a figure up.

What `subagent_tokens` measures (checked 10 Oct 2026 on three Pecking Order scorers): the size of the sub-agent's context when it finished, about 145,000 to 152,000 for a scorer. The tokens the API processed were 2.7 to 3.8 million per scorer, because every one of its 24 to 32 turns reads the whole context again; 93% of that was cache reads and 73,000 of the context was this host's own preamble (tool definitions and the like), there before the scorer opened a file. A receipt counts the processed tokens, so a run whose scorers were recorded from receipts has a real estimate; one recorded from `subagent_tokens` alone is a context-size figure that compares rows and paces the run but is not the bill, and the estimate says so.

## What the spike showed (6 Oct 2026)

- Data files published beside the page are read by the page with relative `fetch()`. `evidence/<row>.json` and `downloads.json` stay as files.
- `db`, `user` and `downloads` are open to this account. The `db` `rules` form above is the documented shape, but it has not been tried on a live publish yet.
- Republishing the same page keeps the URL. Leave out `capabilities` and `icon`.
- Nobody has yet clicked through a published page (a database write, a download prompt). Until that has been done, treat a failure there as news to report, not as the owner's mistake.
