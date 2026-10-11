# Filling the reasons

`compare` gives every disagreement a reason it can prove from the files and leaves the rest as `unexplained`, `by: "files"`. Your job is the `unexplained` ones only. Every reason the files proved stays as it is.

The result is shown to the owner labelled "Claude's reading", next to the reasons the files proved. So state only what the files show, and keep the inference small.

## Start by counting

Say how many need a reason before you begin:

```
python3 - <<'EOF'
import json
d = json.load(open("<project>/compare/compare.json", encoding="utf-8"))
todo = [x for x in d["disagreements"] if x["reason"] == "unexplained"]
print(d["level"], "level:", len(todo), "of", len(d["disagreements"]), "disagreements have no reason")
for x in todo:
    print(x["row"], x["key"], "ours", x["ours"], "theirs", x["theirs"], x.get("cells", ""))
EOF
```

If there are more than about 50, tell the owner the number and agree whether to do all of them or start with the rows that matter most. Do the work yourself in this session, a row at a time. Read each row's `answers/<row>.json` once and use it for every disagreement on that row.

## What a key is

The `level` in `compare.json` says what each `key` means.

- **question level:** the key is a question id. The cell is that one question: `cells` is `[key]`.
- **section level:** the key is a section id. Look at that section's questions that apply to the row. Name the cells most likely responsible, at most three. Prefer cells that are `no_evidence`, carry a flag in `checks/<row>.json`, rest on a rule, or cite a document with a problem. Use the direction too: if ours is higher, look for cells where we gave points that last year may not have.
- **total or band level:** first see which section keys on that row disagree, then go to the cells under those sections. Name at most five cells. If the held-back results have no section values, work from the cells with the most points at stake.

## What to read, in this order

Stop as soon as one reason is clear.

1. `answers/<row>.json`: for each cell under the key, its state, value, points, `rules`, `judgement`, and the evidence quotes and documents.
2. `checks/<row>.json`: the flags on those cells.
3. `documents/<doc>/meta.json` for the cited documents only: title, publisher, date, `host_type`.
4. `found/<row>.json`: `expected_not_found`, and whether `second_search` is done.
5. `rules.json`: only the rules the cells cite.
6. `where_to_look.json` `last_year`: the documents last year's list names for this row. And `<slug>.held-back/results.json` `edition` and `notes`.

Read the saved page text only when the quote does not settle which reading applies. Never read whole documents.

## Choosing the reason

Take the first line that the files support.

| Reason | What the files show |
|---|---|
| `missing_document` | The cell is `no_evidence`, or `expected_not_found` lists what the question needs, or `needs.json` has a need still `needed` whose `why` lists a cell under the key. We lacked a document the question depends on. |
| `different_document` | Last year's list names a document for this row that we do not cite, and we cite a different one on the subject (another page, another version, another level such as group instead of national). |
| `document_date` | We cite the same document or the same publisher's page, but ours is dated after last edition's collection period (the held-back `edition`, its `notes`, or the owner's word), so last year's assessor could not have seen it. |
| `rule_reading` | Same document on both sides, and a decided rule governs the cell, and its quote supports the other reading as well. |
| `open_rule` | Only if a cell cites a rule whose `status` is `open` or `skipped` and the script missed it. Normally the script assigns this one. |

If the disagreement turns on a reading nobody wrote a rule for, the reason is `rule_reading`. Tell the owner in chat that it needs a decision. Do not edit `rules.json` or raise a rule from here.

If no line is supported, leave `reason` as `unexplained`, leave `by` as `files`, and write a note saying what you looked at, for example "Checked 3 cells and their documents. None shows a cause." Do not pick the nearest reason to fill the gap. Say in chat how many you left.

## The note

One line, one sentence, plain UK English, no em dashes. It states what the files show, then at most a short "so". Use document titles and dates, not ids. Never say what last year's assessor saw or thought, because no file records it.

- `missing_document`: "No country page was found (listed as expected, not found), so 1.3 scored no evidence."
- `different_document`: "We cite the 2025 annual report. Last year's list names the sustainability page."
- `document_date`: "Our answer cites a policy dated March 2025. Last edition is 2024."
- `rule_reading`: "R2 counts group policies and 1.3 cites the group's policy. The national page says less."

## Writing it back

Do not rewrite the file by hand. Fill the ones you decided through the library, which keeps the file's format and changes nothing else. Set `<lib>` and `<project>` as in the skill.

```
python3 - <<'EOF'
import sys
sys.path.insert(0, "<lib>")
from handbook.folder import Project, read_json, write_json

path = Project("<project>").file("compare/compare")
data = read_json(path)
fills = {   # (row, key): (reason, note, cells)
    ("dk-acme", "P1"): ("rule_reading", "R2 counts group policies and 1.3 cites the group's policy.", ["1.3"]),
    ("se-acme", "P2"): ("unexplained", "Checked 3 cells and their documents. None shows a cause.", []),
}
filled = 0
for d in data["disagreements"]:
    f = fills.get((d["row"], d["key"]))
    if not f or d["reason"] != "unexplained":
        continue
    reason, note, cells = f
    d["note"], d["cells"] = note, cells
    if reason != "unexplained":
        d["reason"], d["by"] = reason, "claude"
        filled += 1
write_json(path, data)
print(filled, "reasons filled,", sum(d["reason"] == "unexplained" for d in data["disagreements"]), "left")
EOF
```

Put every row you have decided into one `fills` dictionary and run it once.

Then run `validate` and fix every problem it lists. Never touch `ours`, `theirs`, `agree` or any answer.
