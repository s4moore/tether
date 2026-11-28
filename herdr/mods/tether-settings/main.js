// tether settings: the defaults kept in tether's config.json, as switches and choices. Opened by the gear in the
// tether-spaces header. It only calls `tether config --json` (what to show) and `tether config set <key> <value>`
// (see `tether --help`). A setting that an environment variable overrides is shown as such and cannot be changed
// here. Changes apply to the next agent you start or the next background run. Esc closes.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify, openOverlay, pickFolder } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;
const POPUP_HEIGHT = 640;
const ISSUES_URL = "https://github.com/s4moore/tether/issues/new?template=usage-summary.md";
// A small button beside a group's title: where that group's related window opens from.
const ACTIONS = {
  Context: { text: "Browse saved facts", open: { mod: "tether-context", kind: "popup", width: 560, height: 620 } },
  Diagnostics: { text: "Share a usage summary…", open: { mod: "tether-settings", kind: "popup", width: 520, height: 640, props: { mode: "share" } } },
};
const label = { claude: "Claude", copilot: "Copilot", haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };

// ---- the look shared by every tether popup: title + Esc keycap, grouped cards with hairlines, switches, segmented choices ----
const header = (t, title, sub) => h_flex().px(6).pt(2).pb(4).items_center().gap(10).flex_none()
  .child(v_flex().flex_1().min_w(0).gap(1)
    .child(div().text_size(15).font_medium().text_color(t.foreground).child(title))
    .when(Boolean(sub), (el) => el.child(div().text_size(11.5).text_color(t.dim).child(sub))))
  .child(div().flex_none().h(18).px(6).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10).text_color(t.dim).child("Esc"));
const section = (t, text) => div().px(6).pt(10).pb(2).flex_none().text_size(11).font_medium().text_color(t.dim).child(text);
const card = (t, rows) => v_flex().flex_none().rounded(10).border_1().border_color(t.border).bg(t.background)
  .children(rows.flatMap((r, i) => (i ? [div().h(1).w_full().bg(t.border), r] : [r])));

export default class Settings extends View {
  init(_props, cx) {
    this.shareMode = props()?.mode === "share"; // the "Share a usage summary" view of this popup
    this.summary = null; // share view: the text of the file that would be written
    this.saved = null;
    this.rows = null; // null: loading
    this.error = null;
    this.busy = false;
    this.handle = cx.focus_handle();
    if (this.shareMode) this.loadSummary(cx);
    else this.load(cx);
  }

