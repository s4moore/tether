// Review the facts tether proposes for its context store (`tether context`): from "Learn from this space" in the space
// menu (opened with props { learn: { id, name } }: it shows the progress, then the facts), from "Review learned facts", or
// for facts learned in the background when "context_auto" is on. Nothing is saved until you press Accept. It only calls
// `tether context learn --id <space> --json`, `proposals --json`, `accept --only N [--into <topic>]`, `reject --only N`, `recent --json` and `undo --only N`
// (see `tether --help`). Facts saved without review ("context_auto_accept") are listed below the proposals, each with Undo.
// Esc closes.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;

// ---- the look shared by every tether popup: title + Esc keycap, grouped cards with hairlines ----
const header = (t, title, sub) => h_flex().px(2).pt(2).pb(2).items_center().gap(10).flex_none()
  .child(v_flex().flex_1().min_w(0).gap(1)
    .child(div().text_size(15).font_medium().text_color(t.foreground).child(title))
    .when(Boolean(sub), (el) => el.child(div().text_size(11.5).text_color(t.dim).child(sub))))
  .child(div().flex_none().h(18).px(6).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10).text_color(t.dim).child("Esc"));
const divided = (t, rows) => rows.flatMap((r, i) => (i ? [div().h(1).w_full().bg(t.border), r] : [r]));
const POPUP_HEIGHT = 520;

export default class ContextReview extends View {
  init(_props, cx) {
    this.rows = null; // null: loading
    this.error = null;
    this.recent = [];       // facts saved without review, newest last
    this.busy = false;
    this.learning = null;   // { name, since } while tether reads a conversation
    this.note = null;       // the outcome of that, shown above the list
    this.learnError = null;
    this.handle = cx.focus_handle();
    const learn = props()?.learn;
    if (learn) {
      this.learning = { name: learn.name, since: Date.now() };
      cx.timer.every(1000, (cx) => { if (this.learning) cx.notify(); }); // the elapsed seconds
      this.learnFrom(learn, cx);
    } else {
      this.load(cx);
    }
  }

