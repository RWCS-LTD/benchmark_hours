# Plan — Duplicate / Overlap Override & Discoverability

## Triggering scenario

Same vehicle, same day, appears on two separate audit forms:

- **Form A** (existing cache record, id `50cb1eb1-f926-4166-a27b-130205406a0c`): 1 circuit.
- **Form B** (new entry being saved): 10 circuits on the same unit/day; timing overlaps Form A.

Current app behavior blocks Form B:

- If any circuit start time matches Form A → `check_conflicts` returns `duplicate` → only a **← Cancel (keep existing)** button is shown (`seasonal_aggregator.py:1590-1601`).
- If no start-time match but windows intersect → `overlap` → user can already **Save Anyway + Flag as Overlap** (`seasonal_aggregator.py:1603-1632`).

The auditor's only recourse today is to manually delete Form A via the Analytics tab, then re-save Form B. That delete flow has three usability gaps:

1. **No ID in the displayed dataframe** (`seasonal_aggregator.py:1791-1810`). The `_id` is in the frame but never rendered, so the ID shown in the conflict JSON cannot be cross-referenced to a row.
2. **Delete dropdown label lacks ID** (`seasonal_aggregator.py:2096-2098`). Label is `"{Date} — {Unit} — {Routes} ({hrs} hrs)"`. When two same-day/same-unit records exist (the exact case that produced the conflict), both rows render with nearly identical labels.
3. **Filters can hide the target** (`seasonal_aggregator.py:1834-1844`). Patrol/unit/route/date filters narrow `fdf`; the delete dropdown reads from `fdf`, not `df`. If the auditor changed filters while investigating, the conflicting record is invisible.

## Root cause summary

Two independent bugs compounded into the user-visible problem:

- **No override path for `duplicate`** (only `overlap` and `same_day_no_overlap` offer one).
- **No way to identify a record by ID** from the dashboard, so deleting the conflict by hand is unreliable.

## Proposed changes

### 1. Duplicate branch: three buttons (Cancel / Accept Both / Replace)

File: `seasonal_aggregator.py`, block starting `seasonal_aggregator.py:1590`. Decision: these three options live **in the `duplicate` branch only**. The `overlap` branch keeps its existing single-override behavior.

Layout — three columns:

- **← Cancel (keep existing)** — unchanged.
- **✅ Accept Both Entries (Flag as Duplicate)** — sets `pending["conflict_status"] = "duplicate_confirmed"` and calls `_do_save_push` with `_upsert_mutator(pending, eid)`. Keeps both Form A and Form B. Intended use: both forms are legitimate partial audits that should roll up together in season totals.
- **🔁 Replace Existing Entry** — password-gated (reuse `benchmark` from delete flow). Mutator filters out every id in `crecs` *and* the edit target, then appends `pending`. One commit. Intended use: Form B is the more complete record and supersedes Form A. Post-save: log the replaced id(s) in the commit message so the GitHub history preserves the audit trail.

Rationale: the `duplicate` branch today has no override at all. Accept-Both gives parity with the overlap branch's non-destructive "save anyway"; Replace gives a destructive one-step path for the Form-A/Form-B supersede case. Keeping Replace out of the overlap branch prevents destructive action on weaker conflicts.

### 2. Anomaly Log + Conflicts & Flags coverage for `duplicate_confirmed`

File: `seasonal_aggregator.py:2394-2417`.

Today:

- **Conflicts & Flags** view (`seasonal_aggregator.py:2404-2417`) filters on `Flags != "clean"` — `duplicate_confirmed` will appear automatically since `conflict_status` is rendered into the `Flags` column at `:1806`. But the caption legend at `:2414-2417` lists only `overlap_confirmed`, `multiple_same_day`, `spare_overlap`. **Add `duplicate_confirmed` to the legend** so auditors reviewing flags know what it means.
- **Anomaly Log** view (`seasonal_aggregator.py:2394-2402`) filters on `Anomalies != ""`. A `duplicate_confirmed` record won't appear there unless it also carries an anomaly string. **Change:** when Accept-Both or Replace is used, inject a synthetic anomaly into `pending["anomalies"]` before save:
  - Accept-Both: `"⚠️ Duplicate accepted — coexists with id {short_id_of_A}"` (one entry per record in `crecs`).
  - Replace: `"⚠️ Replaced existing record(s): {short_ids_of_A}"`.

This makes the record show up in **both** the Anomaly Log (for auditor spot-check) and the Conflicts & Flags view (for rollup reports). It also creates a human-readable pointer back to the other side of the conflict without requiring the auditor to cross-reference ids.

Add a new Conflicts & Flags legend line:

```
`duplicate_confirmed` = same unit/date/start-time as another form, both retained by auditor decision;
`duplicate_replaced` = would fire on the new record if we tracked replacements (future).
```

### 3. Surface record identity in the conflict prompt

Inside `seasonal_aggregator.py:1596-1598`, before `st.json(rec)`:

```python
st.markdown(
    f"**Existing entry:** `{rec['id'][:8]}…` — "
    f"{rec.get('start_date','?')} / Unit {rec.get('unit_number','?')} / "
    f"Patrol {rec.get('patrol_number','?')} / "
    f"{len(rec.get('circuits', []))} circuit(s) / "
    f"{round(rec.get('total_operating_minutes',0)/60, 2)} hrs"
)
```

