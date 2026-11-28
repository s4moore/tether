// Backups, one mod with two uses (both just call the `tether` command):
//   - popup, no props: what the backup holds, plus Back up all / Restore missing transcripts / Import space.
//     Opened by the archive button in the tether-spaces header.
//   - dialog, props { mode: "export", id, name }: export one space to a folder you choose.
//     Opened by Export… in the tether-menu space menu.
//   - dialog, props { mode: "delete", id, name }: confirm, then delete one space for good (`tether delete --sessions`: the
//     space, its conversations and their backup copies). Opened by Delete… in the same menu.
// Esc closes.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify, pickFolder } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;
const POPUP_HEIGHT = 580;

// ---- the look shared by every tether popup: title + Esc keycap, grouped cards with hairlines ----
const header = (t, title, sub) => h_flex().px(2).pt(2).pb(2).items_center().gap(10).flex_none()
  .child(v_flex().flex_1().min_w(0).gap(1)
    .child(div().text_size(15).font_medium().text_color(t.foreground).child(title))
    .when(Boolean(sub), (el) => el.child(div().text_size(11.5).text_color(t.dim).child(sub))))
  .child(div().flex_none().h(18).px(6).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10).text_color(t.dim).child("Esc"));
const DIALOG_HEIGHT = 270;

async function tether(args, timeout = 300000) {
  const r = await exec(TETHER(), args, { timeout });
  if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
  return r.stdout;
}
const tetherJson = async (args, timeout) => JSON.parse(await tether(args, timeout));

function ago(iso) {
  const secs = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(secs)) return "never";
  if (secs < 90) return "just now";
  for (const [unit, n] of [["d", 86400], ["h", 3600], ["min", 60]]) {
    if (secs >= n) return `${Math.floor(secs / n)} ${unit} ago`;
  }
  return "just now";
}
const agentLabel = (a) => (a === "claude" ? "Claude" : "Copilot");
const mb = (bytes) => `${(bytes / 1e6).toFixed(1)} MB`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// macOS file chooser (Herdr only has a folder picker). null: cancelled. Throws on a real failure.
async function chooseExportFile() {
  const script = 'POSIX path of (choose file with prompt "Choose a tether export file" of type {"gz", "tgz"})';
  const r = await exec("/usr/bin/osascript", ["-e", script], { timeout: 600000 });
  if (r.code === 0) return r.stdout.trim() || null;
  if (/cancel/i.test(r.stderr)) return null;
  throw new Error(r.stderr.trim() || "the file chooser failed; from a terminal: tether import <file>");
}