  // Share view: show exactly what `tether stats --export` would write, before anything is saved.
  loadSummary(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["stats", "--export", "--json"], { timeout: 30000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
        this.summary = r.stdout.trim();
        this.error = null;
      } catch (e) {
        this.summary = "";
        this.error = String(e?.message ?? e);
      }
      cx.notify();
    });
  }

  // Save the summary in a folder you choose, then show it in Finder. Nothing is sent anywhere.
  saveSummary(cx) {
    if (this.busy) return;
    cx.spawn(async (cx) => {
      try {
        const folder = await pickFolder({ prompt: "Save summary here" }).catch(() => null);
        if (!folder) return;
        this.busy = true;
        cx.notify();
        const r = await exec(TETHER(), ["stats", "--export", "-y", "-o", folder], { timeout: 30000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
        const m = r.stdout.match(/^wrote (.+)$/m);
        this.saved = m ? m[1] : folder;
        if (m) exec("/usr/bin/open", ["-R", m[1]]).catch(() => {});
        notify({ title: "Summary saved", body: "Nothing was sent. Attach the file to an issue if you want to share it.", level: "success" });
      } catch (e) {
        this.error = String(e?.message ?? e);
      }
      this.busy = false;
      cx.notify();
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["config", "--json"], { timeout: 15000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
        this.rows = JSON.parse(r.stdout);
        this.error = null;
      } catch (e) {
        this.rows = [];
        this.error = String(e?.message ?? e);
      }
      cx.notify();
    });
  }

  set(key, value, cx) {
    this.busy = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["config", "set", key, value], { timeout: 15000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
      } catch (e) {
        notify({ title: "Could not change the setting", body: String(e?.message ?? e).slice(0, 300), level: "error" });
      }
      this.busy = false;
      this.load(cx);
    });
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  switch(id, on, enabled, onClick) {
    const t = theme();
    return div().id(id).w(38).h(22).flex_none().rounded(11).p(2).flex().items_center()
      .bg(on ? t.solid : t.border).when(on, (el) => el.justify_end())
      .when(enabled, (el) => el.cursor_pointer().on_click(onClick)).when(!enabled, (el) => el.opacity(0.45))
      .child(div().size(18).rounded(9).bg(t.background));
  }

  segmented(key, choices, value, enabled, cx) {
    const t = theme();
    return h_flex().flex_none().p(2).gap(2).rounded(8).bg(t.hover)
      .children(choices.map((c) => div().id(`st-${key}-${c}`).h(24).px(10).flex().items_center().justify_center().rounded(6).text_size(12)
        .bg(c === value ? t.background : t.hover).text_color(c === value ? t.foreground : t.dim).when(c === value, (el) => el.font_medium())
        .when(enabled && c !== value, (el) => el.cursor_pointer().hover((s) => s.text_color(t.foreground)).on_click((_e, cx) => this.set(key, c, cx)))
        .when(!enabled, (el) => el.opacity(0.6))
        .child(label[c] ?? c)));
  }

  renderShare() {
    const t = theme();
    const idle = !this.busy;
    const button = (id, text, primary, enabled, onClick) => div().id(id).flex_none().h(28).px(12).flex().items_center().justify_center().rounded(8).border_1()
      .border_color(primary && enabled ? t.solid : t.border).bg(primary && enabled ? t.solid : t.background).text_size(12).font_medium()
      .text_color(primary && enabled ? t.solid_foreground : (enabled ? t.foreground : t.dim))
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.opacity(0.85)).on_click(onClick)).child(text);
    const lines = (this.summary ?? "").split("\n").map((l) => div().text_size(11).text_color(t.foreground).child(l.replace(/^ +/, (m) => "\u00a0".repeat(m.length)) || "\u00a0"));
    return v_flex().id("tether-share").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).min_h(0).p(12).gap(8).bg(t.popover).text_color(t.foreground)
      .child(header(t, "Share a usage summary", "Totals and versions only · nothing is sent"))
      .child(div().px(6).text_size(12).text_color(t.dim).child("This is everything the file would contain: how often each feature ran, how long it took, how many learned facts you accepted, and version numbers. It has no conversation text, no facts, no search words, no names and no paths."))
      .child(v_flex().id("tether-share-text").flex_1().min_h(0).overflow_y_scrollbar().p(12).rounded(10).border_1().border_color(t.border).bg(t.background)
        .when(this.summary === null, (el) => el.child(div().text_size(12).text_color(t.dim).child("Preparing the summary…")))
        .when(Boolean(this.error), (el) => el.child(div().text_size(12).text_color(t.blocked).child(this.error)))
        .children(lines))
      .when(Boolean(this.saved), (el) => el.child(div().px(6).text_size(11.5).text_color(t.dim).child(`Saved: ${this.saved}`)))
      .child(h_flex().flex_none().gap(8).justify_end()
        .child(button("sh-issue", "Open the issue page", false, true, () => exec("/usr/bin/open", [ISSUES_URL]).catch(() => {})))
        .child(button("sh-save", "Save…", true, Boolean(this.summary) && idle, (_e, cx) => this.saveSummary(cx))));
  }

  render() {
    if (this.shareMode) return this.renderShare();
    const t = theme();
    const rows = this.rows;
    const idle = !this.busy;
    const line = (r) => {
      const editable = r.editable && r.source !== "env" && idle;
      const control = r.type === "bool"
        ? this.switch(`st-${r.key}`, Boolean(r.value), editable, (_e, cx) => this.set(r.key, r.value ? "off" : "on", cx))
        : r.type === "choice" ? this.segmented(r.key, r.choices, r.value, editable) : null;
      return h_flex().id(`st-row-${r.key}`).px(14).py(11).gap(14).items_center()
        .child(v_flex().flex_1().min_w(0).gap(3)
          .child(h_flex().gap(8).items_center()
            .child(div().text_size(13).text_color(t.foreground).child(r.label))
            .when(Boolean(r.privacy), (el) => el.child(div().flex_none().px(6).h(17).flex().items_center().rounded(5).border_1().border_color(t.blocked)
              .text_size(10).text_color(t.blocked).child(`sends text to ${label[(rows ?? []).find((x) => x.key === "context_agent")?.value] ?? "the agent"}`))))
          .child(div().text_size(11.5).text_color(t.dim).child(r.help))
          .when(r.type === "path", (el) => el.child(div().truncate().text_size(11.5).text_color(t.foreground).child(r.value ?? "(off)")))
          .when(r.source === "env", (el) => el.child(div().text_size(11).text_color(t.dim).child(`Set by ${r.env} in your environment: unset it to change this here.`)))
          .when(r.source === "config" && r.type !== "path", (el) => el.child(
            div().id(`st-reset-${r.key}`).text_size(11).text_color(t.dim).cursor_pointer().hover((s) => s.text_color(t.foreground))
              .on_click((_e, cx) => this.set(r.key, "default", cx)).child("Reset to default"))))
        .when(Boolean(control), (el) => el.child(control));
    };
    const groups = [];
    for (const r of rows ?? []) {
      let g = groups.find((x) => x.name === r.group);
      if (!g) groups.push((g = { name: r.group, rows: [] }));
      g.rows.push(r);
    }
    return v_flex().id("tether-settings").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).min_h(0).p(12).gap(2).bg(t.popover).text_color(t.foreground)
      .child(header(t, "Settings", "Applies to the next agent you start"))
      .child(v_flex().id("tether-settings-list").flex_1().min_h(0).overflow_y_scrollbar().pb(6)
        .when(rows === null, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Loading…")))
        .when(rows !== null && Boolean(this.error), (el) => el.child(div().p(10).text_size(12).text_color(t.blocked).child(`Could not read tether: ${this.error}`)))
        .children(groups.flatMap((g) => {
          const action = ACTIONS[g.name];
          return [action ? h_flex().items_center().flex_none().child(div().flex_1().child(section(t, g.name)))
            .child(div().id(`st-action-${g.name}`).flex_none().mr(6).mt(8).mb(4).h(22).px(8).flex().items_center().rounded(6).border_1().border_color(t.border).text_size(11).text_color(t.dim)
              .cursor_pointer().hover((s) => s.text_color(t.foreground).bg(t.hover))
              .on_click(() => { closeOverlay(props().overlay); openOverlay(action.open); }).child(action.text))
            : section(t, g.name), card(t, g.rows.map(line))];
        })));
  }
}
