// Modified copy of the "menu" mod that ships with Herdr 0.1.15 (Apache-2.0, https://github.com/herdrdev/herdr-app).
// Changed for tether; see THIRD-PARTY.md at the top of this repository for the licence and attribution.
// Context menus (a copy of Herdr's `menu`, for the spaces list): Close here does not ask, and a
// space also gets Fork, Back up now and Export… (mod tether-backups) and Archive: it takes a backup and unlinks
// the space's sessions (`tether delete`), no dialog; the Backups popup (archive button) brings it back. A space also gets Learn from this space and Review learned facts (`tether context`). A tab gets
// Continue in Copilot / Claude (`tether continue --tab`): its conversation with the other agent, in a new tab.
// Context menus (Herdr's items). Opened as a popup with
// props { tab: "pane" | "tab" | "space" | "global", target }.
// ↑/↓ move, Enter runs, Esc closes. An item is [label, command, args];
// a command "worktree" opens the worktree dialog instead.

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { run, props, closeOverlay, openOverlay, theme, keys, terminals, spaces, panes, exec, home, notify } from "herdr";

// Back up one space's conversations right away; the result is a toast.
async function forkSpace(a) {
  notify({ title: `Forking “${a.name}”…`, body: "Copying its sessions into a new space.", level: "info" });
  try {
    const r = await exec(`${home()}/.local/bin/tether`, ["fork", "--id", a.id, "-y"], { timeout: 120000 });
    if (r.code !== 0) {
      notify({ title: `Could not fork “${a.name}”`, body: (r.stderr || r.stdout).trim().slice(0, 300), level: "warning" });
      return;
    }
    notify({ title: `Forked “${a.name}”`, body: (r.stdout.trim().split("\n").pop() ?? "").slice(0, 200), level: "success" });
  } catch (e) {
    notify({ title: `Could not fork “${a.name}”`, body: String(e?.message ?? e), level: "error" });
  }
}

// Archive: unlink the space (tether takes a backup first and leaves the agents' files), closing it first if it is
// open. Recoverable from the archive (Backups: Restore & open). A space with no tether link is just closed.
async function archiveSpace(a) {
  try {
    const r = await exec(`${home()}/.local/bin/tether`, ["delete", "--id", a.id, "-y", "--close"], { timeout: 60000 });
    if (r.code !== 0) {
      const why = (r.stderr || r.stdout).trim();
      if (/nothing linked/.test(why)) {
        run("space.close", { id: a.id, confirmed: true });
        notify({ title: `Closed “${a.name}”`, body: "tether had nothing linked to it.", level: "info" });
        return;
      }
      throw new Error(why);
    }
    notify({ title: `Archived “${a.name}”`, body: "Bring it back from the archive button (Backups): Restore & open.", level: "success" });
  } catch (e) {
    notify({ title: `Could not archive “${a.name}”`, body: String(e?.message ?? e).slice(0, 300), level: "error" });
  }
}

async function backupSpace(a) {
  try {
    const r = await exec(`${home()}/.local/bin/tether`, ["backup", "--id", a.id, "--json"], { timeout: 300000 });
    if (r.code !== 0) {
      const why = (r.stderr || r.stdout).trim();
      notify({ title: `Could not back up “${a.name}”`, body: /no linked space/.test(why) ? "tether has no session linked to this space." : why.slice(0, 300), level: "warning" });
      return;
    }
    const j = JSON.parse(r.stdout);
    notify({ title: `Backed up “${a.name}”`, body: `${j.sessions} session${j.sessions === 1 ? "" : "s"}, ${(j.bytes / 1e6).toFixed(1)} MB new or changed`, level: "success" });
  } catch (e) {
    notify({ title: `Could not back up “${a.name}”`, body: String(e?.message ?? e), level: "error" });
  }
}

// Where a space is now: its first terminal's live folder, else where it was made.
function place(s) {
  const tabs = new Set((s.tabs ?? []).map((c) => c.id));
  return panes().find((p) => tabs.has(p.tab) && p.cwd)?.cwd || s.folder;
}

// A space's checkout: "main" (a repo's own), "linked" (a worktree), or null.
async function checkout(folder, on) {
  const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd: folder, on });
  const common = await exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd: folder, on });
  if (top.code !== 0 || common.code !== 0) return null;
  return common.stdout.trim() === `${top.stdout.trim()}/.git` ? "main" : "linked";
}

