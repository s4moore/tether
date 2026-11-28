// Browse what tether has saved about your environment (the context store, `tether context`): search it, read it by topic,
// delete a fact. Opened from "Browse context" in the space menu or the button in Settings. It only calls
// `tether context list --json` and `tether context remove <topic> --line N` (see `tether --help`). Facts are edited in the
// Markdown files themselves. Esc closes.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify, openOverlay } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;
const POPUP_HEIGHT = 620;

// ---- the look shared by every tether popup: title + Esc keycap, grouped cards with hairlines ----
const header = (t, title, sub) => h_flex().px(2).pt(2).pb(2).items_center().gap(10).flex_none()
  .child(v_flex().flex_1().min_w(0).gap(1)
    .child(div().text_size(15).font_medium().text_color(t.foreground).child(title))
    .when(Boolean(sub), (el) => el.child(div().text_size(11.5).text_color(t.dim).child(sub))))
  .child(div().flex_none().h(18).px(6).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10).text_color(t.dim).child("Esc"));
const divided = (t, rows) => rows.flatMap((r, i) => (i ? [div().h(1).w_full().bg(t.border), r] : [r]));

export default class ContextBrowser extends View {
  init(_props, cx) {
    this.topics = null; // null: loading
    this.error = null;
    this.busy = false;
    this.confirm = null; // "topic:line" waiting for a second click
    this.text = "";
    this.handle = cx.focus_handle();
    this.input = InputState.new({ value: "", placeholder: "Search saved facts" });
    this.input.on("change", (_e, cx) => { this.text = this.input.value(); cx.notify(); });
    this.input.focus();
    cx.timer.after(600, () => this.input.focus());
    this.load(cx);
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", "list", "--json"], { timeout: 15000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
        this.topics = JSON.parse(r.stdout);
        this.error = null;
      } catch (e) {
        this.topics = [];
        this.error = String(e?.message ?? e);
      }
      cx.notify();
    });
  }

  remove(topic, line, cx) {
    this.busy = true;
    this.confirm = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", "remove", topic, "--line", String(line)], { timeout: 15000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
      } catch (e) {
        notify({ title: "Could not delete the fact", body: String(e?.message ?? e).slice(0, 300), level: "error" });
      }
      this.busy = false;
      this.load(cx);
    });
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  quiet(id, label, enabled, onClick) {
    const t = theme();
    return div().id(id).flex_none().h(22).px(8).flex().items_center().rounded(6).border_1().border_color(t.border).text_size(11).text_color(enabled ? t.dim : t.border)
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.text_color(t.foreground).bg(t.hover)).on_click(onClick)).child(label);
  }

  render() {
    const t = theme();
    const idle = !this.busy;
    const words = this.text.toLowerCase().split(/\s+/).filter(Boolean);
    const matches = (topic, fact) => words.every((w) => `${topic.topic} ${topic.about} ${fact?.fact ?? ""}`.toLowerCase().includes(w));
    const shown = (this.topics ?? [])
      .map((tp) => ({ ...tp, facts: words.length ? tp.facts.filter((f) => matches(tp, f)) : tp.facts }))
      .filter((tp) => (words.length ? tp.facts.length > 0 : true));
    const total = (this.topics ?? []).reduce((n, tp) => n + tp.facts.length, 0);
    const factRow = (tp, f) => {
      const key = `${tp.topic}:${f.line}`;
      const asking = this.confirm === key;
      return h_flex().id(`cx-${key}`).px(14).py(9).gap(10).items_start().flex_none()
        .child(v_flex().flex_1().min_w(0).gap(3)
          .child(div().text_size(13).text_color(t.foreground).child(f.fact))
          .child(h_flex().gap(6).items_center()
            .child(div().text_size(11).text_color(t.dim).child([f.date, f.source ? f.source.replace(/^auto: /, "") : ""].filter(Boolean).join(" · ")))
            .when(f.auto, (el) => el.child(div().flex_none().px(6).h(16).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10).text_color(t.dim).child("added automatically")))))
        .child(asking
          ? h_flex().gap(6).flex_none()
              .child(this.quiet(`cx-keep-${key}`, "Keep", idle, (_e, cx) => { this.confirm = null; cx.notify(); }))
              .child(div().id(`cx-del-${key}`).flex_none().h(22).px(8).flex().items_center().rounded(6).bg(t.solid).text_size(11).text_color(t.solid_foreground)
                .cursor_pointer().on_click((_e, cx) => this.remove(tp.topic, f.line, cx)).child("Delete"))
          : this.quiet(`cx-ask-${key}`, "Delete", idle, (_e, cx) => { this.confirm = key; cx.notify(); }));
    };
    const topicCard = (tp) => v_flex().flex_none().gap(2)
      .child(h_flex().px(4).pt(8).pb(2).gap(8).items_baseline().flex_none()
        .child(div().text_size(12).font_medium().text_color(t.foreground).child(tp.topic))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(tp.about)))
      .child(v_flex().flex_none().rounded(10).border_1().border_color(t.border).bg(t.background)
        .children(tp.facts.length ? divided(t, tp.facts.map((f) => factRow(tp, f))) : [div().px(14).py(10).text_size(12).text_color(t.dim).child("No facts yet.")]));
    return v_flex().id("tether-context").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).min_h(0).p(12).gap(8).bg(t.popover).text_color(t.foreground)
      .child(header(t, "Context", this.topics === null ? "Loading…" : `${total} fact${total === 1 ? "" : "s"} in ${this.topics.length} topic${this.topics.length === 1 ? "" : "s"} · every agent can look these up`))
      .child(Input.new(this.input).h(34).px(10).rounded(9).border_1().border_color(t.border).bg(t.background).text_size(12.8).text_color(t.foreground))
      .child(v_flex().id("tether-context-list").flex_1().min_h(0).overflow_y_scrollbar().gap(2)
        .when(Boolean(this.error), (el) => el.child(div().p(10).text_size(12).text_color(t.blocked).child(`Could not read tether: ${this.error}`)))
        .when(this.topics !== null && !this.error && this.topics.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim)
          .child("Nothing saved yet. Right-click a space and choose “Learn from this space”, or turn on background learning in Settings.")))
        .when(this.topics !== null && this.topics.length > 0 && shown.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("No saved fact matches.")))
        .children(shown.map(topicCard)))
      .child(h_flex().flex_none().gap(8).justify_end().px(2)
        .child(this.quiet("cx-review", "Review proposed facts", true, () => { closeOverlay(props().overlay); openOverlay({ mod: "tether-context-review", kind: "popup", width: 560, height: 520, props: {} }); })));
  }
}
