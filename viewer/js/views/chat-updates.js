/* =============================================================================
   Handbook viewer: views/chat-updates.js, the corner chat's version tools. They add
   to HB.chatMore (views/chat-tools.js), so "is there a new version?", "update it" and
   "go back to the previous version" work in the chat as they do with the page's
   banner and footer (local.js), through the same /api/update call (serve.py,
   updater.py). Offered only in the tool on the owner's own computer.
     tool_version     this version, the latest one and what is new in it, the
                      previous version kept, the download key's expiry (asks GitHub)
     propose_update   puts installing the latest version to the owner (Confirm)
     propose_go_back  puts going back to the previous version to the owner (Confirm)
     propose_update_emails  puts starting (with an address) or stopping the opt-in
                      emails about big updates to the owner (Confirm)
   ========================================================================== */

(function (HB) {
  "use strict";

  const more = HB.chatMore;
  if (!more) return;
  const local = () => Boolean(HB.local && typeof HB.local.post === "function");
  const S = (props, required) => ({ type: "object", properties: props, required: required || [] });

  async function status(check) {
    if (check) return HB.local.post("update", { action: "check" });
    const res = await HB.local.get("update");
    return res.json();
  }

  function said(u) {
    return {
      current: u.current, latest: u.latest || null, newer_available: Boolean(u.available), whats_new: u.notes || [],
      previous_kept: u.previous || null, edition: u.edition, development_copy: Boolean(u.development),
      update_under_way: u.phase ? { phase: u.phase, words: u.words } : null,
      download_key: u.key_note || (u.key_days != null ? "Runs out in " + u.key_days + " days." : null),
      problem: u.problem === "offline" ? "GitHub could not be reached just now." : u.problem === "no_release" ? "No version has been released yet." : null,
      how_it_checks: u.privacy,
      update_emails: u.email ? "On, to " + u.email : "Off (the owner can ask for them)",
    };
  }

  async function toolVersion() {
    return said(await status(true));
  }

  async function proposeUpdate(input, api) {
    const u = await status(true);
    if (u.development) throw new Error("This is a development copy of the tool, which is updated from its source, not from here. Tell the owner that in one sentence.");
    if (!u.available) {
      throw new Error(u.latest ? "Version " + u.current + " is already the latest. Say so, and that the page checks once a day." :
        "No newer version was found" + (u.problem === "offline" ? " (GitHub could not be reached; suggest trying again in a minute)" : "") + ". Say so.");
    }
    const notes = (u.notes || []).length ? " What's new: " + u.notes.map((n) => String(n).replace(/[.\s]+$/, "")).join("; ") + "." : "";
    return Object.assign(api.propose("Update the benchmark tool from version " + u.current + " to " + u.latest + "." + notes +
      " It waits until nothing is running, then restarts this page. Your projects are not changed, and you can go back afterwards.",
      () => HB.local.post("update", { action: "install" })), {
      next: "Once confirmed, the update's progress shows in a banner at the top of the page and the page reloads by itself on the new version.",
    });
  }

  async function proposeGoBack(input, api) {
    const u = await status(false);
    if (u.development) throw new Error("This is a development copy of the tool, so there is no previous version kept here. Tell the owner that in one sentence.");
    if (!u.previous) throw new Error("No previous version is kept on this computer (one is kept after the first update). Say so in one sentence.");
    const blocked = u.back_blocked || [];
    if (blocked.some((b) => !b.backup)) {
      throw new Error("Version " + u.previous + " cannot read " + blocked.map((b) => b.name).join(", ") + " (this version upgraded the files) and there is no copy from before the upgrade. Explain that, and offer instead to keep this version and fix whatever made the owner want to go back.");
    }
    const restore = blocked.length ? " Version " + u.previous + " cannot read this version's project files, so " + blocked.map((b) => b.name).join(", ") +
      " goes back to how it was before the upgrade (how it is now is kept in a copy, not lost)." : "";
    return api.propose("Go back to version " + u.previous + " of the benchmark tool (you have " + u.current + "). It waits until nothing is running, then restarts this page on it." + restore + " You can come back to " + u.current + " the same way.",
      () => HB.local.post("update", { action: blocked.length ? "back_restore" : "back" }));
  }

  async function proposeUpdateEmails(input, api) {
    const u = await status(false);
    if (input.stop) {
      if (!u.email) throw new Error("No update emails are set up on this computer, so there is nothing to stop. Say so.");
      return api.propose("Stop the emails about big updates to " + u.email + " (the address is removed from the benchmark tool's website).",
        () => HB.local.post("update/email", { stop: true }));
    }
    const email = String(input.email || "").trim();
    if (!/^[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/.test(email)) throw new Error("Ask the owner for the email address to use, then call this again with it.");
    return api.propose("Email " + email + " about big updates to the tool, a few times a year. " + (u.email_privacy || ""),
      () => HB.local.post("update/email", { email }));
  }

  const baseTools = more.tools;
  const baseInstructions = more.instructions;
  more.tools = function (api) {
    const list = baseTools(api);
    if (!local()) return list;
    const w = (fn) => (input) => fn(input || {}, api);
    return list.concat([
      { name: "tool_version", description: "The benchmark tool's version on this computer, whether a newer one is out and what is new in it (asks GitHub now), the previous version kept for going back, an update under way, and when the Full version's download key runs out. Changes nothing.",
        inputSchema: S({}), execute: w(toolVersion) },
      { name: "propose_update", description: "Puts updating the benchmark tool to the latest version to the owner, with what is new. Once confirmed it waits until nothing is running, swaps the new version in and restarts the page; the old one is kept for going back. Saved only when they click Confirm.",
        inputSchema: S({}), execute: w(proposeUpdate) },
      { name: "propose_go_back", description: "Puts going back to the previous version of the benchmark tool to the owner. Once confirmed it waits until nothing is running, swaps it back in and restarts the page. Saved only when they click Confirm.",
        inputSchema: S({}), execute: w(proposeGoBack) },
      { name: "propose_update_emails", description: "Puts the opt-in emails about big updates to the owner: email (the address to use) to start or change them, or stop: true to stop them. Only the address and the edition go to the tool's website. tool_version says whether they are on. Saved only when they click Confirm.",
        inputSchema: S({ email: { type: "string" }, stop: { type: "boolean" } }), execute: w(proposeUpdateEmails) },
    ]);
  };
  more.instructions = function () {
    const lines = baseInstructions();
    if (!local()) return lines;
    return lines.concat(["The tool's own version: tool_version answers \"is there a new version?\" and \"what's new?\"; propose_update installs the latest one, propose_go_back returns to the previous one and propose_update_emails starts or stops the opt-in emails about big updates (all with Confirm)."]);
  };
})(window.HB = window.HB || {});