function items(p, git, tabAgent) {
  p = p ?? {};
  const tg = p.target ?? {};
  switch (p.tab) {
    case "pane": {
      // A kept sub-agent can go back under its parent, still running.
      const all = terminals();
      const term = all.find((t) => t.address === tg.terminal);
      const parent = term?.parent && all.find((t) => t.address === term.parent);
      // Once they go to the program, Shift+right-click still opens this menu.
      const toProgram = term?.labels?.["herdr.right-click"] === "program";
      // A terminal shown outside a tab (a layout terminal block, a window) has
      // no pane to split, zoom or close: its pane is the block's id.
      const inTab = panes().some((pane) => pane.pane === tg.pane);
      return [
        ...(inTab
          ? [
              ["Split right", "pane.split", { direction: "right", pane: tg.pane }],
              ["Split down", "pane.split", { direction: "down", pane: tg.pane }],
              ["Zoom", "pane.zoom", { pane: tg.pane }],
            ]
          : []),
        ...(term
          ? [
              toProgram
                ? ["Use Herdr right-click menu", "terminal.right_click", { terminal: term.address, to: "menu" }]
                : ["Send right-clicks to pane", "terminal.right_click", { terminal: term.address, to: "program" }],
            ]
          : []),
        ...(parent ? [[`Send back to ${parent.name || parent.address}`, "terminal.park", { terminal: term.address }]] : []),
        ...(inTab ? [["Close pane", "pane.close", { pane: tg.pane }]] : []),
      ];
    }
    case "tab":
      return [
        ["Rename", "tab.rename", { id: tg.id }],
        [tabAgent === "claude" ? "Continue in Copilot" : tabAgent === "copilot" ? "Continue in Claude" : "Continue in other agent", "tether.continue", { id: tg.id }],
        ["New tab", "tab.new", { space: tg.space }],
        ["Close", "tab.close", { id: tg.id }],
      ];
    case "space":
      return [
        ["Rename", "space.rename", { id: tg.id }],
        ...(git ? [["New worktree", "worktree", { space: tg.id, action: "new" }], ["Open worktree", "worktree", { space: tg.id, action: "open" }]] : []),
        ...(git === "linked" ? [["Remove worktree", "worktree", { space: tg.id, action: "remove" }]] : []),
        ["Fork", "tether.fork", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
        ["Back up now", "tether.backup", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
        ["Export…", "tether.export", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
        ["Learn from this space", "tether.learn", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
        ["Review learned facts", "tether.review", {}],
        ["Browse context", "tether.context", {}],
        ["Close", "space.close", { id: tg.id, confirmed: true }],
        ["Archive", "space.delete", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
        ["Delete…", "tether.delete", { id: tg.id, name: spaces().find((s) => s.id === tg.id)?.name ?? "" }],
      ];
    default:
      return [
        ["New space", "space.new", {}],
        ["Keybinds", "app.help", {}],
        ["Theme: graphite", "app.theme", { name: "graphite" }],
        ["Theme: ink", "app.theme", { name: "ink" }],
        ["Theme: moss", "app.theme", { name: "moss" }],
        ["Theme: atom one dark", "app.theme", { name: "atom-one-dark" }],
      ];
  }
}

function menuIcon(command) {
  if (command === "space.delete") return "mods/workspace-chrome/archive.svg";
  if (command === "tether.delete") return "mods/tether-backups/trash.svg";
  if (command === "tether.fork") return "mods/workspace-chrome/git-branch.svg";
  if (command === "tether.learn") return "mods/workspace-chrome/file-text.svg";
  if (command === "tether.review") return "mods/workspace-chrome/circle-check.svg";
  if (command === "tether.context") return "mods/workspace-chrome/folder.svg";
  if (command === "tether.continue") return "mods/workspace-chrome/git-compare.svg";
  if (command === "tether.backup") return "mods/workspace-chrome/files.svg";
  if (command === "tether.export") return "mods/workspace-chrome/folder.svg";
  const name = command.endsWith(".close") ? "x" : command.endsWith(".rename") ? "square-pen" : command.endsWith(".new") ? "plus" : command === "worktree" ? "git-branch" : command === "app.help" ? "terminal" : command === "app.theme" ? "settings" : command === "pane.split" ? "panel-right" : command === "pane.zoom" ? "square" : "terminal";
  return `mods/workspace-chrome/${name}.svg`;
}

// Continue in the other agent (tab menu): the tab's conversation carried over to Copilot or Claude in a NEW tab; the
// original tab is untouched. tether does the work (`tether continue --tab`); the result is a toast.
async function continueInOtherAgent(a) {
  try {
    const r = await exec(`${home()}/.local/bin/tether`, ["continue", "--tab", a.id], { timeout: 60000 });
    if (r.code !== 0) {
      notify({ title: "Could not continue in the other agent", body: (r.stderr || r.stdout).trim().replace(/^tether: /, "").slice(0, 300), level: "warning" });
      return;
    }
    notify({ title: "Continuing in a new tab", body: (r.stdout.trim().split("\n").pop() ?? "").slice(0, 200), level: "success" });
  } catch (e) {
    notify({ title: "Could not continue in the other agent", body: String(e?.message ?? e).slice(0, 300), level: "error" });
  }
}

// Learn from this space: read its conversation once (one model call, your own Claude) and propose facts for the context
// store (`tether context`); the review popup opens at once, shows the progress, then the facts. Nothing is saved until you accept them.
function reviewFacts(learn) {
  openOverlay({ mod: "tether-context-review", kind: "popup", width: 560, height: 520, props: learn ? { learn } : {} });
}

// The popup itself shows the progress (usually 15 to 60 seconds) and then the facts, so nothing depends on a toast.
function learnFromSpace(a) {
  reviewFacts({ id: a.id, name: a.name });
}

// The key bound to a command, for the hint on the right.
function shortcut(name, args) {
  // `pane.split:right` binds pane.split with { direction: "right" }.
  const value = args && Object.values(args)[0];
  const k = keys().find((b) => b.section === "global" && (b.action === name || b.action === `${name}:${value}`));
  return k ? k.keys : "";
}

export default class Menu extends View {
  init(_props, cx) {
    this.at = 0;
    this.git = null;
    this.tabAgent = null; // the agent linked to the tab (tab menu): names the "Continue in ..." item
    this.handle = cx.focus_handle();
    this.handle.focus();
    const p = props();
    const space = p?.tab === "space" && spaces().find((s) => s.id === p.target?.id);
    const folder = space && place(space);
    if (folder)
      cx.spawn(async (cx) => {
        this.git = await checkout(folder, space.machine).catch(() => null);
        cx.notify();
      });
    if (p?.tab === "tab" && p.target?.id)
      cx.spawn(async (cx) => {
        try {
          const r = await exec(`${home()}/.local/bin/tether`, ["list", "--json"], { timeout: 10000 });
          const rows = r.code === 0 ? JSON.parse(r.stdout) : [];
          this.tabAgent = rows.flatMap((row) => row.links).find((l) => l.tab === p.target.id)?.agent ?? null;
        } catch {}
        cx.notify();
      });
  }

  choose(i, cx) {
    const it = items(props(), this.git, this.tabAgent)[i];
    if (!it) return;
    closeOverlay(props()?.overlay);
    if (it[1] === "tether.fork") forkSpace(it[2]);
    else if (it[1] === "tether.learn") learnFromSpace(it[2]);
    else if (it[1] === "tether.review") reviewFacts();
    else if (it[1] === "tether.context") openOverlay({ mod: "tether-context", kind: "popup", width: 560, height: 620 });
    else if (it[1] === "tether.continue") continueInOtherAgent(it[2]);
    else if (it[1] === "tether.backup") backupSpace(it[2]);
    else if (it[1] === "tether.export") openOverlay({ mod: "tether-backups", kind: "dialog", width: 460, height: 270, props: { mode: "export", id: it[2].id, name: it[2].name } });
    else if (it[1] === "space.delete") archiveSpace(it[2]);
    else if (it[1] === "tether.delete") openOverlay({ mod: "tether-backups", kind: "dialog", width: 460, height: 270, props: { mode: "delete", id: it[2].id, name: it[2].name } });
    else if (it[1] === "worktree") openOverlay({ mod: "worktree", kind: "dialog", ...(it[2].action === "open" ? { width: 560, height: 480 } : { width: 440, height: 260 }), props: it[2] });
    else run(it[1], it[2]);
  }

  render(cx) {
    const t = theme();
    const list = items(props(), this.git, this.tabAgent);
    return v_flex()
      .id("menu")
      .track_focus(this.handle)
      .w_full()
      .min_h(0)
      .p(5)
      .bg(t.popover)
      .text_size(12.8)
      .on_key_down((e, cx) => {
        if (e.key === "down") this.at = (this.at + 1) % list.length;
        else if (e.key === "up") this.at = (this.at + list.length - 1) % list.length;
        else if (e.key === "enter") this.choose(this.at, cx);
        else if (e.key === "escape") closeOverlay(props().overlay);
        else return;
        cx.notify();
      })
      .children(
        list.map((it, i) =>
          v_flex()
            .when(i > 0 && (it[1].endsWith(".close") || it[1] === "tether.fork" || it[1] === "tether.learn" || it[1] === "tether.delete" || it[1] === "app.help" || (it[1] === "app.theme" && list[i - 1][1] !== "app.theme")), el => el.child(div().mx(7).my(4).h(1).bg(t.border)))
            .child(h_flex()
            .id(`mi-${i}`)
            .h(31)
            .px(8)
            .gap(9)
            .rounded(7)
            .items_center()
            .justify_between()
            .text_color(t.foreground)
            .when(i === this.at, (el) => el.bg(t.selected))
            .hover((s) => s.bg(t.hover))
            .on_click((_e, cx) => this.choose(i, cx))
            .child(svg(menuIcon(it[1])).size(14).flex_none().text_color(t.muted))
            .child(div().flex_1().child(it[0]))
            .child(div().text_size(11).text_color(t.dim).child(shortcut(it[1], it[2])))),
        ),
      );
  }
}
