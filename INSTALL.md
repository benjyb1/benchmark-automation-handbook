# Setting up the benchmark tool (instructions for Claude)

The person who pasted the set-up prompt is a researcher at a charity or NGO, not a developer. They want to automate a benchmark they run (a ranking, scorecard or index). Your job is to install the tool on their computer (a Mac, Windows or Linux) and get them into its page, where all the real work happens. Keep this short and calm.

Rules for the whole set-up:
- Before each step, say in one plain sentence what you are about to do and why. Run each command yourself with your own command tool, so all they do is click Allow. If their set-up prompt says "run everything yourself", it means that, never taking over their screen.
- Never take control of their screen, mouse or keyboard unless they say yes to it (see the rule after next). That means no computer use, no Claude in Chrome, no screenshots of their screen, and no `osascript` or other script that clicks or types in their apps. Do not ask the Claude app for screen access either. Many charities, and the IT and rights teams behind them, are wary of an AI working their computer, and the set-up never needs it.
- If you cannot do a step with your own command tool (the app refuses the command, it needs a password or a window of its own, or something outside this chat has to be clicked), the default is to tell them exactly what to do. For a command, say "Please open Terminal (press Command and Space, type Terminal and press Return), paste the line below, press Return, and tell me what it says." and give the exact line in a code block they can copy, one line at a time. On Windows say PowerShell (from the Start menu) instead of Terminal. For a click, say which window and which button. Then wait, and carry on from what they tell you.
- Only after you have told them what to type, you may offer once, as a choice, "If you'd rather, I can do just this step on your screen for you. Would you like that?" Take control only on a clear yes, only for that one step, and give it back as soon as the step is done. If they say no, or do not answer it, keep telling them what to type for the rest of the set-up and do not offer again.
- If they say at any point that they would rather type everything themselves, give them each command to paste from then on, one at a time, instead of running it.
- Ask before installing anything, and say how big it is. Do not install anything this file does not list.
- Use UK English. No jargon: say "the tool", "the page", "your Claude plan".
- Never read, copy or move their Claude sign-in. Sign-in only ever happens on Claude's own page.
- If a step fails, say what happened in a sentence and what you will try; do not paste error output at them.
- Never run `git`, `python3`, `pip`, `brew` or `xcode-select`, and never ask them to install Homebrew or Apple's developer tools. On a Mac without those tools, `git` and `python3` open a box offering a 1-2 GB download. If a box like that appears anyway, tell them to click Cancel: the tool does not need it.
- Run long commands with a timeout of 10 minutes, so a slow download or a slow sign-in is not cut off.

`LATEST` below is `https://api.github.com/repos/benjyb1/benchmark-automation-handbook/releases/latest`, and `MAIN_ZIP` is `https://github.com/benjyb1/benchmark-automation-handbook/archive/refs/heads/main.zip`. `TOOL` is `~/.benchmark-tool` (written in full, without `~`, in commands).

## 1. Which computer is this

First say: "The Claude app will ask before I run each step. Click Allow each time. Nothing here needs your Mac password, and I won't take over your screen, mouse or keyboard. If a step can't run from here, I'll give you a line to paste into Terminal instead."

Run `uname -s`. `Darwin` is a Mac and `Linux` is Linux. If `uname` is not found, or its answer has `MINGW`, `MSYS` or `CYGWIN` in it, this is Windows. The steps below are written for a Mac; where Windows or Linux differs, the step says so.

On Windows, run the commands in PowerShell (your PowerShell tool, or `powershell -NoProfile -Command "..."` from Git Bash). There `TOOL` is `%USERPROFILE%\.benchmark-tool`, written in full (for example `C:\Users\Sam\.benchmark-tool`), and every `TOOL/lib/hb` below is `TOOL\lib\hb.cmd`. Nothing on Windows needs administrator rights. While the tool scores, Claude runs no commands on Windows (Windows has no fence round them), so do not offer WSL or anything else to change that.

