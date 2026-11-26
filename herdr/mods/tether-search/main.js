// Search conversations: describe what you remember, find the conversation. Opened by the magnifier in the
// tether-spaces header. It only calls `tether search` (see `tether --help`). Two ways to search, and either
// can be used straight away (the free one does not have to come first):
//   Search words    the words anywhere in the conversations (instant, free)
//   Search with AI  the same words, then one Claude call ranks the best candidates from short digests; with no
//                   word match it looks at the most recent conversations, so a description works too
// There is deliberately no "read the best results in full" step here: it costs the most tokens and the AI search
// is good enough. (`tether search --deep` still exists for the command line.)
// Once a words search has run, the AI button shows the token estimate for the next AI search.
// Open puts the conversation back (a new tab; a deleted or closed space is recreated). Esc closes.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { props, closeOverlay, theme, exec, home, notify, run } from "herdr";

const TETHER = () => `${home()}/.local/bin/tether`;
const POPUP_HEIGHT = 620;

function ago(iso) {
  const secs = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(secs)) return "";
  if (secs < 90) return "just now";
  for (const [unit, n] of [["d", 86400], ["h", 3600], ["min", 60]]) {
    if (secs >= n) return `${Math.floor(secs / n)} ${unit} ago`;
  }
  return "just now";
}
const tokens = (n) => (n >= 10000 ? `${Math.round(n / 1000)}K` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}`);
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

const SHORT_TERM = 3; // a term of up to this many characters matches only as a whole word (SHORT_TERM in tether)
const isAlnum = (c) => c !== undefined && /[a-z0-9]/.test(c);

// Split one word around the search terms (substring, ignoring case, as the search itself matches) so the part
// that matched can be drawn highlighted: "investigation" with the term "investig" gives [investig][ation].
function pieces(word, terms) {
  const low = word.toLowerCase();
  if (low.length !== word.length) return [{ text: word, hit: false }]; // a letter that changes length when lowercased
  const marks = new Array(word.length).fill(false);
  for (const term of terms) {
    if (!term) continue;
    for (let i = low.indexOf(term); i !== -1; i = low.indexOf(term, i + term.length)) {
      // The same rule as the search (_term_re in tether): a short term only counts as a whole word.
      if (term.length <= SHORT_TERM && (isAlnum(low[i - 1]) || isAlnum(low[i + term.length]))) continue;
      for (let k = i; k < i + term.length; k++) marks[k] = true;
    }
  }
  const out = [];
  for (let i = 0; i < word.length;) {
    let j = i;
    while (j < word.length && marks[j] === marks[i]) j++;
    out.push({ text: word.slice(i, j), hit: marks[i] });
    i = j;
  }
  return out;
}

export default class TetherSearch extends View {
  init(_props, cx) {
    this.text = "";
    this.scopes = { open: true, closed: true, deleted: false }; // deleted: the backup of deleted spaces (costly)
    this.rows = null; // null: nothing searched yet
    this.expanded = new Set(); // sessions whose row is opened to show all the matching text
    this.meta = null; // the last response: tier, estimate, cost_usd, corpus, model, fallback
    this.busy = null; // text while something runs
    this.secs = 0; // how long it has been running, shown so a slow step does not look stuck
    this.error = null;
    this.input = InputState.new({ value: "", placeholder: "Words you used, or describe what you remember" });
    this.input.on("change", (_e, cx) => { this.text = this.input.value(); cx.notify(); });
    this.input.on("submit", (_e, cx) => this.search(cx));
    this.input.focus();
    cx.timer.after(600, () => this.input.focus());
    cx.timer.every(1000, () => { if (this.busy) { this.secs += 1; cx.notify(); } });
    this.handle = cx.focus_handle();
  }

  scopeList() {
    return Object.keys(this.scopes).filter((k) => this.scopes[k]);
  }

  // All slow work runs inside cx.spawn and notifies through ITS cx: the cx of a click or key handler is
  // stale after an await (Herdr throws), which left the popup on "Searching…" with no results.
  go(label, work, apply, cx) {
    if (this.busy) return;
    this.busy = label;
    this.secs = 0;
    this.error = null;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        apply(await work());
      } catch (e) {
        this.error = String(e?.message ?? e).slice(0, 300);
      }
      this.busy = null;
      cx.notify();
    });
  }

  async tether(extra, timeout) {
    const r = await exec(TETHER(), ["search", this.text.trim(), "--scope", this.scopeList().join(","), "--json", ...extra], { timeout });
    if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim() || `exit ${r.code}`);
    return JSON.parse(r.stdout);
  }

  search(cx) {
    if (!this.text.trim() || this.scopeList().length === 0) return;
    this.go("Searching…", () => this.tether([], 60000), (out) => { this.rows = out.results; this.meta = out; this.expanded = new Set(); }, cx);
  }

  // Text drawn word by word so the parts that matched the search can be highlighted (gpui-kit has no inline spans).
  marked(text, terms) {
    const t = theme();
    return h_flex().flex_wrap().gap_x(4).gap_y(1).text_size(11).text_color(t.muted)
      .children(text.split(/\s+/).filter(Boolean).map((w) => {
        const ps = pieces(w, terms);
        if (ps.length === 1 && !ps[0].hit) return div().child(w);
        return h_flex().children(ps.map((p) => (p.hit
          ? div().px(1).rounded(3).bg(t.selection).text_color(t.foreground).font_semibold().child(p.text)
          : div().child(p.text))));
      }));
  }

  // The AI search needs no words search first: `tether search --rank` does the keyword step itself.
  ai(cx) {
    if (!this.text.trim() || this.scopeList().length === 0) return;
    this.go("Asking Claude…", () => this.tether(["--rank"], 300000), (out) => { this.rows = out.results; this.meta = out; this.expanded = new Set(); }, cx);
  }

  open(r, cx) {
    this.go("Opening…", async () => {
      const o = await exec(TETHER(), ["open-session", r.session, "--agent", r.agent], { timeout: 120000 });
      if (o.code !== 0) throw new Error((o.stderr || o.stdout).trim() || `exit ${o.code}`);
      return o;
    }, (o) => {
      if (r.state === "open") run("space.select", { id: r.space_id });
      notify({ title: r.state === "deleted" ? "Space restored" : "Opened", body: o.stdout.trim().split("\n").pop().slice(0, 200), level: "success" });
      closeOverlay(props().overlay);
    }, cx);
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  button(id, label, kind, enabled, onClick, grow = false) {
    const t = theme();
    const primary = kind === "primary";
    return h_flex().id(id).h(grow ? 34 : 28).px(12).items_center().justify_center().rounded(8).border_1().text_size(12).font_medium()
      .when(grow, (el) => el.flex_1().min_w(0))
      .border_color(primary ? t.solid : t.input).bg(primary ? t.solid : t.raised)
      .text_color(primary ? t.solid_foreground : t.foreground)
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.opacity(0.88)).on_click(onClick))
      .when(!enabled, (el) => el.opacity(0.45))
      .child(label);
  }

  checkbox(key, label) {
    const t = theme();
    const on = this.scopes[key];
    return h_flex().id(`sc-${key}`).gap(6).items_center().cursor_pointer().on_click((_e, cx) => {
      this.scopes[key] = !on;
      cx.notify();
    })
      .child(div().size(15).flex_none().flex().items_center().justify_center().rounded(4).border_1()
        .border_color(on ? t.solid : t.input).bg(on ? t.solid : t.background).text_size(10).text_color(t.solid_foreground)
        .child(on ? "✓" : ""))
      .child(div().text_size(12).text_color(t.foreground).child(label));
  }

  render() {
    const t = theme();
    const idle = !this.busy;
    const rows = this.rows;
    const m = this.meta;
    const est = m?.estimate;
    const canSearch = idle && this.text.trim() !== "" && this.scopeList().length > 0;
    const haveRows = Boolean(rows && rows.length > 0);
    const pill = (state) => div().flex_none().px(6).py(1).rounded(5).border_1().border_color(t.border).text_size(10)
      .text_color(state === "deleted" ? t.blocked : t.dim).child(state === "deleted" ? "archived" : state);
    // The words that matched: the ones the search used, else what was typed.
    const terms = m?.terms?.length ? m.terms : this.text.toLowerCase().split(/\s+/).filter(Boolean);
    const line = (r) => {
      const expandable = Boolean(r.quote) || r.snippets?.length > 0;
      const open = expandable && this.expanded.has(r.session);
      return v_flex().id(`sr-${r.session}`).px(10).py(8).gap(2).flex_none().rounded(8)
      .hover((s) => s.bg(t.hover))
      // Click anywhere on the row except the Open button to show or hide all the matching text.
      .when(expandable, (el) => el.cursor_pointer().on_click((_e, cx) => {
        if (this.expanded.has(r.session)) this.expanded.delete(r.session); else this.expanded.add(r.session);
        cx.notify();
      }))
      .child(h_flex().gap(8).items_center()
        .child(div().flex_none().w(10).text_size(10).text_color(t.dim).child(expandable ? (open ? "▾" : "▸") : ""))
        .child(div().flex_1().min_w(0).truncate().text_size(12.5).font_medium().text_color(t.foreground)
          .child(`${r.space}${r.tab && !/^\d+$/.test(r.tab) && r.tab.toLowerCase() !== r.agent ? ` · ${r.tab}` : ""}`))
        .child(pill(r.state))
        .child(div().flex_none().w(30).text_size(11).text_color(t.dim).child(`${r.score}`))
        .child(this.button(`sr-open-${r.session}`, r.state === "deleted" ? "Restore & open" : "Open", "plain", idle, (_e, cx) => { cx.stop_propagation(); this.open(r, cx); })))
      .when(Boolean(r.why), (el) => el.child(div().text_size(11.5).text_color(t.foreground).child(r.why)))
      .when(Boolean(r.quote) && !open, (el) => el.child(div().text_size(11).text_color(t.dim).child(`“${r.quote}”`)))
      .when(!r.quote && !r.why && r.snippets?.length > 0 && !open, (el) => el.child(div().text_size(11).text_color(t.dim).truncate()
        .child(`${r.snippets[0].role === "user" ? "you" : "assistant"}: ${r.snippets[0].text}`)))
      // Opened: the quote and every snippet, wrapped, with what matched highlighted.
      .when(open, (el) => el.child(v_flex().gap(5).mt(2).pl(18)
        .children([
          ...(r.quote ? [this.marked(`“${r.quote}”`, terms)] : []),
          ...(r.snippets ?? []).map((s) => h_flex().gap(6).items_start()
            .child(div().flex_none().w(54).text_size(10.5).text_color(t.dim).child(s.role === "user" ? "you" : "assistant"))
            .child(div().flex_1().min_w(0).child(this.marked(s.text, terms)))),
        ])))
      // The first message is only a last resort: when the row has no explanation, quote or snippet of its own.
      .when(!r.quote && !r.why && !(r.snippets?.length > 0), (el) => el.child(div().text_size(11.5).text_color(t.muted).truncate()
        .child(r.title ? `“${r.title}”` : "(no first message found)")))
      .child(div().text_size(10.5).text_color(t.dim).child(`${r.agent === "claude" ? "Claude" : "Copilot"} · ${ago(r.last_used)}${r.via_backup ? " · from the backup" : ""}`));
    };
    const status = () => {
      if (this.busy) return `${this.busy} ${this.secs}s${this.secs >= 30 ? " (taking longer than usual; Esc closes the popup, the work stops being shown)" : ""}`;
      if (!m) return "Search words finds your exact words anywhere in a conversation (instant, free). Search with AI also understands a description of what you remember, and uses one Claude call.";
      const base = `${plural(rows.length, "match")} in ${plural(m.corpus, "conversation")}`;
      const words = m.terms?.length ? ` for ${m.terms.join(", ")}` : "";
      if (m.tier === 1) return rows.length ? `${base}${words}. Search with AI ranks these with Claude and can match a description too.`
        : `Nothing in ${plural(m.corpus, "conversation")} has those words${words ? ` (${m.terms.join(", ")})` : ""}. Search with AI can still look at your most recent conversations.`;
      return `${m.fallback ? `Nothing had those words, so Claude looked at your ${plural(est?.rank_candidates ?? 0, "most recent conversation")}: ` : ""}${base}, ranked by ${m.model}${m.cost_usd ? ` · cost $${m.cost_usd.toFixed(3)}` : ""}.`;
    };
    return v_flex().id("tether-search").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().h(POPUP_HEIGHT).p(16).gap(10).bg(t.popover).text_color(t.foreground)
      .child(h_flex().items_center().child(div().flex_1().text_size(15).font_semibold().child("Search conversations"))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(Input.new(this.input).h(36).px(10).rounded(9).border_1().border_color(t.input).bg(t.background).text_size(12.8).text_color(t.foreground))
      .child(h_flex().gap(16).items_center()
        .child(div().text_size(11.5).text_color(t.dim).child("Search in"))
        .child(this.checkbox("open", "Open spaces"))
        .child(this.checkbox("closed", "Closed spaces"))
        .child(this.checkbox("deleted", "Archived spaces")))
      .when(this.scopes.deleted, (el) => el.child(div().text_size(11).text_color(t.blocked).child(
        "Archived spaces are searched in the backup. Their conversations are never in an open or closed space, so the AI steps may read more text: higher token cost.")))
      .child(h_flex().gap(8)
        .child(this.button("ts-search", "Search words", "primary", canSearch, (_e, cx) => this.search(cx), true))
        .child(this.button("ts-ai", est && m?.tier === 1 ? `Search with AI · ~${tokens(est.rank_tokens)} tokens` : "Search with AI", "plain", canSearch, (_e, cx) => this.ai(cx), true)))
      .child(div().text_size(11.5).text_color(this.busy ? t.foreground : t.dim).child(status()))
      .when(this.error, (el) => el.child(div().text_size(12).text_color(t.blocked).child(this.error)))
      .child(v_flex().id("ts-list").flex_1().min_h(0).overflow_y_scrollbar().gap(1).rounded(8).border_1().border_color(t.border).py(4)
        .when(rows === null, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Results appear here.")))
        .when(rows !== null && rows.length === 0, (el) => el.child(div().p(10).text_size(12).text_color(t.dim).child("Nothing matched those words.")))
        .children((rows ?? []).map(line)))
      .child(div().text_size(11).text_color(t.dim).child(
        "Search with AI sends short digests of your conversations to Claude, never tool output and never a whole conversation. Estimates are rough (about 4 characters a token)."));
  }
}
