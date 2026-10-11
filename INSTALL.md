# Setting up the benchmark tool (instructions for Claude)

The person who pasted the set-up prompt is a researcher at a charity or NGO, not a developer. They want to automate a benchmark they run (a ranking, scorecard or index). Your job is to install the tool on their Mac and get them into its page, where all the real work happens. Keep this short and calm.

Rules for the whole set-up:
- Before each step, say in one plain sentence what you are about to do and why. Never ask them to type commands; you run them.
- Ask before installing anything, and say how big it is. Do not install anything this file does not list.
- Use UK English. No jargon: say "the tool", "the page", "your Claude plan".
- Never read, copy or move their Claude sign-in. Sign-in only ever happens on Claude's own page.
- If a step fails, say what happened in a sentence and what you will try; do not paste error output at them.

`REPO_ZIP` below is `https://github.com/benjyb1/benchmark-automation-handbook/archive/refs/heads/main.zip`. `TOOL` is `~/.benchmark-tool` (written in full, without `~`, in commands).

## 1. Check this is a Mac

Run `uname -s`. If it is not `Darwin`, stop: say the tool works best with Claude on a Mac, and that they can email benjaminbream@gmail.com to use it on another computer or with another AI.

## 2. Download the tool

Say: "I'll download the benchmark tool (a few megabytes) into a hidden folder in your home folder."

Download `REPO_ZIP` with `curl -fsSL`, unzip it into a new empty folder in your scratch space, and move its top folder to `TOOL`, replacing any older copy there (this is how updates work; their projects live elsewhere and are never touched). Check `TOOL/lib/hb` exists and is executable.

The tool must not be inside Desktop, Documents or Downloads: macOS guards those folders and the tool's double-click app would be refused there.

## 3. Install uv, if missing

Check for `uv` on the PATH or at `~/.local/bin/uv`. If missing, say: "The tool needs one small program, uv (about 40 MB), which fetches the tool's own copy of Python. It goes in your home folder and needs no password. OK?" On a yes, run `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## 4. Install Claude's command line, if missing

The tool runs Claude in the background on their own plan through Claude's command line. Check with `TOOL/lib/hb doctor --json` (the first run takes a minute while uv fetches Python; say so). If the "Claude Code command line" check is not ok, say: "The tool runs Claude in the background through Claude's own command-line program (about 200 MB, from Anthropic, no password). OK?" On a yes, run `curl -fsSL https://claude.ai/install.sh | bash`.

## 5. Sign in

If the doctor's "Sign-in" check is not ok, say: "I'll open Claude's sign-in page in your browser. Sign in with the same Claude account you use here and press Authorise." Run `claude auth login --claudeai` (use `~/.local/bin/claude` if `claude` is not on the PATH) and wait for it to finish.

## 6. Check everything works

Say: "One last check: I'll start a tiny Claude session in the background to make sure the tool can use your plan and fetch web pages. It uses a few thousand tokens." Run `TOOL/lib/hb doctor --probe`. Explain any line that is not ok in one sentence, using its "What to do". Lines marked STOP must be fixed before going on. A "Web access" note means their organisation blocks Claude from fetching web pages: the tool still works if they add the documents themselves.

## 7. Name the benchmark and choose its folder

Ask what the benchmark is called and which edition (year). Propose the folder `~/Benchmark tool/<name>`, and say they can choose another, but not one synced by iCloud, OneDrive, Dropbox or Google Drive, and not in Desktop, Documents or Downloads. Check their choice with `TOOL/lib/hb doctor --folder "<folder>"`.

Make the slug from the name and edition (lowercase letters, digits and dashes), then:

```
TOOL/lib/hb init --root "<the folder that holds projects>" --name "<name and edition>" --slug "<slug>"
TOOL/lib/hb launcher --project "<project folder>" --desktop
```

The second command puts "Open <name>" in the project folder and on their Desktop.

## 8. Open the page

Run `TOOL/lib/hb serve --project "<project folder>"`. It opens the page in their browser.

Then tell them, in about four lines:
- From now on everything happens in that page. Its Claude button (bottom right) walks them through the rest, starting with whether their benchmark suits the tool.
- To come back another day, double-click "Open <name>" on their Desktop.
- Their files stay on this Mac, in the folder they chose.
- They can close this chat.

## Updating

If they paste the set-up prompt again later, repeat steps 2 and 6 only, then open their page with `TOOL/lib/hb serve` (it remembers the last project).
