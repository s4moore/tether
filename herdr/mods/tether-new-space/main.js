// New tether space: asks for a name (required, unique), then makes the space and
// starts a persistent, tether-linked agent session in it (Claude, or Copilot if that is the default_agent setting). Enter creates, Esc cancels.
// Opened by tether-spaces' "+" with props { }. Shift-click on "+" skips all this.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { run, props, closeOverlay, theme, spaces, exec, home } from "herdr";

const norm = (s) => s.trim().replace(/\s+/g, " ").toLowerCase();

export default class SpaceNew extends View {
  init(_props, cx) {
    this.text = "";
    this.closed = []; // names of closed spaces tether still knows
    this.agent = "claude"; // the default_agent setting, read below
    this.input = InputState.new({ value: "" });
    this.input.on("change", (_e, cx) => { this.text = this.input.value(); cx.notify(); });
    this.input.on("submit", () => this.submit());
    this.inputFocused = true;
    this.input.on("focus", (_e, cx) => { this.inputFocused = true; cx.notify(); });
    this.input.on("blur", (_e, cx) => { this.inputFocused = false; cx.notify(); });
    this.input.focus();
    cx.timer.after(600, () => this.input.focus());
    this.handle = cx.focus_handle();
    // Closed spaces count for uniqueness too: reopening one by name would be confusing.
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${home()}/.local/bin/tether`, ["list", "--json"]);
        this.closed = JSON.parse(r.stdout).filter((s) => s.closed).map((s) => s.name);
        cx.notify();
      } catch (_e) { /* live names are still checked */ }
    });
    cx.spawn(async (cx) => {
      try {
        const r = await exec(`${home()}/.local/bin/tether`, ["config", "get", "default_agent"]);
        const a = r.stdout.trim();
        if (a === "claude" || a === "copilot") this.agent = a;
      } catch (_e) { /* Claude, as before */ }
    });
  }

  // null when fine, else what is wrong.
  problem() {
    const n = norm(this.text);
    if (!n) return "";
    if (spaces().some((s) => norm(s.name ?? "") === n)) return `A space named “${this.text.trim()}” is already open.`;
    if (this.closed.some((c) => norm(c) === n)) return `A closed space named “${this.text.trim()}” exists. Reopen it with the history button, or pick another name.`;
    return null;
  }

  valid() {
    return norm(this.text) !== "" && this.problem() === null;
  }

  submit() {
    if (!this.valid()) return;
    const p = props() ?? {};
    closeOverlay(p.overlay);
    run("space.new", { name: this.text.trim(), argv: [`${home()}/.local/bin/tether`, this.agent] });
  }

  key(e, cx) {
    if (e.key === "escape") { closeOverlay(props().overlay); cx.stop_propagation(); }
  }

  render() {
    const t = theme();
    const p = props() ?? {};
    const problem = this.problem();
    const ok = this.valid();
    const button = (id, text, primary, enabled, action) => h_flex().id(id).h(32).px(14).items_center().rounded(8)
      .border_1().border_color(primary ? t.solid : t.input).bg(primary ? t.solid : t.raised)
      .text_color(primary ? t.solid_foreground : t.foreground).text_size(12.5).font_medium()
      .when(enabled, (el) => el.cursor_pointer().hover((s) => s.opacity(0.88)).on_click(action))
      .when(!enabled, (el) => el.opacity(0.45))
      .child(text);
    return v_flex().id("space-new").track_focus(this.handle).on_key_down((e, cx) => this.key(e, cx))
      .w_full().min_h(0).p(18).bg(t.popover).text_color(t.foreground)
      .child(h_flex().items_center().child(div().flex_1().text_size(15).font_semibold().child("New space"))
        .child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(div().pt(16).pb(7).text_size(11.5).text_color(t.dim).child("Name"))
      .child(Input.new(this.input).h(36).px(10).rounded(9).border_1()
        .border_color(problem ? t.blocked : this.inputFocused ? t.accent : t.input)
        .bg(t.background).text_size(12.8).text_color(t.foreground))
      .child(div().pt(6).text_size(11.5).text_color(problem ? t.blocked : t.dim)
        .child(problem || "Use something specific to the task, so you can find it again later. You can rename it any time."))
      .child(div().pt(8).text_size(11).text_color(t.dim).child(`Starts a ${this.agent === "copilot" ? "Copilot" : "Claude"} session that tether keeps linked to this space.`))
      .child(h_flex().pt(20).gap(8).justify_end()
        .child(button("sn-cancel", "Cancel", false, true, () => closeOverlay(p.overlay)))
        .child(button("sn-create", "Create", true, ok, () => this.submit())));
  }
}