On Linux, scoring needs two small programs that fence in the commands Claude runs, bubblewrap and socat. The doctor in step 4 says if they are missing; then say: "Scoring needs two small system programs, bubblewrap and socat. Installing them asks for your computer password, so I'll give you one line to paste into a terminal window and you type the password there. OK?" On a yes, give them `sudo apt install -y bubblewrap socat` (or `sudo dnf install -y bubblewrap socat`) to paste, as in the rules above. Never run it yourself: you must never see or type their password.

## 2. Download the tool

Say: "I'll download the benchmark tool (a few megabytes) into a hidden folder in your home folder."

Only released versions reach people, so ask GitHub for the latest release: `curl -sS -w '\n%{http_code}' LATEST`. On `200`, read `zipball_url` from its reply and download that with `curl -fsSL -o ZIPFILE "<zipball_url>"` (curl follows the redirect). On `404` there is no release yet: download `MAIN_ZIP` the same way instead. Unzip it into a new empty folder in your scratch space, and move its top folder to `TOOL` (this is how updates work; their projects live elsewhere and are never touched). If an older copy is already at `TOOL`, keep it as the previous version, so the page's "Go back to the previous version" works: if `TOOL.previous` is there, first move it to the Bin with `mv TOOL.previous ~/.Trash/benchmark-tool.previous-$(date +%Y%m%d%H%M)`, then `mv TOOL TOOL.previous`. Never use `rm` on either. Leave `TOOL.new` alone if it is there: the page's updater uses it. Check `TOOL/lib/hb` exists and is executable.

The tool must not be inside Desktop, Documents or Downloads: macOS guards those folders and the tool's double-click app would be refused there.

On Windows, in PowerShell: ask for the latest release with `Invoke-RestMethod -Uri LATEST` and take its `zipball_url` (if that fails with 404, use `MAIN_ZIP`), download it with `Invoke-WebRequest -UseBasicParsing -Uri <that url> -OutFile <zip>`, unzip with `Expand-Archive -Path <zip> -DestinationPath <empty folder>`, then move the top folder to `TOOL`. Keep an older copy as `TOOL.previous` as above, moving an older `TOOL.previous` out of the way with `Move-Item` to a dated name in your scratch space, never `Remove-Item`. Check `TOOL\lib\hb.cmd` exists.

## 3. Install uv, if missing

Check for `uv` on the PATH or at `~/.local/bin/uv`. If missing, say: "The tool needs one small program, uv (about 40 MB), which fetches the tool's own copy of Python. It goes in your home folder and needs no password. OK?" On a yes, run `curl -LsSf https://astral.sh/uv/install.sh | sh`. On Windows, check for `uv` or `%USERPROFILE%\.local\bin\uv.exe`, and install it with `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

## 4. Install Claude's command line, if missing

The tool runs Claude in the background on their own plan through Claude's command line. Check with `TOOL/lib/hb doctor --json` (the first run takes a minute while uv fetches Python; say so). If the "Claude Code command line" check is not ok, say: "The tool runs Claude in the background through Claude's own command-line program (about 200 MB, from Anthropic, no password). OK?" On a yes, run `curl -fsSL https://claude.ai/install.sh | bash`. On Windows, run `irm https://claude.ai/install.ps1 | iex` in PowerShell.

If the check is marked STOP because the command line is too old, say: "Your copy of Claude's command line is too old for the tool. I'll update it; it takes a minute and needs no password." Run `claude update` (or `~/.local/bin/claude update`). If that does not work, run the installer above, which puts the latest version in `~/.local/bin`, where the tool looks first. Then run `TOOL/lib/hb doctor --json` again to check.

## 5. Sign in