Same treatment for the overlap and same_day_no_overlap branches. The raw `st.json(rec)` can stay below for full detail.

### 4. Make records findable by ID on the dashboard

Two complementary changes in the Analytics tab (`seasonal_aggregator.py:1791-1810` + filter block + delete expander):

- Add an **`ID`** column to `rows` showing `r.get("id","")[:8] + "…"` (short form), and include it in the rendered dataframe.
- Add a **"Find by ID"** text filter in the filter row — matches full or partial UUID, case-insensitive.
- Update the delete dropdown label (`seasonal_aggregator.py:2097`) to append `— id:{_id[:8]}…` so two same-day same-unit records are distinguishable.

### 5. Delete-flow robustness

In the delete expander (`seasonal_aggregator.py:2091-...`), change the source from `fdf` to `df` *only for the dropdown options*, and show a caption noting that filters don't apply to delete targets. This prevents the "I filtered it out and now I can't see it" footgun.

### 6. Per-row Delete on the Submissions Table (password-protected)

File: `seasonal_aggregator.py:1926-1930` (Submissions Table view).

Streamlit's native `st.dataframe` does not support button-in-cell. Two viable implementations; recommending (B).

**(A) `st.data_editor` with a checkbox column.** Cons: adds edit semantics users don't want, schema policing overhead.

**(B) Expand-per-row delete panel — recommended.** Render the Submissions Table as today (read-only), then below it add a compact "Delete a record" control that already exists (`:2091-...`) but hoist it to be visible under the Submissions Table view, not only inside an expander. Key change: surface one password field + one confirm button per-session (not per-row), with the row selector next to it. This keeps the single-button flow the user asked for without Streamlit cell-button contortions.

Concretely under the Submissions Table render:

1. Add a small `🗑 Delete selected row` control bar: a single selectbox of row labels (`"{Date} — Unit {Unit} — Patrol {Patrol} — id:{_id[:8]}…"`), a password text_input, and a Delete button. Options sourced from `df` (not `fdf`) so filters don't hide targets.
2. First click stages the delete into `sa_pending_delete` with `sa_pending_delete_confirmed=False`, reruns.
3. Second view shows a one-line confirm with **Confirm Delete** (checks pw == `benchmark`) and **Cancel**.
4. On success: reuse the existing `_delete_mutator` pattern from `:2152`, clear caches, rerun.

This reuses the existing delete mutator and password literal, so security posture is unchanged. Only the placement moves from an expander buried at the bottom of the Analytics tab to a prominent control under the main table.

Remove the old expander at `:2091-...` once the new control is live, to avoid two delete paths.

## Out of scope (explicit non-goals)

- **Automatic merge of two forms into one record** — circuit lists merging, refuel minute reconciliation, anomaly re-derivation. Too much implicit logic; better handled as a manual two-step (delete A, save B with all circuits) or via #2 above.
- **Soft-delete / undo** — current architecture is straight overwrite to GitHub JSON. Not adding history here.
- **Cross-form chain linking** — `sa_chain_cache` already handles event continuity via `continues_to_next_form`; that's a separate concern.

## Ordering

1. #3 (identity in prompt) — ~5 lines, zero risk, immediate relief.
2. #4 (ID column + find-by-ID filter) — ~20 lines, changes dataframe contract.
3. #1 — split into two sub-ships:
   - 1a. **Accept Both Entries** button — mirrors overlap `Save Anyway` pattern, non-destructive, ~15 lines.
   - 1b. **Replace Existing** button — destructive, password-gated, ~25 lines. Ship after 1a has been smoke-tested.
4. #2 (Anomaly Log + legend coverage for `duplicate_confirmed`) — small, ride-along with 1a.
5. #5 (delete dropdown uses `df`) — 2 lines.
6. #6 (per-row delete under Submissions Table) — largest UI change, ship last and remove the old expander.

## Smoke tests to add to `CLAUDE.md` checklist

- Duplicate detected → Cancel → no save.
- Duplicate detected → Accept Both → both records present; new record flagged `duplicate_confirmed`; Anomaly Log shows the injected `"⚠️ Duplicate accepted — coexists with id …"` string; Conflicts & Flags shows the new row.
- Duplicate detected → Replace Existing (wrong pw) → rejected, nothing saved.
- Duplicate detected → Replace Existing (correct pw) → Form A gone, Form B saved, one commit, commit message names the replaced id.
- Conflicts & Flags legend lists `duplicate_confirmed`.
- Analytics → filter by partial ID → correct row highlighted.
- Per-row delete under Submissions Table: wrong pw → rejected. Correct pw → record gone, one commit. Options list is sourced from unfiltered `df` (stress test with a date filter that excludes the target).
- Old Analytics-tab delete expander no longer renders.

## Resolved decisions

- Replace button: **duplicate branch only** (not overlap).
- Accept Both: **new button in duplicate branch** (parity with overlap's Save Anyway, but named for the auditor's mental model).
- `duplicate_confirmed` must surface in **both** Anomaly Log and Conflicts & Flags views via injected anomaly string + legend update.
- Per-row delete lives **directly under the Submissions Table**, password-gated, replacing the buried expander.
