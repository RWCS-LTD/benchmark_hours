# CLAUDE.md — Developer Reference for `benchmark_hours`

Developer-facing tracking doc. End-user documentation lives in `docs/auditor_guide.md`.

## What this app is

A Streamlit app (`seasonal_aggregator.py`, single file, ~2.6k lines) for auditing winter vehicle operating hours against contract benchmarks. Three tabs:

1. **Entry & Calculate** — enter circuits → calculate operating hours → save audit report → save to season cache.
2. **Cache Viewer & Analytics** — filter/aggregate the season cache, manage route benchmark overrides, edit/delete records, download per-form HTML reports and CSVs, view overclaim reports.
3. **Auditor Guide** — renders `docs/auditor_guide.md` pulled live from GitHub.

## Repos & data flow

| Repo | Visibility | Role |
|------|-----------|------|
| `RWCS-LTD/benchmark_hours` | **Public** | App code, `docs/auditor_guide.md`, example benchmarks. Streamlit Cloud deploys from `main`. |
| `RWCS-LTD/bh-data` | **Private** | `data/season_cache.json` (audit records), `data/benchmarks.json` (route overrides). Written by the app via the GitHub Contents API. |

Data paths are configured via `st.secrets["github"]`:

```toml
[github]
token     = "ghp_…"        # PAT with contents:write on bh-data
repo      = "RWCS-LTD/benchmark_hours"     # code repo (used for auditor guide fetch)
data_repo = "RWCS-LTD/bh-data"             # data repo (cache + benchmarks)
branch    = "main"
data_path = "data/season_cache.json"
```

`[benchmarks]` in secrets overrides `benchmarks.json` for Streamlit Cloud deploys (keeps contract values out of the public repo).

## Golden rule: widget-bound session_state

Streamlit raises `StreamlitAPIException` when you write to a session_state key bound to a widget **after** that widget has been instantiated in the same run.

Every key below is widget-bound. **Writes to these keys must happen either (a) inside an `on_click` callback, or (b) at the top of the script before any widget renders.** Not inside an inline `if st.button(…):` block that runs after the widget itself.

| Key pattern | Widget type | Where it's rendered |
|-------------|-------------|---------------------|
| `sa_patrol`, `sa_unit`, `sa_primary_unit` | `text_input` | Event Header |
| `sa_is_spare`, `sa_refuel_cb`, `sa_continues` | `checkbox` | Header / End-of-event |
| `sa_unit_type`, `sa_time_mode` | `selectbox` / `radio` | Header / Circuits |
| `sa_start_date` | `date_input` | Header |
| `sa_refuel_min` | `number_input` | End-of-event |
| `sa_auditor` | `text_input` | Download report section |
| `sa_sh_{id}`, `sa_sm_{id}`, `sa_eh_{id}`, `sa_em_{id}` | `number_input` (H/M mode) | Per-circuit row |
| `sa_st_{id}`, `sa_et_{id}` | `text_input` (HHMM / HH:MM modes) | Per-circuit row |
| `sa_rt_{id}` | `text_input` | Per-circuit row |
| `sa_tp_{id}` | `checkbox` | Per-circuit row |
| `sa_del_pw_tbl`, `sa_f_*` (no `sa_f_id`), `sa_dup_replace_pw_{scope}`, `sa_view`, `sa_bm_new_*`, `sa_dl_sort_*`, `sa_tab2_auditor` | various | Analytics tab / duplicate-cascade flow |
| `sa_patrol` | `selectbox` (was `text_input` pre-Fix 5) | Event Header — Patrol # dropdown constrained to `PATROL_OPTIONS` + empty placeholder |
| `bme_{route}` | `number_input` | Manage Route Benchmarks expander |

Non-widget session_state (safe to write anywhere):

