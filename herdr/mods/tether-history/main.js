// Previous spaces: closed spaces tether still has sessions for, most recently used
// first. Click one to recreate it and resume its sessions. Esc closes.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;

function ago(iso) {
  const secs = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(secs)) return "";
  if (secs < 90) return "just now";
  for (const [unit, n] of [["d", 86400], ["h", 3600], ["min", 60]]) {
    if (secs >= n) return `${Math.floor(secs / n)} ${unit} ago`;
  }
  return "just now";
}

export default class SpaceHistory extends View {
  init(_props, cx) {
    this.rows = null; // null: loading
    this.error = null;
    this.handle = cx.focus_handle();
    this.load(cx);
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["list", "--json"]);
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
        // tether already sorts newest first; sorting again keeps that true whatever it prints.
        this.rows = JSON.parse(r.stdout).filter((s) => s.closed)
          .sort((a, b) => (a.last_used < b.last_used ? 1 : -1));
      } catch (e) {
        this.rows = [];
        this.error = String(e?.message ?? e);
      }
      cx.notify();
    });
  }

  reopen(row) {
    closeOverlay(props().overlay);
    exec(TETHER(), ["open", "--id", row.id, "-y"], { timeout: 60000 }).then((r) => {
      if (r.code !== 0) notify({ title: `Could not reopen “${row.name}”`, body: (r.stderr || r.stdout).trim().slice(0, 300), level: "error" });
    }).catch((e) => notify({ title: `Could not reopen “${row.name}”`, body: String(e?.message ?? e), level: "error" }));
  }

  // Archive a closed space: unlink it (tether backs it up first, the agents' files stay), so it moves to the
  // archive (Backups: Restore & open). Nothing to confirm: it is recoverable.
  del(row, cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["delete", "--id", row.id, "-y"], { timeout: 60000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
        notify({ title: `Archived “${row.name}”`, body: "Bring it back from the archive button (Backups): Restore & open.", level: "success" });
      } catch (e) {
        notify({ title: `Could not archive “${row.name}”`, body: String(e?.message ?? e).slice(0, 300), level: "error" });
      }
      this.load(cx);
    });
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  render() {
    const t = theme();
    const rows = this.rows;
    const line = (row) => {
      // Always reopenable: a space nobody typed in yet has no transcript, and just starts fresh.
      const live = row.links.some((l) => l.transcript);
      const restorable = !live && row.links.some((l) => l.recoverable);
      const fresh = !live && !restorable;
      const agents = [...new Set(row.links.map((l) => l.agent))].join(" · ");
      const forked = row.forked_from ? `fork of ${row.forked_from} · ` : "";
      return h_flex().id(`sh-${row.id}`).px(10).h(40).flex_none().items_center().gap(10).rounded(8)
        .cursor_pointer().hover((s) => s.bg(t.hover)).on_click(() => this.reopen(row))
        .child(v_flex().flex_1().min_w(0)
          .child(div().truncate().text_size(13).text_color(t.foreground).child(row.name))
          .child(div().truncate().text_size(11).text_color(t.dim)
            .child(fresh ? `${forked}${agents} · empty, starts a fresh session` : restorable ? `${forked}${agents} · restored from the backup` : `${forked}${agents}`)))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(ago(row.last_used)))
        .child(div().id(`sh-del-${row.id}`).size(24).flex_none().flex().items_center().justify_center().rounded(6)
          .text_color(t.dim).cursor_pointer().hover((s) => s.bg(t.hover).text_color(t.blocked))
          .tooltip("Archive this space (restore it from the archive button)")
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
          .on_click((_e, cx) => { cx.stop_propagation(); this.del(row, cx); })
          .child(svg("mods/workspace-chrome/archive.svg").size(14)));
    };
    return v_flex().id("space-history").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(380).min_h(0).p(8).bg(t.popover).text_color(t.foreground)
      .child(h_flex().px(10).h(28).flex_none().items_center()
        .child(div().flex_1().text_size(11.5).text_color(t.dim).child("Previous spaces, most recent first"))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(v_flex().id("space-history-list").flex_1().min_h(0).overflow_y_scrollbar().gap(2)
        .when(rows === null, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Loading…")))
        .when(rows !== null && this.error, (el) => el.child(div().p(10).text_size(12).text_color(t.blocked).child(`Could not read tether: ${this.error}`)))
        .when(rows !== null && !this.error && rows.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("No closed spaces with a linked session yet. Spaces you archived are in the archive button (Backups).")))
        .children((rows ?? []).map(line)));
  }
}
