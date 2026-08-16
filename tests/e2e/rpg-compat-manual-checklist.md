# RPG Companion Compat + LangGraph Backend — Manual Release Checklist

Manual release gate for the RPG Companion Compat extension (SillyTavern) paired with the
LangGraph RPG Engine backend. Each item maps to spec §17.4 发布验收. Work through the
preconditions once, then run the steps for every item in order. A pass requires the
expected result to hold exactly; capture a screenshot or diagnostic text beside any
fail/pass decision.

## Prerequisites

- Running backend: `python -m sillytavern_rpg_engine serve --database ./campaign.db`
  (defaults to port 8000; override with `--port` / `RPG_BACKEND_URL` if different).
- SillyTavern ≥ 1.18.0 with RPG Companion Compat enabled and **DualModel Engine disabled**.
- A connection profile (Chat Completion) pointing at `http://127.0.0.1:8000/v1` with a
  Custom Body of `{"campaign_id":"<your-campaign>"}` so every request carries a campaign.
- `SILLYTAVERN_URL` set to the running SillyTavern origin for any automated smoke step.
- A test chat (1:1, no group chat) with at least one character and a known starting state.

## §17.4 #1 — New character appears in Compat panel next turn

- (a) Preconditions: active campaign; a chat with one character; panel open.
- (b) Steps: add a second character to the chat. Send a user turn. Wait for the
  Narrator reply and the panel refresh.
- (c) Expected result: the new character appears in the RPG Companion panel (present
  characters / character list) on the next rendered turn, without manual reload.

## §17.4 #2 — New skill renders dynamic attribute control

- (a) Preconditions: same chat; entity with at least one known skill.
- (b) Steps: teach the character a new skill (e.g. "Acrobatics") in the turn text.
  Wait for the next Narrator reply that records the trait. Inspect the panel.
- (c) Expected result: a corresponding dynamic attribute control (skill row with
  editable value) renders in the panel for the new skill.

## §17.4 #3 — Restart backend + SillyTavern → state matches

- (a) Preconditions: chat with known entities, skills, and at least one past turn.
- (b) Steps: note a few visible state values. Stop the backend and SillyTavern, start
  both again with the same `./campaign.db`. Open the same chat.
- (c) Expected result: panel state equals the noted values; no drift, no missing
  entities/skills after restart.

## §17.4 #6 — Swipe branches do not cross-pollute

- (a) Preconditions: a chat where two different swipes produce different states
  (e.g. branch A gains a skill, branch B changes a stat).
- (b) Steps: generate reply, swipe to branch B, confirm branch B state renders. Swipe
  back to branch A.
- (c) Expected result: branch A still shows branch A state; branch B state never leaks
  into branch A (and vice versa).

## §17.4 #9 — Tracker parse failure keeps stale banner; backend unchanged

- (a) Preconditions: a valid tracker already rendered; backend serving the campaign.
- (b) Steps: force a Narrator reply whose tracker JSON is malformed (e.g. truncated or
  wrong schema). Observe the panel.
- (c) Expected result: the panel shows a stale/parse-failure banner, keeps the last
  valid state visible, and the backend database is not modified by the failed parse.

## §17.4 #11 — Dice, combat, and resource changes have immutable audit

- (a) Preconditions: a D&D-enabled campaign; a combat or dice roll occurring.
- (b) Steps: roll dice and change a resource (HP/mana) via combat. Inspect the backend
  audit (e.g. `flush-audit --database ./campaign.db --output ./audit.jsonl`).
- (c) Expected result: a read-only audit trail records the roll and resource change;
  historical audit rows are never overwritten.

## §17.4 #12 — Long campaign still retrieves early high-importance events

- (a) Preconditions: a long-running campaign (or a simulation of one) with an early,
  high-importance event.
- (b) Steps: query memory for that early event (via panel search or API).
- (c) Expected result: the early high-importance event is still retrievable even after
  many turns; prompting does not lose it.

## §17.4 #13 — New campaign defaults to Narrative, no D&D dice/resource settlement

- (a) Preconditions: no campaign with the target id yet.
- (b) Steps: create a new campaign (first request with a fresh `campaign_id`). Send a
  turn, roll narrative and watch the panel.
- (c) Expected result: the campaign behaves as Narrative — no D&D dice rolls, no
  resource/combat settlement panel appears.

## §17.4 #14 — Same API, independent per-campaign D&D toggle

- (a) Preconditions: two campaigns on the same backend URL.
- (b) Steps: enable D&D on campaign A, keep campaign B Narrative. Alternate requests
  between the two.
- (c) Expected result: campaign A shows combat/roll behavior; campaign B stays
  Narrative; toggling one campaign never affects the other.

## §17.4 #15 — Disabling D&D keeps sheet/records, hides D&D panel

- (a) Preconditions: a D&D campaign with a character sheet and combat records.
- (b) Steps: disable D&D for that campaign. Open the panel.
- (c) Expected result: the character sheet and combat records remain recoverable in the
  backend, while RPG Companion auto-hides the D&D panel UI.

## Post-check

- [ ] All §17.4 items above verified on the release build.
- [ ] No console errors mentioning DualModel on any page load during the above.
- [ ] Capture screenshots/diagnostics for any fail to attach to the release note.

Optional automated smoke (same preconditions): run the backend, set `SILLYTAVERN_URL`,
then `npm run test:e2e -- tests/e2e/rpg-compat-smoke.spec.js`.