- `sa_circuits`, `sa_circuit_counter`, `sa_prev_time_mode`, `sa_calc_results`, `sa_conflict_state`, `sa_chain_cache`, `sa_analytics_view`, `sa_cache_data`, `sa_benchmarks`, `sa_benchmarks_sha`, `sa_benchmarks_loaded`, `sa_pending_delete`, `sa_pending_delete_confirmed`, `sa_editing_record_id`, `sa_just_loaded`, `sa_guide_content`, `sa_dup_replace_armed_target`.

> **`sa_analytics_view` is a paired-lifecycle cache with `sa_chain_cache`.** Both are keyed by `_get_chain_cache_key(records)` and must be invalidated together. If you set one to `None`, set the other too — clearing only one will leave a stale view that no longer matches the chain data and produces silent inconsistencies in the Analytics tab. Existing pair sites: top-of-script init, `_do_save_push`, Refresh Cache button, delete flow, rescan-conflicts, normalize-patrol.

## Button inventory

| Tab | Key | Pattern | Safe? |
|-----|-----|---------|-------|
| Entry | `sa_cancel_edit` | inline, non-widget writes | ✅ |
| Entry | `sa_rm_{id}` (🗑) | inline, pops only row being removed | ✅ |
| Entry | ➕ Add Circuit | inline, non-widget writes | ✅ |
| Entry | `sa_calc` (▶ Calculate) | inline, non-widget writes | ✅ |
| Entry | Save to Cache / Save Changes | inline, no widget writes | ✅ |
| Entry | Cancel / Save Anyway / Confirm & Save (conflict flow) | inline, sets `sa_conflict_state=None` only on success | ✅ |
| Entry | `sa_dl_btn` (⬇ HTML Report) | `download_button` | ✅ |
| Entry | `sa_reset` (🔄 New Form) | **`on_click=_clear_form_state`** — MUST stay a callback, writes widget-bound keys | ✅ |
| Analytics | `sa_refresh` | inline, non-widget only | ✅ |
| Analytics | `sa_csv_dl`, `sa_dl_rec_{id}`, `sa_oc_csv` | `download_button` | ✅ |
| Analytics | `sa_bm_add`, `sa_bm_save` | inline, non-widget only | ✅ |
| Analytics | `sa_rowedit_btn` (✏️ Edit) | **`on_click=_do_load_edit_from_selection`** — calls `_clear_form_state()` first (Frankenstein prevention), then hydrates from selected record. Writes widget-bound keys. | ✅ |
| Analytics | `sa_tbl_select` (st.dataframe single-row selection), `sa_del_btn_tbl`, `sa_del_confirm_tbl`, `sa_del_cancel1_tbl`, `sa_del_yes_tbl`, `sa_del_cancel2_tbl` | inline, non-widget only | ✅ |
| Entry (duplicate conflict, primary row) | `sa_dup_cancel`, `sa_dup_accept_both` | inline, non-widget only | ✅ |
| Entry (duplicate conflict, destructive expander) | `sa_dup_replace_arm_{scope}`, `sa_dup_replace_final_{scope}`, `sa_dup_replace_cancel_{scope}` — record-scoped | inline, non-widget writes only (`sa_dup_replace_armed_target` is non-widget) | ✅ |
| Analytics (Conflicts & Flags) | `sa_rescan_btn`, `sa_normalize_patrol_btn` | inline, non-widget only | ✅ |
| Guide | `sa_guide_refresh` | inline, non-widget only | ✅ |

## GitHub write protocol (`push_cache`)

`push_cache(config, mutator, commit_message)` reads the current cache, applies `mutator(list[record]) -> list[record]`, and PUTs. On `409` (concurrent write), it re-reads and re-applies the mutator before retrying.

**Always supply a mutator** — never a pre-built records list with a pre-fetched SHA. A pre-built list corrupts:

- **Edits:** re-append on 409 after re-read → duplicate record.
- **Deletes:** blind `append(records[-1])` on 409 → un-deletes the target.

Current mutators:

