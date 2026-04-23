# Auditor Guide — Winter Equipment Benchmark Hours Calculator

A practical manual for auditing winter vehicle operating hours against contract benchmarks. This guide walks you through every screen and every decision you'll face while using the tool.

---

## What This Tool Does

For each vehicle-form submitted by the contractor, the tool:

1. Calculates the **correct benchmark operating hours** that can be claimed, applying the contract's gap and refuel rules automatically.
2. Saves the audited record to a central cache so your work is never lost and so totals roll up per unit, per route, and per patrol.
3. Detects conflicts between forms — duplicates, time overlaps, cross-unit route sharing — and surfaces them for your review.
4. Produces downloadable HTML audit reports suitable for filing and submission.

All billing totals dedupe at the minute level: if two forms claim the same operating minute for the same vehicle, it counts once.

---

## Quick Reference — Contract Rules at a Glance

| Rule | Effect |
|------|--------|
| **Inter-circuit gap ≤ 60 min** | Full gap counts as operating time. |
| **Inter-circuit gap 61–180 min** | Only first 60 min counts; excess is non-operating. |
| **Inter-circuit gap > 180 min** | Gap ends the current winter event; circuits after the gap belong to a new event. |
| **End-of-event allowance** | Up to 30 min per winter event for unloading and refuel. |
| **Season dates** | Oct 15 – Apr 30. Forms outside this range save but are tagged `Out of Season`. |
| **Patrols** | Valid numbers: 11, 12, 13, 14, 15, 16. |

---

## Tab 1 — Entry & Calculate

Use this tab to enter one contractor form, calculate the audited hours, and save the record to the cache.

### Event Header

Fill in the event identification:

| Field | What to enter |
|-------|---------------|
| **Patrol #** | Pick from the dropdown — 11, 12, 13, 14, 15, or 16. |
| **Unit #** | The vehicle unit number exactly as written on the form. |
| **Unit Type** | Plow / Spreader / Combination Unit / Tow Plow / etc. |
| **Event Start Date** | The calendar date the event began. |

If the vehicle is running as a spare: check **Is this a spare unit?** and enter the **primary unit number** it's replacing. Hours will be attributed to the primary unit's benchmark.

### Entering Circuits

Each **circuit** is one run of one route, start to finish. Enter circuits in the order they appear on the form.

For each circuit: pick the **Route**, enter **Start time**, enter **End time**, and tick **Tow Plow** if the vehicle used one.

Choose your preferred time entry style under **Time entry format**:

| Format | Type | Looks like |
|--------|------|-----------|
| **HHMM** (default) | 4 digits, no colon | `0930`, `1415`, `0005` |
| **HH:MM** | 3 or 4 digits, colon auto-inserted | `605` → `06:05` |
| **H/M Boxes** | Two separate number boxes | Hour `9`, Minute `30` |

All three formats produce identical results — pick whichever is fastest for you.

- **➕ Add Circuit** — add another circuit row.
- **🗑** on a row — remove that circuit (at least one must remain).
- **Overnight events** — just keep adding circuits. When a start time is earlier than the previous end time, the tool automatically advances to the next calendar day.

### End-of-Event Allowance (Refuel)

The contract allows up to 30 minutes at the end of an event for unloading and refuelling. The checkbox is ticked by default (30 min). Reduce the number or uncheck the box if less applies.

**If this form continues on the next day's form**, check **"This form continues on the next day's form"**. When checked the refuel isn't added to this form — it's deferred to the continuation form. The audit report will show a clear "Continues to next form" notice.

### Calculate

Click **▶ Calculate Operating Hours**. Results appear below:

- **Circuits table** — one row per circuit with date, times, and duration.
- **Gap analysis** — one row per inter-circuit gap showing how much counts as operating and which contract rule was applied.
- **Anomaly flags** — coloured boxes if any gap triggered a rule.
- **Calculation breakdown** — line-by-line math.
- **Final total** — audited operating hours in h/m, decimal, and minutes.

Recalculate as many times as you need.

### Saving to the Cache

Click **💾 Save This Entry to Cache** to commit the record. One of three things happens:

1. **Clean save.** A green toast confirms; the record is written.
2. **A conflict dialog appears.** See [When a conflict is detected](#when-a-conflict-is-detected) below.
3. **An error appears.** The record is not saved. Scroll up to read the error; most common cause is a missing required field.

### Downloading the HTML Audit Report

Scroll to **📄 Download Audit Report**, enter your **Auditor Name**, and click **⬇ Download HTML Report**. The report is self-contained: opens offline, prints cleanly, and contains the full event header, circuit log, gap analysis, anomaly flags, calculation breakdown, final total, and an auditor certification block.

### Starting Fresh

Click **🔄 New Form** to clear every field and start a brand new form. The new form gets a fresh set of widgets — nothing from the previous form leaks through.

---

## When a Conflict Is Detected

Before saving, the tool checks whether the same unit is already recorded on the same date. Three possible dialogs:

### 🔴 Duplicate — matching circuit start times

Shown when the new form shares at least one circuit start time with an existing same-unit/same-day record. Two safe options appear side by side; a destructive option is hidden in a separate expander.

**Primary actions (pick one):**

| Button | What happens |
|--------|---------------|
| **← Cancel (keep existing)** | The new form is not saved. You go back to the entry screen. |
| **✅ Accept Both Entries** *(primary)* | Both records are kept. Each side is flagged `duplicate_confirmed` and gets an anomaly naming the counterpart's short ID and the exact shared minutes (e.g. `"Duplicate accepted — 240 min shared (08:00-12:00) with id abc12345…"`). |

**Use Accept Both** when both forms are legitimate partial audits that should roll up together. Billing dedupes at the minute level automatically — no minute is counted twice.

**Destructive option — only if you mean it:**

The expander titled `🗑 Destructive: delete the existing record(s) and save this one instead` is collapsed by default. Opening it shows you every record that would be permanently removed, then requires four intentional clicks plus a password before any delete runs:

1. Click the expander title (click 1).
2. Read the preview block — it names each record being dropped by date, unit, patrol, ID, circuit count, hours, and saved timestamp.
3. Enter the deletion password (`benchmark`).
4. Click **🗑 Delete record xxxxxxxx… and save this new one** (click 2). This arms the action but does not yet delete.
5. A red final-confirm layer appears.
6. Click **✅ Yes, permanently delete xxxxxxxx…** (click 3) — **this is the click that actually deletes**. Or **← No, cancel** to back out cleanly.

Use Replace only when the new form is the complete record and the existing ones are known to be wrong or incomplete. Accept Both is safer in almost all cases.

### 🟡 Time Overlap — windows intersect but start times differ

| Button | What happens |
|--------|---------------|
| **← Cancel** | The new form is not saved. |
| **⚠️ Save Anyway + Flag as Overlap** | Both records are kept. Each side is flagged `overlap_confirmed` and gets an anomaly naming the exact overlapping minutes. |

### ℹ️ Same Unit/Day, No Time Overlap

Common when a contractor submits multiple short forms for one event.

| Button | What happens |
|--------|---------------|
| **← Cancel** | The new form is not saved. |
| **✅ Confirm & Save** | Both records are kept. Each side is flagged `multiple_same_day`. |

**Every conflict flags both sides.** After save, the existing record and the new record both appear in Conflicts & Flags with matching flags and cross-reference anomalies. Nothing is hidden.

---

## Tab 2 — Cache Viewer & Analytics

Use this tab to browse the saved audit records, see totals, investigate flags, and edit or delete records.

### Filters

Four filters across the top: **Patrol #**, **Unit #**, **Route #**, and a **From/To date range**. All filter boxes are populated from the data in the cache — so as new units or routes appear, they show up automatically.

### Views

Pick a view from the radio above the table:

| View | What it shows |
|------|--------------|
| **Submissions Table** | Every record, one row per form. Click a row to Edit or Delete it. |
| **Hours by Unit** | Audited operating hours per vehicle across all their forms, deduped per event. |
| **Hours by Route** | Circuit run-time and attributed operating hours per route, plus benchmark progress. |
| **Hours by Patrol** | Roll-up by patrol — total hours, unit count, form count. |
| **Anomaly Log** | Every record with a non-empty anomaly string. One-stop list of things worth a second look. |
| **Conflicts & Flags** | Every flagged record with its `conflict_status`, plus the per-record Overlap min column and the authoritative pairwise overlap banner. See [Conflicts & Flags](#conflicts--flags-view) below. |
| **Timeline** | Chronological event chains per unit. One continuous winter event may span multiple forms. |
| **Overclaim Report** | Per-chain audit: how many minutes the contractor claimed vs how many the audit recognises. Non-zero `Excess Hrs` means the contractor's claim is higher than audited. |

### Finding a Record

The Submissions Table shows an **ID column** (first 8 characters of the record's unique ID). When a conflict dialog mentions an ID, you can sort the table by ID or scroll to find it. The filters narrow which rows display; the delete panel always operates off the full cache so filters can never hide a record from you when you want to remove it.

### Editing a Record

1. Switch to the **Submissions Table** view.
2. Click the row you want to edit. An **Actions on selected record** panel appears below.
3. Click **✏️ Edit this record**. The Entry tab is populated with that record's data.
4. Switch to **📝 Entry & Calculate**, make your changes, and click **💾 Save Changes (Replace Record)**.

The loaded form is fully reset before being populated, so nothing leaks in from whatever you had on screen before.

### Deleting a Record

1. Select the row in the Submissions Table.
2. Click **🗑 Delete this record**.
3. Enter the deletion password (`benchmark`) and click **Confirm**.
4. Click **✅ Yes, Delete** on the final confirmation.

Cancel is available at every step. Selecting a different row before confirming cancels the in-progress delete.

### Conflicts & Flags View

This is where you reconcile duplicate or overlapping records before submitting billing.

**The red banner at the top of the view** reports the authoritative total:
- `N pair(s) with impossible overlap — X.XX hrs total unique overlap across the flagged set.`

**The Overlap min (this record) column** shows, for each flagged row, how many of that record's own operating minutes overlap with any other same-unit form.

**Don't sum the column — it double-counts.** If two forms overlap by 297 min, record A shows 297 and record B shows 297 (the same 297 minutes viewed from each side). The banner walks each pair exactly once and is the only correct aggregate figure.

**Tolerance: 2 minutes.** Contiguous overlap intervals of 2 minutes or less are treated as rounding/boundary artifacts (e.g. one form's `10:00` end vs another's `10:01` start). They don't count toward Overlap min or the banner. Real operational overlap is always much larger than 2 min. **Billing dedupe is stricter** — every shared minute is removed from the total, regardless of tolerance — so billing correctness never depends on this setting.

**Flag types you'll see:**

| Flag | Meaning |
|------|---------|
| `duplicate_confirmed` | Same unit / day / matching circuit start times. Auditor chose to retain both forms. |
| `duplicate_replaced` | Same unit / day / matching start times. Auditor chose to delete the prior record and save the new one. |
| `overlap_confirmed` | Same unit / day / time windows intersect (no exact start match). Auditor confirmed the overlap. |
| `multiple_same_day` | Same unit / day / no time overlap — two separate forms for fragmented audits. |
| `spare_overlap` | A spare unit and its primary were both recorded on the same date. |

**Anomalies column** names the exact overlapping time ranges with each counterpart — e.g. `Duplicate accepted — 110 min shared (06:24-08:14) with id b52d89fc…`. You can click through to each referenced counterpart via the ID.

### Cross-Unit Route Coverage (Anomaly Only)

When the **same route** is run by **different units** with overlapping time windows, the tool flags it as an informational anomaly: `ℹ️ Cross-unit route coverage — also run by unit S1495E (id abc12345…, 90 min shared on WK3 10:00-11:30)`.

This is not a billing issue — different vehicles are billed separately. But two vehicles on the same route at the same time may indicate a data-entry error, double-dispatch, or a takeover that's worth investigating. Cross-unit anomalies show up in the **Anomaly Log** view (they don't alter `conflict_status`).

### Admin Controls in Conflicts & Flags

Two buttons at the top of the view:

- **🔍 Rescan cache for conflicts** — walks every record pair and ensures both sides of any same-unit/same-day conflict (and every cross-unit route overlap) carries the matching flag + anomaly. Safe to run any time. Only dirty records change; the commit message names how many.
- **📋 Normalize Patrol numbers** — one-shot cleanup of legacy records that have "Patrol 11" or "Patrol  12" in the patrol field. Strips any leading `Patrol` prefix and collapses whitespace so every record has a bare number (`11`, `12`, etc.) that matches the dropdown. Safe to run any time; idempotent. After running, the Patrol filter on this tab will show one entry per patrol instead of several fragments.

### Manage Route Benchmarks

Expand the `📋 Manage Route Benchmarks` panel to review contract benchmark hours per route. Routes found in the cache are listed with their current values; you can override a route only if the contract value was amended. Click **💾 Save Overrides to GitHub** to commit.

---

## Tab 3 — Auditor Guide

The manual you're reading now, rendered inside the app. Use **🔄 Refresh Guide** if you want to pull the latest version from the repository without restarting the app.

---

## Downloading the Audit Report

On Tab 1, after calculating, scroll to **📄 Download Audit Report**:

1. Enter your full name in **Auditor Name**.
2. Click **⬇ Download HTML Report**.

The report is a self-contained HTML file — double-click it to open in any browser, print it cleanly for filing, or attach it to the ministry submission. It contains the event header, the circuit log with calendar dates, the gap analysis table with the contract rule applied to each gap, any anomaly flags, the full calculation breakdown, the final operating hours total, and an auditor certification block.

On Tab 2's **Submissions Table** view, you can download the report for any previously-saved record — expand **📄 Download Per-Form Audit Reports**, pick the record, and click its Download button.

---

## Anomaly Flag Quick Reference

You'll see these in the per-form results (Tab 1) and in the Anomaly Log and Conflicts & Flags views (Tab 2):

| Flag | Meaning |
|------|---------|
| 🔴 **NEW WINTER EVENT** | Gap > 3 hours — circuits after it are a separate event and excluded from this total. |
| 🟡 **Capped at 1h** | Gap 61–180 min — only 60 min counts as operating. |
| ⚠️ **Overlap within a form** | A circuit's start is before the prior circuit's end. Check for data-entry error. |
| ⚠️ **Duplicate accepted — N min shared (HH:MM-HH:MM) with id …** | `duplicate_confirmed`. Both forms retained. |
| ⚠️ **Time overlap — N min shared (…) with id …** | `overlap_confirmed`. Both forms retained. |
| ⚠️ **Replaced existing record(s): …** | `duplicate_replaced`. New form replaced the named prior record(s). |
| ℹ️ **Multiple forms same unit/day — see id …** | `multiple_same_day`. Two legitimate fragmented forms. |
| ℹ️ **Cross-unit route coverage — also run by unit … (…)** | Same route, different units, overlapping time. Data quality check, not a billing issue. |

---

## Tips & Common Questions

**What if I enter a time wrong?**
Correct the field and click **▶ Calculate Operating Hours** again. Recalculate as many times as you need before saving or downloading.

**What if the event spans midnight?**
No special steps needed. Enter circuits in chronological order. When a start time is earlier than the previous end time (e.g. 23:50 end → 00:10 start), the tool automatically advances to the next calendar day.

**What if there is only one circuit?**
That's fine — no gaps to analyse. The result is just the circuit duration plus any refuel allowance.

**What does "non-operating time" mean?**
The portion of a gap the contract does not count as billable. It's shown for transparency but doesn't contribute to the total.

**The time I need to enter has a leading zero — do I include it?**
Yes, for HHMM and HH:MM formats always use two digits for each part. `0605` for 6:05 AM, not `605`. The tool warns if the format is off.

**I see "Patrol 11" and "11" as separate options in the Patrol filter. What do I do?**
Open the **Conflicts & Flags** view and click **📋 Normalize Patrol numbers**. The legacy prefix is stripped from every affected record in one commit. The filter will then consolidate into one entry per patrol.

**The Submissions Table doesn't show a record I just saved. Where is it?**
Check the date-range filter at the top of the Cache Viewer — if the "To" date is older than the form you just saved, the record is hidden. Widen the filter.

**Can I undo a delete?**
Not in the app. Deletes commit immediately to GitHub. The commit history preserves the record forever, so it can be recovered by a developer if needed. The password gate and multi-step confirmation exist specifically to prevent accidental deletes.

**Does the app double-count when two forms overlap?**
No. Every minute of billable time is counted at most once in Hours by Unit / Route / Patrol and Overclaim Report. The red banner in Conflicts & Flags tells you exactly how many minutes were at risk so you can audit the contractor's original claim.

**Who should I contact if something looks wrong?**
Capture a screenshot of what you're seeing and contact your system administrator. Include the record ID (first 8 characters) where possible.