  // Read one space's conversation (one model call: usually 15 to 60 seconds), then show what it proposed.
  learnFrom(learn, cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", "learn", "--id", learn.id, "--json"], { timeout: 240000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().replace(/^tether: /, ""));
        const out = JSON.parse(r.stdout);
        this.note = out.proposed
          ? `${out.proposed} new fact${out.proposed === 1 ? "" : "s"} found in “${learn.name}”`
          : `Nothing new worth keeping in “${learn.name}”`;
      } catch (e) {
        this.learnError = String(e?.message ?? e).slice(0, 400);
      }
      this.learning = null;
      this.load(cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", "proposals", "--json"], { timeout: 15000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
        this.rows = JSON.parse(r.stdout);
        this.error = null;
        const a = await exec(TETHER(), ["context", "recent", "--json"], { timeout: 15000 });
        this.recent = a.code === 0 ? JSON.parse(a.stdout) : [];
      } catch (e) {
        this.rows = [];
        this.error = String(e?.message ?? e);
      }
      cx.notify();
    });
  }

  // Accept or reject one proposal (its number), or all of them. The numbers change after each decision, so the list is reloaded.
  decide(accept, n, cx, into) {
    this.busy = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", accept ? "accept" : "reject", ...(n ? ["--only", String(n)] : []), ...(into ? ["--into", into] : [])], { timeout: 30000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
        if (accept) notify({ title: "Saved to your context", body: r.stdout.trim().split("\n")[0].slice(0, 200), level: "success" });
      } catch (e) {
        notify({ title: "Could not update the proposals", body: String(e?.message ?? e).slice(0, 300), level: "error" });
      }
      this.busy = false;
      this.load(cx);
    });
  }

  undo(n, cx) {
    this.busy = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const r = await exec(TETHER(), ["context", "undo", "--only", String(n)], { timeout: 30000 });
        if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
      } catch (e) {
        notify({ title: "Could not undo", body: String(e?.message ?? e).slice(0, 300), level: "error" });
      }
      this.busy = false;
      this.load(cx);
    });
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  button(id, label, kind, enabled, onClick) {
    const t = theme();
    const primary = kind === "primary";
    return h_flex().id(id).h(26).px(12).items_center().justify_center().rounded(8).border_1().text_size(12).font_medium().flex_none()
      .border_color(primary && enabled ? t.solid : t.border).bg(primary && enabled ? t.solid : t.background)
      .text_color(primary && enabled ? t.solid_foreground : (enabled ? t.foreground : t.dim))
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.opacity(0.85)).on_click(onClick))
      .child(label);
  }

  render() {
    const t = theme();
    const rows = this.rows;
    const idle = !this.busy;
    const have = Boolean(rows && rows.length > 0);
    const learning = this.learning;
    const secs = learning ? Math.floor((Date.now() - learning.since) / 1000) : 0;
    const line = (r) => v_flex().id(`cr-${r.n}`).px(14).py(11).gap(7).flex_none()
      .child(h_flex().gap(6).items_center()
        .child(div().flex_none().px(7).h(18).flex().items_center().rounded(5).bg(t.hover).text_size(10.5).text_color(t.foreground).child(r.topic))
        .when(r.new_topic, (el) => el.child(div().flex_none().px(7).h(18).flex().items_center().rounded(5).border_1().border_color(t.border).text_size(10.5).text_color(t.dim).child("new topic")))
        .when(Boolean(r.similar), (el) => el.child(div().flex_none().px(7).h(18).flex().items_center().rounded(5).border_1().border_color(t.blocked).text_size(10.5).text_color(t.blocked).child(`similar to ${r.similar}`))))
      .child(div().text_size(13).text_color(t.foreground).child(r.fact))
      .when(r.new_topic && Boolean(r.about), (el) => el.child(div().text_size(11.5).text_color(t.dim).child(r.about)))
      .child(h_flex().gap(8).items_center()
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(r.source ? `From ${r.source}` : ""))
        .child(this.button(`cr-no-${r.n}`, "Reject", "plain", idle, (_e, cx) => this.decide(false, r.n, cx)))
        .when(Boolean(r.similar), (el) => el.child(this.button(`cr-into-${r.n}`, `File under ${r.similar}`, "plain", idle, (_e, cx) => this.decide(true, r.n, cx, r.similar))))
        .child(this.button(`cr-yes-${r.n}`, "Accept", "primary", idle, (_e, cx) => this.decide(true, r.n, cx))));
    const auto = (r) => h_flex().id(`cr-auto-${r.n}`).px(14).py(9).gap(10).items_center().flex_none()
      .child(div().flex_none().px(7).h(18).flex().items_center().rounded(5).bg(t.hover).text_size(10.5).text_color(t.foreground).child(r.topic))
      .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(t.foreground).child(r.fact))
      .child(this.button(`cr-undo-${r.n}`, "Undo", "plain", idle, (_e, cx) => this.undo(r.n, cx)));
    const recent = [...(this.recent ?? [])].reverse();
    return v_flex().id("context-review").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).min_h(0).p(12).gap(8).bg(t.popover).text_color(t.foreground)
      .child(header(t, "Facts to review", have && !learning ? `${rows.length} proposed · nothing is saved until you accept` : "Proposed for your context store"))
      .child(v_flex().id("context-review-list").flex_1().min_h(0).overflow_y_scrollbar().gap(8)
        .when(Boolean(learning), (el) => el.child(v_flex().p(14).gap(6).flex_none().rounded(10).border_1().border_color(t.border).bg(t.background)
          .child(div().text_size(13).font_medium().text_color(t.foreground).child(`Reading “${learning.name}”…  ${secs}s`))
          .child(div().text_size(11.5).text_color(t.dim).child("Asking your learning agent for facts worth remembering from this conversation. This usually takes 15 to 60 seconds. Nothing is saved until you accept it."))))
        .when(!learning && Boolean(this.learnError), (el) => el.child(div().p(10).text_size(12).text_color(t.blocked).child(`Could not learn from this space: ${this.learnError}`)))
        .when(!learning && Boolean(this.note), (el) => el.child(div().px(4).text_size(12.5).font_medium().text_color(t.foreground).child(this.note)))
        .when(rows === null && !learning, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Loading…")))
        .when(rows !== null && Boolean(this.error), (el) => el.child(div().p(10).text_size(12).text_color(t.blocked).child(`Could not read tether: ${this.error}`)))
        .when(rows !== null && !learning && !this.error && !have && recent.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim)
          .child("Nothing to review. Right-click a space and choose “Learn from this space”, or turn on background learning in Settings.")))
        .when(have, (el) => el.child(v_flex().flex_none().rounded(10).border_1().border_color(t.border).bg(t.background).children(divided(t, rows.map(line)))))
        .when(recent.length > 0 && !learning, (el) => el
          .child(div().px(4).pt(6).flex_none().text_size(11).font_medium().text_color(t.dim).child("Added automatically, without review"))
          .child(v_flex().flex_none().rounded(10).border_1().border_color(t.border).bg(t.background).children(divided(t, recent.map(auto))))))
      .when(have && !learning, (el) => el.child(h_flex().flex_none().gap(8).justify_end().px(2)
        .child(this.button("cr-reject-all", "Reject all", "plain", idle, (_e, cx) => this.decide(false, null, cx)))
        .child(this.button("cr-accept-all", "Accept all", "primary", idle, (_e, cx) => this.decide(true, null, cx)))));
  }
}