- Save (clean / edit): `_upsert_mutator(pending, edit_id)` → filters out `edit_id` if present, appends `pending`.
- Save (conflict-flagged): same mutator with `pending["conflict_status"]` pre-set.
- Delete: inline `lambda recs: [r for r in recs if r.get("id") != del_id]`.
- Benchmark overrides use a separate `save_benchmarks()` (single-object PUT, no mutator needed).

`save_benchmarks(config, data, sha)` mirrors `push_cache`'s retry shape — a single PUT, with a one-shot 409 re-read+retry via the side-effect-free `_get_benchmarks_sha()` helper (raw GET, returns SHA only, no `st.error`). No mutator: this is a single-object PUT and last-writer-wins is the documented semantic. The retry helper returns `None` on any error (404/network/parse), which the caller treats as "file absent, attempt create" — acceptable trade-off for a config file. The Save Overrides button must call `st.toast(...)` + `st.rerun()` on success (not `st.success(...)`, which is wiped by the rerun before it renders).

Conflict-flow save buttons must only clear `sa_conflict_state` and `st.rerun()` when `new_sha is not None`. On failure, `push_cache` has already rendered `st.error(...)`; leaving the conflict UI up lets the auditor retry without re-entering the form.

## Session-state lifecycle snippets

**Top-of-script init** (lines ~915–940): defaults for every non-widget session key and `sa_time_mode`. Widget defaults come from their widget declarations.

**Reset (New Form, `_do_reset_form`)**: increments counter, replaces `sa_circuits`, clears every header/refuel/continues widget key, resets non-widget state. Preserves `sa_time_mode`.

**Load for Edit (`_do_load_edit`)**: hydrates widget keys from saved record; assigns fresh circuit IDs so keys never collide with stale widgets.

## Local dev quickstart

```bash
git clone https://github.com/RWCS-LTD/benchmark_hours.git
cd benchmark_hours
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp benchmarks.example.json benchmarks.json    # optional — overridden by [benchmarks] secret
mkdir -p .streamlit
# populate .streamlit/secrets.toml per the secrets contract above (gitignored)
streamlit run seasonal_aggregator.py
```

Static checks before commit:

```bash
python3 -m py_compile seasonal_aggregator.py
python3 -c "import ast; ast.parse(open('seasonal_aggregator.py').read())"
grep -n 'st.session_state\["sa_' seasonal_aggregator.py   # every widget-bound write
grep -n 'push_cache(' seasonal_aggregator.py              # every call site must pass a mutator
```

## Smoke-test checklist (after deploy)

Run through every button post-merge — session-state regressions are invisible to unit tests:

1. Entry → Calculate → Save to Cache → success toast; Analytics shows new row.
2. Calculate a duplicate → red "duplicate" prompt → Cancel closes cleanly.
3. Calculate a same-day-no-overlap → Confirm & Save → success; row appears.
4. 🔄 New Form → header fields clear, circuit row reset to `00:00`, continues-to-next-form checkbox unchecked, time-format radio preserved.
5. Analytics → Load for Editing → Entry populated → Save Changes replaces cleanly (no duplicate).
6. Analytics → Delete flow (password `benchmark`) → record removed (no un-delete on 409).
7. Manage Route Benchmarks → Add → Save Overrides → confirm in `bh-data/data/benchmarks.json`.
8. Guide tab → Refresh Guide → markdown re-renders.

## Known gotchas

- **Setting a widget-bound key after render = crash.** Always use `on_click` callbacks.
- **`push_cache` must be called with a mutator**, never a pre-built list.
- **Conflict-branch saves** must only clear `sa_conflict_state` on success.
- **Per-circuit widget keys use row IDs**, not indices. `sa_circuit_counter` must only ever increment — never reset or decrement — so IDs stay unique across resets.
- **`sa_time_mode` radio switches** trigger a one-shot sync from canonical circuit dict → widget keys in the top-of-tab mode-change block. Don't skip this; switching modes without the sync silently zeros times.