export default class TetherBackups extends View {
  init(_props, cx) {
    this.exportMode = props()?.mode === "export";
    this.deleteMode = props()?.mode === "delete";
    this.status = null; // popup: `tether backup --status --json`
    this.items = null; // popup: `tether backup --list --json` (null while loading)
    this.row = null; // dialog: this space's row from `tether list --json`; false when it has none
    this.busy = null; // text while something runs
    this.note = null; // last result
    this.error = null;
    this.confirming = null; // session id of the one row waiting for "Delete" to be confirmed
    this.handle = cx.focus_handle();
    this.handle.focus();
    this.load(cx);
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        if (this.exportMode || this.deleteMode) {
          const id = props()?.id;
          this.row = (await tetherJson(["list", "--json"], 60000)).find((s) => s.id === id) ?? false;
        } else {
          [this.status, this.items] = await Promise.all([
            tetherJson(["backup", "--status", "--json"], 60000),
            tetherJson(["backup", "--list", "--json"], 60000),
          ]);
        }
        this.error = null;
      } catch (e) {
        this.error = String(e?.message ?? e);
        if (this.exportMode || this.deleteMode) this.row = false;
        else this.items = this.items ?? [];
      }
      cx.notify();
    });
  }

  // Runs one action with a "working" state, shows its result, then refreshes the list and status. The work runs inside
  // cx.spawn and uses ITS cx afterwards: the click's own cx is no longer good once the action has awaited, which left
  // the list showing a deleted conversation until the popup was reopened.
  work(label, fn, cx) {
    if (this.busy) return;
    this.busy = label;
    this.error = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        this.note = await fn();
      } catch (e) {
        this.error = String(e?.message ?? e);
      }
      this.busy = null;
      cx.notify();
      this.load(cx);
    });
  }

  backupAll(cx) {
    return this.work("Backing up…", async () => {
      const r = await tetherJson(["backup", "--json"]);
      notify({ title: "Backup finished", body: `${plural(r.sessions, "session")}, ${mb(r.bytes)} new or changed`, level: "success" });
      return r.sessions === 0 ? "Nothing to back up yet: no session is linked." : `Backed up ${plural(r.sessions, "session")} (${mb(r.bytes)} new or changed).`;
    }, cx);
  }

  // Opens the session in its space (bringing the space back if it was deleted, restoring the
  // transcript if the agent lost it). Closes the popup on success so you see the result.
  openOne(r, cx) {
    return this.work("Opening…", async () => {
      const out = (await tether(["open-session", r.session, "--agent", r.agent])).trim().split("\n").pop();
      notify({ title: !r.linked ? "Space restored" : r.in_agent ? "Opened" : "Session restored", body: out, level: "success" });
      closeOverlay(props().overlay);
      return out;
    }, cx);
  }

  forget(r, cx) {
    this.confirming = null;
    return this.work("Removing…", async () => {
      const out = await tetherJson(["backup", "--forget", "--session", r.session, "--agent", r.agent, ...(r.linked ? [] : ["--everything"]), "--json"]);
      notify({ title: r.linked ? "Removed from the backup" : "Deleted permanently", body: `${out.name}: ${mb(out.bytes)}`, level: "success" });
      return r.linked ? `Removed this ${agentLabel(r.agent)} session of “${out.name}” from the backup (${mb(out.bytes)} freed).`
        : `Deleted this ${agentLabel(r.agent)} session of “${out.name}” permanently (${mb(out.bytes)} freed).`;
    }, cx);
  }

  async importPick(cx) {
    if (this.busy) return;
    try {
      const path = await chooseExportFile();
      if (path) await this.importPath(path, cx);
    } catch (e) {
      this.error = String(e?.message ?? e);
      cx.notify();
    }
  }

  importPath(path, cx) {
    if (!path) return;
    return this.work("Importing…", async () => {
      const r = await tetherJson(["import", path, "--json"]);
      notify({ title: `Imported “${r.name}”`, body: "It is a closed space now: reopen it from the history button (clock).", level: "success" });
      return `Imported “${r.name}” (${plural(r.sessions, "session")}). Reopen it from the history button.`;
    }, cx);
  }

  async exportHere(cx) {
    if (this.busy) return;
    const p = props() ?? {};
    const folder = await pickFolder({ prompt: "Export here" }).catch(() => null);
    if (!folder) return;
    this.busy = "Exporting…";
    this.error = null;
    cx.notify();
    try {
      const r = await tetherJson(["export", "--id", p.id, "-o", folder, "--json"]);
      exec("/usr/bin/open", ["-R", r.path]).catch(() => {});
      notify({ title: `Exported “${r.name}”`, body: `${r.path}\n${plural(r.sessions, "session")}, full conversations: keep it private.`, level: "success" });
      closeOverlay(p.overlay);
      return;
    } catch (e) {
      this.error = String(e?.message ?? e);
    }
    this.busy = null;
    cx.notify();
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  button(id, label, kind, enabled, onClick, grow = false) {
    const t = theme();
    const primary = kind === "primary";
    const danger = kind === "danger";
    return h_flex().id(id).h(grow ? 34 : 26).px(grow ? 10 : 12).items_center().justify_center().rounded(8).border_1().text_size(12).font_medium().flex_none()
      .when(grow, (el) => el.flex_1().min_w(0))
      .border_color(primary && enabled ? t.solid : danger ? t.blocked : t.border).bg(primary && enabled ? t.solid : t.background)
      .text_color(primary && enabled ? t.solid_foreground : danger ? t.blocked : (enabled ? t.foreground : t.dim))
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.opacity(0.85)).on_click(onClick))
      .child(label);
  }

  // Delete for good: the space, its conversations and their backup copies. Archive (the menu's other item) keeps a backup.
  deleteNow(cx) {
    if (this.busy) return;
    const p = props() ?? {};
    this.busy = "Deleting…";
    this.error = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["delete", "--id", p.id, "--sessions", "--close", "-y"], { timeout: 120000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
        notify({ title: `Deleted “${p.name ?? "space"}”`, body: "The space, its conversations and their backup copies are gone.", level: "success" });
        closeOverlay(p.overlay);
        return;
      } catch (e) {
        this.error = String(e?.message ?? e).slice(0, 400);
      }
      this.busy = null;
      cx.notify();
    });
  }

  renderDelete() {
    const t = theme();
    const p = props() ?? {};
    const links = this.row ? this.row.links : [];
    const ready = this.row !== null && links.length > 0 && !this.busy;
    return v_flex().id("tether-delete").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(DIALOG_HEIGHT).p(18).gap(10).bg(t.popover).text_color(t.foreground)
      .child(header(t, `Delete “${p.name ?? "space"}”?`, "This cannot be undone"))
      .child(div().text_size(12.5).text_color(t.foreground).child(
        this.row === null ? "Checking what tether has linked…"
          : links.length > 0 ? `The space closes, and ${plural(links.length, "conversation")} and their backup copies are deleted from this computer: ${links.filter((l) => l.tab_open).length} in open tabs, ${links.filter((l) => !l.tab_open).length} left from tabs you closed (for example after Continue in another agent).`
            : "tether has nothing linked to this space, so there is nothing to delete here. Use Close instead."))
      .child(div().text_size(11.5).text_color(t.dim).child("To keep a backup you can bring back later, choose Archive instead."))
      .when(this.error, (el) => el.child(div().text_size(12).text_color(t.blocked).child(this.error)))
      .child(div().flex_1())
      .child(h_flex().justify_end().gap(8)
        .child(this.busy ? div().text_size(12).text_color(t.dim).child(this.busy) : div())
        .child(this.button("td-cancel", "Cancel", "plain", true, () => closeOverlay(p.overlay)))
        .child(this.button("td-delete", "Delete permanently", "danger", ready, (_e, cx) => this.deleteNow(cx))));
  }

  renderExport() {
    const t = theme();
    const p = props() ?? {};
    const links = this.row ? this.row.links : [];
    const ready = this.row !== null && links.length > 0 && !this.busy;
    return v_flex().id("tether-export").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(DIALOG_HEIGHT).p(18).gap(10).bg(t.popover).text_color(t.foreground)
      .child(h_flex().items_center().child(div().flex_1().text_size(15).font_semibold().child(`Export “${p.name ?? "space"}”`))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(div().text_size(12).text_color(t.muted).child(
        this.row === null ? "Checking what tether has linked…"
          : links.length > 0 ? `${plural(links.length, "session")} (${[...new Set(links.map((l) => l.agent))].join(" · ")}) go into one file: the links, and the full conversations.`
            : "tether has no sessions linked to this space, so there is nothing to export."))
      .child(div().text_size(11.5).text_color(t.dim).child("The file contains everything said in those conversations, including anything you pasted. Keep it private. Import it on another machine from the Backups popup."))
      .when(this.error, (el) => el.child(div().text_size(12).text_color(t.blocked).child(this.error)))
      .child(div().flex_1())
      .child(h_flex().justify_end().gap(8)
        .child(this.busy ? div().text_size(12).text_color(t.dim).child(this.busy) : div())
        .child(this.button("te-cancel", "Cancel", "plain", true, () => closeOverlay(p.overlay)))
        .child(this.button("te-export", "Choose folder…", "primary", ready, (_e, cx) => this.exportHere(cx))));
  }

  render() {
    if (this.exportMode) return this.renderExport();
    if (this.deleteMode) return this.renderDelete();
    const t = theme();
    const s = this.status;
    const p = props() ?? {};
    const idle = !this.busy;
    const items = this.items ?? [];
    const gone = s ? s.missing - s.recoverable : 0;
    const agentName = (a) => (a === "claude" ? "Claude" : "Copilot");
    const section = (label, n) => div().px(12).pt(10).pb(4).text_size(11).font_medium().text_color(t.dim).child(`${label} · ${n}`);
    const line = (r) => {
      const restorable = !r.in_agent && r.in_backup;
      const asking = this.confirming === r.session;
      const tab = r.tab && !/^\d+$/.test(r.tab) && r.tab.toLowerCase() !== r.agent ? ` · ${r.tab}` : "";
      return h_flex().id(`bk-${r.agent}-${r.session}`).px(12).py(9).gap(10).items_center().flex_none()
        .when(idle && r.linked && !asking, (el) => el.cursor_pointer().hover((st) => st.bg(t.hover)).on_click((_e, cx) => this.openOne(r, cx)))
        .child(v_flex().flex_1().min_w(0).gap(1)
          .child(div().truncate().text_size(12.5).font_medium().text_color(t.foreground).child(`${r.space}${tab}`))
          .child(div().truncate().text_size(11.5).text_color(t.muted).child(r.title ? `“${r.title}”` : "(no first message found)"))
          .child(asking
            ? div().text_size(11).text_color(t.blocked).child(!r.linked
              ? "Delete permanently? This removes the backup AND the agent's own copy, and cannot be undone."
              : r.in_agent
                ? "Are you sure? Only this backup copy is deleted, not the live session; the next backup copies it again."
                : "Are you sure? This backup is the only copy left, and deleting it cannot be undone.")
            : div().truncate().text_size(11).text_color(t.dim).child(`${agentName(r.agent)} · ${ago(r.last_used)} · ${mb(r.bytes)}`)))
        .child(asking
          ? h_flex().flex_none().gap(6).items_center()
            .child(this.button(`bk-yes-${r.session}`, r.linked ? "Yes, delete" : "Delete permanently", "danger", idle, (_e, cx) => { cx.stop_propagation(); this.forget(r, cx); }))
            .child(this.button(`bk-no-${r.session}`, "Cancel", "plain", true, (_e, cx) => { cx.stop_propagation(); this.confirming = null; cx.notify(); }))
          : h_flex().flex_none().gap(6).items_center()
            .child(r.linked
              ? this.button(`bk-open-${r.session}`, restorable ? "Restore & open" : "Open", "plain", idle,
                (_e, cx) => { cx.stop_propagation(); this.openOne(r, cx); })
              : this.button(`bk-restore-${r.session}`, "Restore & open", "plain", idle,
                (_e, cx) => { cx.stop_propagation(); this.openOne(r, cx); }))
            .child(div().id(`bk-del-${r.session}`).size(24).flex_none().flex().items_center().justify_center().rounded(6)
              .text_color(t.dim).cursor_pointer().hover((st) => st.bg(t.hover).text_color(t.blocked))
              .tooltip(r.linked ? "Delete this backup copy…" : "Delete permanently…")
              .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
              .on_click((_e, cx) => { cx.stop_propagation(); this.confirming = r.session; cx.notify(); })
              .child(svg("mods/tether-backups/trash.svg").size(14))));
    };
    const needs = items.filter((r) => !r.in_agent && r.in_backup);
    const fine = items.filter((r) => r.in_agent || !r.in_backup);
    return v_flex().id("tether-backups").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).p(12).gap(10).bg(t.popover).text_color(t.foreground)
      .child(header(t, "Backups", s ? `${s.backed_up} of ${s.linked} sessions backed up · ${mb(s.bytes)} · ${s.last_backup ? `last ${ago(s.last_backup)}` : "never backed up"}` : "Loading…"))
      .when(s && (s.recoverable > 0 || gone > 0), (el) => el.child(v_flex().gap(3).px(2)
        .when(s.recoverable > 0, (el2) => el2.child(div().text_size(12).text_color(t.foreground).child(
          `${plural(s.recoverable, "conversation")} gone from Claude/Copilot, but in the backup: use Restore & open below.`)))
        .when(gone > 0, (el2) => el2.child(div().text_size(11.5).text_color(t.dim).child(
          `${plural(gone, "linked session")} ${gone === 1 ? "has" : "have"} no transcript anywhere: never used, or cleaned up before a backup.`)))))
      .child(h_flex().gap(8)
        .child(this.button("tb-backup", "Back up all", "primary", idle, (_e, cx) => this.backupAll(cx), true))
        .child(this.button("tb-import", "Import…", "plain", idle, (_e, cx) => this.importPick(cx), true)))
      .when(this.busy, (el) => el.child(div().text_size(12).text_color(t.dim).child(this.busy)))
      .when(!this.busy && this.note, (el) => el.child(div().text_size(12).text_color(t.muted).child(this.note)))
      .when(this.error, (el) => el.child(div().text_size(12).text_color(t.blocked).child(this.error)))
      .child(v_flex().id("bk-list").flex_1().min_h(0).overflow_y_scrollbar().rounded(10).border_1().border_color(t.border).bg(t.background).pb(4)
        .when(this.items === null, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Loading…")))
        .when(this.items !== null && items.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Nothing is backed up yet. Press “Back up all”.")))
        .when(needs.length > 0, (el) => el.child(section("Gone from Claude/Copilot, in the backup", needs.length)).children(needs.flatMap((r, i) => (i ? [div().h(1).w_full().bg(t.border), line(r)] : [line(r)]))))
        .when(fine.length > 0, (el) => el.child(section(needs.length > 0 ? "Also backed up" : "Backed up", fine.length)).children(fine.flatMap((r, i) => (i ? [div().h(1).w_full().bg(t.border), line(r)] : [line(r)])))))
      .child(h_flex().gap(8).items_center()
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(
          s ? `Automatic: ${s.auto ? "on" : "off"} · ${s.dir}` : ""))
        .when(s && s.exists, (el) => el.child(h_flex().id("tb-open").flex_none().cursor_pointer().text_size(11).text_color(t.muted)
          .hover((st) => st.text_color(t.foreground)).on_click(() => exec("/usr/bin/open", [s.dir]).catch(() => {})).child("Show in Finder"))))
      .child(div().text_size(10.5).text_color(t.dim).child("Backups contain full conversations: keep them private."));
  }
}