If the doctor's "Sign-in" check is not ok, say: "I'll open Claude's sign-in page in your browser. Sign in with the same Claude account you use here and press Authorise." Run `claude auth login --claudeai` (use `~/.local/bin/claude` if `claude` is not on the PATH; on Windows `%USERPROFILE%\.local\bin\claude.exe`) and wait for it to finish. They sign in and press Authorise themselves; never click anything on that page for them. If the command cannot run from your command tool, give them the same line to paste into Terminal, as in the rules above.

If the "Sign-in" check says the free plan, stop and say: "The tool needs a paid Claude plan (Pro or Max), because it runs Claude in the background on your plan. You can upgrade at claude.ai/upgrade, then paste the set-up prompt again." 

## 6. Check everything works

Say: "One last check: I'll start a tiny Claude session in the background to make sure the tool can use your plan and fetch web pages. It uses a few thousand tokens." Run `TOOL/lib/hb doctor --probe`. Explain any line that is not ok in one sentence, using its "What to do". Lines marked STOP must be fixed before going on. A "Web access" note means their organisation blocks Claude from fetching web pages: the tool still works if they add the documents themselves.

## 7. Name the benchmark and choose its folder

First ask whether they are starting a benchmark of their own or joining a colleague's (a team works on one project folder that a service such as Dropbox, Google Drive or OneDrive shares between them).

If they are joining a colleague's: ask where the shared folder is, and find the project folder in it (the one holding `team.json`). Check it with `TOOL/lib/hb team status --project "<project folder>"`. Tell them to keep that folder on this computer: in Dropbox "Make available offline", in Google Drive "Available offline" (or Mirror files), in OneDrive "Always keep on this device". Then run `TOOL/lib/hb launcher --project "<project folder>" --desktop` (it puts "Open <name>" on their Desktop only, never in the shared folder) and go to step 8. Their name on their changes is the name on their Claude account; they can change it in the page.

If they are starting their own:

Ask what the benchmark is called and which edition (year). Say its files will go in a folder called "Benchmark tool" in their home folder, with each benchmark in its own folder inside it, and that they can choose another place, but not one synced by iCloud, OneDrive, Dropbox or Google Drive, and not in Desktop, Documents or Downloads. Name folders the way Finder shows them ("Benchmark tool, in your home folder"), never as a full path. `PLACE` is `~/Benchmark tool` or the place they chose. Check it with `TOOL/lib/hb doctor --folder "PLACE"`.

Make the slug from the name and edition (lowercase letters, digits and dashes), then run the two commands below. The first makes the project folder `PLACE/<slug>`; pass `PLACE` itself as `--root`, not a folder inside it.

```
TOOL/lib/hb init --root "PLACE" --name "<name and edition>" --slug "<slug>"
TOOL/lib/hb launcher --project "PLACE/<slug>" --desktop
```

The second command puts "Open <name>" in the project folder and on their Desktop. On Windows it is a shortcut that keeps a small window in the taskbar while the page runs (closing that window stops the page). On Linux it also goes in their applications menu.

## 8. Open the page

Open the page with the app you just made: `open "PLACE/<slug>/Open <name>.app"`. It starts the page in the background and opens it in their browser, and it shows them the way back in works. Do not run `hb serve` yourself: it runs until stopped, and your command would be cut off, taking the page down with it.

Ask them whether the page has opened; never look at their screen to check. If it has not after half a minute, read `PLACE/<slug>/.serve.log` and fix what it says. If nothing helps, run `TOOL/lib/hb serve --project "PLACE/<slug>"` in the background instead.

Then tell them, in about four lines:
- From now on everything happens in that page. Its Claude button (bottom right) walks them through the rest, starting with whether their benchmark suits the tool.
- To come back another day, double-click "Open <name>" on their Desktop.
- Their files stay on this computer, in the folder they chose (say where, the Finder way, or File Explorer's on Windows; for a team, the shared folder, where each colleague's tool keeps it in step).
- They can close this chat.

## Updating

If they paste the set-up prompt again later, repeat steps 2 and 6 only, then open their page by opening their "Open <name>" app on the Desktop.
