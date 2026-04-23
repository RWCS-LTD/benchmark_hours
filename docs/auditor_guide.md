# Auditor Guide — Winter Equipment Benchmark Hours Calculator

---

## What This Tool Does

The **Winter Equipment Benchmark Hours Calculator** helps you figure out how many operating hours a winter vehicle is entitled to claim for a given event, based on the rules set out in the contract. You enter the circuits from the vehicle's form, and the tool applies the contract rules automatically — handling inter-circuit gaps, multi-day events, and the end-of-event refuel allowance.

The result is a verified operating hours total, plus a downloadable audit report you can save, print, or attach to your records.

---

## Quick Reference

| Item | Value |
|------|-------|
| **Gap cap (operating)** | 60 min max per inter-circuit gap |
| **New event threshold** | Gap > 3 hours between circuits |
| **Refuel allowance** | Up to 30 min per winter event |
| **Season dates** | Oct 15 – Apr 30 |

---

## Step-by-Step: Entering a Form

### Step 1 — Enter Your Circuits

Each **circuit** is one route run from start to finish. Enter them in the order they appear on the form, from first to last.

For each circuit, you enter a **start time** and an **end time**. You can choose how you prefer to type times using the **Time entry format** selector:

| Format | Example | Best for |
|--------|---------|----------|
| **HHMM** (default) | `0930`, `1415`, `0005` | Fast keyboard entry |
| **HH:MM** | `09:30`, `14:15`, `00:05` | Familiar clock format |
| **H/M Boxes** | Hour `9`, Minute `30` | Mouse-only entry |

> **Tip:** All three formats produce identical results — pick whatever feels fastest for you.

**To add more circuits:** Click **➕ Add Circuit** and fill in the new row.

**To remove a circuit:** Click the 🗑 button on that row. (You must have at least one circuit.)

**For overnight events:** If the event runs past midnight, just keep adding circuits in order — don't change any dates or settings. The tool automatically detects when the clock rolls past midnight and assigns the correct calendar date to each circuit.

---

### Step 2 — End-of-Event Allowance

The contract allows up to **30 minutes** at the end of a winter event for unloading leftover material and refuelling the vehicle.

- The checkbox is checked by default (30 min).
- If the actual refuel took less time, reduce the number in the box.
- If no refuel time applies, uncheck the box.

The tool will never count more than 30 minutes here, regardless of what you enter.

**If this form continues on the next day's form:** Check the box labelled _"This form continues on the next day's form (refuel will be counted on the next form)"_. When checked, the refuel allowance is not added to this form — it will be recorded on the continuation form instead. The audit report will show a "Continues to Next Form" notice and a deferred refuel line in the breakdown so the record is clear.

---

### Step 3 — Calculate

Click **▶ Calculate Operating Hours**. The results appear below.

---

## Time Entry Formats — Details

**HHMM format** (default): Type the hour and minute as a single 4-digit number, no colon, no spaces.

```
06:05 AM  →  0605
2:30 PM   →  1430
Midnight  →  0000
```

**HH:MM format**: Type 3 or 4 digits — no colon needed. The colon is inserted automatically when you tab to the next field.

```
6:05 AM   →  type 605 or 0605  →  displays 06:05
2:30 PM   →  type 1430         →  displays 14:30
Midnight  →  type 0000         →  displays 00:00
```

**H/M Boxes**: Two separate fields — one for the hour (0–23) and one for the minute (0–59).

If you type something the tool can't parse, a small warning appears under that field. Fix it before clicking Calculate.

---

## Understanding the Results

After calculating, you'll see several sections:

### Circuits Table
A summary of every circuit you entered — number, date, start time, end time, and how long it ran. If the event spanned midnight, each circuit shows the correct calendar date.

### Gap Analysis
The time between each pair of circuits. For each gap, the table shows:
- **Gap Duration** — total length of the gap
- **Operating** — how much of that gap counts toward billable hours
- **Non-operating** — how much is excluded per contract rules
- **Rule Applied** — which rule was used (see below)

### Anomaly Flags
If any gap triggered a contract rule (capped gap or new winter event), a coloured flag appears here explaining exactly what was detected and why.

### Calculation Breakdown
A line-by-line summary of everything that went into the final total:
- Circuit operating time
- Gap operating time (if any)
- End-of-event allowance (if included)

### Final Total
The total benchmark operating hours for this event, shown in hours and minutes, decimal hours, and total minutes.

---

## The Contract Rules Explained

The contract sets out specific rules for how inter-circuit gaps are counted. Here's what each rule means:

### Gap of 60 minutes or less — Full gap counts
If the time between the end of one circuit and the start of the next is 60 minutes or less, the entire gap counts as operating time. The vehicle is considered to still be on deployment.

### Gap between 61 and 180 minutes — Capped at 60 minutes
If the gap is longer than an hour, only the first 60 minutes counts as operating time. The remainder is classified as non-operating and excluded from the total.

> **Example:** A 90-minute gap → 60 min operating + 30 min non-operating.

### Gap over 3 hours — New winter event
A gap longer than 3 hours means the vehicle has effectively stood down and returned for a separate deployment. The circuits after the gap belong to a **new winter event** and are not counted toward the current event's total.

### End-of-event allowance
At the end of a winter event, the contract allows up to 30 minutes for the driver to unload any remaining material and refuel the vehicle. This time is added to the operating total (up to the 30-minute cap).

---

## Anomaly Flags Reference

| Flag | What it means |
|------|--------------|
| 🔴 **NEW WINTER EVENT** | Gap exceeds 3 hours — circuits after this gap belong to a separate event and are excluded from the current total |
| 🟡 **Capped at 1h** | Gap is between 61–180 min — only 60 min counts; the excess is non-operating |
| ⚠️ **Overlap** | A circuit's start time is before the previous circuit's end time — check the form for a data entry error |
| ⚠️ **Duplicate accepted — 240 min shared (08:00-12:00) with id …** | You chose **Accept Both Entries** on a duplicate detection. The message names how many minutes overlap with the counterpart and the exact time range(s) so the double-reported window is visible without opening each record. |
| ⚠️ **Time overlap — N min shared (…) with id …** | You chose **Save Anyway + Flag as Overlap**. Same shared-minute detail as above. |
| ⚠️ **Replaced existing record(s): …** | You chose **Replace Existing Entry** on a duplicate detection. The replaced record's short ID is retained for audit history. |

Capped gaps and new winter events affect the operating hours total. An overlap flag means the data needs to be verified before the result can be relied on.

---

## When a Duplicate or Overlap is Detected

When you save a form, the tool checks the cache for conflicts with any record that shares the same unit and date. Three outcomes are possible, each with its own prompt:

### 🔴 Duplicate — same unit, same day, matching circuit start times

Shows each existing record in short form (ID · date · unit · patrol · circuits · hours) with a **Show full JSON** expander. The two safe actions sit side by side; the destructive option is hidden inside a separate expander that you have to open on purpose.

**Primary row (safe actions):**

| Button | Effect |
|--------|--------|
| **← Cancel (keep existing)** | No save. The new form is discarded from the conflict queue. |
| **✅ Accept Both Entries** *(primary)* | Both records retained. The new record is flagged `duplicate_confirmed`; the existing counterpart is also re-flagged and both sides get a shared-minute anomaly (e.g. `"Duplicate accepted — 240 min shared (08:00-12:00) with id abc12345…"`). Use this for legitimate partial audits that should roll up together. |

**Destructive expander — opens on click, closed by default:**

`🗑 Destructive: delete the existing record(s) and save this one instead`

Expanding the box reveals a preview listing every record that will be permanently dropped (date, unit, patrol, id, circuit count, hours, saved timestamp), then a password field, then an arm button. Clicking the arm button transitions to a final Yes/No confirmation layer. Total four intentional clicks + password before any deletion fires. No typed input anywhere. Each conflict target gets record-scoped widget keys, so switching targets resets the password field and arm state — browser autofill cannot "carry" state between different Replace attempts.

Flow summary:

1. Click the expander to open it (click #1).
2. Review the preview block naming exactly what will be deleted.
3. Enter the deletion password (`benchmark`).
4. Click **🗑 Delete record xxxxxxxx… and save this new one** (click #2). You're now armed.
5. A red final-confirm layer appears with two buttons.
6. Click **✅ Yes, permanently delete xxxxxxxx…** (click #3) — this commits the delete + save in a single commit.
7. Or click **← No, cancel** to back out — nothing changes.

Use Replace when the new form is the complete record and the existing form(s) are known to be wrong or incomplete. Otherwise always prefer Accept Both.

### 🟡 Time overlap — circuit windows intersect (not identical)

| Button | Effect |
|--------|--------|
| **← Cancel** | No save. |
| **⚠️ Save Anyway + Flag as Overlap** | Both records kept; new record flagged `overlap_confirmed`. The auditor is accepting responsibility for the potential double-count. |

### ℹ️ Same unit/day, no time overlap

Common for fragmented contractor forms. Two buttons — **Cancel** or **Confirm & Save** (flags as `multiple_same_day`).

Flagged records appear in the **Conflicts & Flags** view in the Cache Viewer, and any injected anomaly strings also appear in the **Anomaly Log** view — both with a legend explaining every flag type.

**Both sides of a conflict are flagged.** When you save a second form that triggers a duplicate, overlap, or same-day prompt, the existing record you were conflicting with is tagged with the same flag and gets a counterpart anomaly pointing back at the new record. That way Conflicts & Flags shows both forms, not just the newer one. Records that were saved before this behavior was introduced can be retroactively tagged with the **🔍 Rescan cache for conflicts** button at the top of the Conflicts & Flags view.

### Shared-minute detection (double-reporting)

The Conflicts & Flags view has a dedicated **Overlap min (this record)** column. For every flagged record it shows the minutes of this form's operating window that are also claimed on another same-unit form — i.e. the minutes at risk of being billed twice by the contractor.

**The authoritative total is the red banner**, not the column sum. The column is a **per-record** figure — when a pair of forms overlap by 297 min, record A shows 297 and record B shows 297 (same minutes viewed from each side). Summing across rows would double-count. The banner walks each same-unit pair exactly once and reports the true unique total.

**Tolerance: 2 min.** Contiguous overlap sub-intervals of 2 minutes or less are treated as rounding/boundary artifacts and excluded from the column and the banner. This filters stray data-entry slop (e.g. one form's `10:00` end vs another's `10:01` start) without affecting real operational overlap. The tolerance is configurable in `seasonal_aggregator.py` via the `OVERLAP_TOLERANCE_MIN` constant.

**The aggregate Hours views dedupe at the strict minute level, ignoring the tolerance.** If two circuits partially overlap (08:00-10:00 and 09:30-11:00), the combined operating time counts as 08:00-11:00 = 180 min, never as 120 + 90 = 210. The Hours by Unit / Hours by Route / Hours by Patrol / Overclaim Report views all use this merged view internally. The Overclaim Report's `Excess Hrs` column reflects real double-reporting even for 1-minute overlaps — billing correctness never depends on the tolerance setting.

If you want to see the overlap list across the whole season before billing, open Conflicts & Flags and sort descending by **Overlap min (this record)**. Zero-overlap rows are still flagged (e.g. `multiple_same_day` with distinct time windows) but do not carry a double-reporting risk.

---

## Cache Viewer — Finding and Deleting Records

The Cache Viewer (**📊 Cache Viewer & Analytics** tab) shows every saved record with filters for Patrol, Unit, Route, and date range. Two features help locate records when duplicates or conflicts need reconciling:

- **ID column in the Submissions Table.** Every row displays the record's short ID (first 8 chars of the UUID). Sort the table by the ID column to group matching short IDs together if you need to locate a record by the identifier shown in a conflict prompt.

### Editing or deleting a record

The Submissions Table is a contained scrollable grid. Select a row to reveal an **Actions on selected record** panel below with two buttons: **✏️ Edit this record** and **🗑 Delete this record**. Both flows are password-protected as before (delete only — edit re-uses the Entry tab's normal save flow, which you control).

**Edit workflow:**

1. Switch the view radio to **Submissions Table**.
2. Click the row you want to edit. The Actions panel appears below the table.
3. Click **✏️ Edit this record**. The Entry tab is hydrated with the record's data (header, circuits, refuel). A toast at the top of the view confirms "Record loaded — switch to the 📝 Entry & Calculate tab".
4. Switch to the Entry tab, make changes, and click **Save Changes (Replace Record)**. The existing record is updated in place — no new record is created.

Edit safety: the callback blanks every form field before hydrating from the selected record. That way, if you had a partially-entered form in progress when you clicked Edit, you won't get a Frankenstein form mixing old and new fields.

**Delete workflow:**

1. Click the row, then click **🗑 Delete this record**.
2. Enter the deletion password (`benchmark`) and click **Confirm**.
3. Click **✅ Yes, Delete** on the final confirmation screen. Cancel is available at every step. Selecting a different row before confirming cancels the in-progress delete.

The delete commits to GitHub in a single operation and names the short ID, date, unit, and routes in the commit message for audit history.

---

## Downloading the Audit Report

Once you've calculated and reviewed the results, scroll down to the **📄 Download Audit Report** section. Fill in these four fields:

| Field | What to enter |
|-------|--------------|
| **Event Start Date** | The calendar date the event began |
| **Unit ID** | The vehicle unit number (e.g. `Unit 12`) |
| **Route ID#** | The route identifier (e.g. `R-4402`) |
| **Auditor Name** | Your full name |

All four fields are required before the download button becomes active.

Click **⬇ Download HTML Report** to save the file. The report opens in any web browser and prints cleanly — it contains:

- Event header (unit, route, date, auditor)
- Full circuit log with dates and durations
- Gap analysis table with the contract rule applied to each gap
- Anomaly flags (if any)
- Calculation breakdown
- Final operating hours total
- An auditor certification block

The report is self-contained (no internet connection needed to view it) and suitable for filing or submission.

---

## Tips & Common Questions

**What if I enter a time wrong?**
Just correct the field and click **▶ Calculate Operating Hours** again. You can recalculate as many times as you need before downloading.

**What if the event spans midnight?**
No special steps needed. Enter circuits in chronological order. When the tool sees a circuit start time that is earlier than the previous end time (e.g. circuit 3 ends at 23:50, circuit 4 starts at 00:10), it automatically advances to the next calendar day.

**Can I start a fresh calculation without refreshing the browser?**
Yes — after calculating, click **🔄 New Calculation** at the bottom of the page. This clears all circuits and all result fields so you can start over cleanly.

**What if there is only one circuit?**
That's fine. There are no gaps to analyse. The result is just the circuit duration plus any refuel allowance you've included.

**What does "non-operating time" mean?**
It's the portion of a gap that the contract does not count as billable operating time. It appears in the results for your records but does not contribute to the total.

**The time I need to enter has a leading zero — do I include it?**
Yes, for HHMM and HH:MM formats, always use two digits for both hours and minutes. `0605` for 6:05 AM, not `605`. The tool will show a warning if the format isn't quite right.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-04 | Duplicate-detection buttons added: **✅ Accept Both Entries** and **🔁 Replace Existing Entry** (password-protected) — previously the only option was Cancel. Both actions inject an anomaly string naming the counterpart record's short ID so the audit trail is preserved. New flag types `duplicate_confirmed` and `duplicate_replaced` are now listed in the Conflicts & Flags legend. |
| 2026-04 | Duplicate dialog redesigned: only **Cancel** and **Accept Both Entries** are now shown in the primary action row. The destructive Replace path moved behind an expander (`🗑 Destructive: …`) and is gated by a four-click cascade — expand → password → record-specific arm button → final Yes/No. Record-scoped widget keys so browser autofill can't carry state between different conflict targets. |
| 2026-04 | Row-select Edit: clicking a row in the Submissions Table now surfaces **✏️ Edit this record** beside the existing **🗑 Delete** button. Edit hydrates the Entry tab with the record's data via an atomic callback that blanks the form first (Frankenstein prevention). The old separate "Edit a Record" expander and its dropdown were removed. Internal refactor: `_do_reset_form`'s body was factored into a shared `_clear_form_state()` helper that both the 🔄 New Form button and the new Edit flow call. |
| 2026-04 | **Conflicts & Flags banner math fixed** — previously summed a per-record column that double-counted every pair (a 297-min overlap between two records displayed as 594 min). Banner now walks each same-unit pair once and reports the true unique total. Column renamed **Overlap min (this record)** with a caption explaining that it's per-record. Tolerance constant `OVERLAP_TOLERANCE_MIN = 2` filters sub-2-minute contiguous overlaps as rounding artifacts. Billing dedupe (`_merged_chain_windows`) is unchanged and still runs at the strict minute level. |
| 2026-04 | **Patrol # field is now a fixed dropdown** (11, 12, 13, 14, 15, 16) instead of free-form text. Eliminates the fragmentation seen in the cache (e.g. "Patrol 11" vs "11" appearing as two separate filter options). New **📋 Normalize Patrol numbers** admin button in the Conflicts & Flags view performs a one-shot cleanup of existing records by stripping any leading "Patrol" prefix. Row-select Edit hydrates Patrol # through the same normalizer so pre-migration records load without crashing the dropdown. |
| 2026-04 | Cache Viewer: **ID column** added to the Submissions Table (first 8 chars of each record's UUID). |
| 2026-04 | Conflict saves now flag **both sides** — when a duplicate, overlap, or same-day conflict is confirmed by the auditor, both the new record and the existing counterpart record receive the matching `*_confirmed` flag and an anomaly pointing back at each other. New **🔍 Rescan cache for conflicts** button in the Conflicts & Flags view walks the full cache and retroactively tags any pair that slipped through before this behavior existed. |
| 2026-04 | Submissions Table is now a contained, scrollable grid with native **single-row selection**. Click a row to select it, then use the password-protected **🗑 Delete this record** control directly below (password: `benchmark`). Supports thousands of records without flooding the page. The old "Delete a Record" expander and dropdown-picker are removed. |
| 2026-04 | "Continues to next form" checkbox added — defers end-of-event refuel to the continuation form. Audit report shows a banner and explicit deferred-refuel line when checked. Overclaim Report simplified: dollar-rate columns removed, excess hours only. Chain-level refuel calculation updated to handle continues flag correctly. |
| 2026-04 | Time entry format toggle added: choose between HHMM, HH:MM, or separate H/M boxes. Default is HHMM. New Form button fully clears all fields. Add Circuit button reliably initialises fresh fields without requiring a browser refresh. |
| 2026-04 | Initial release — circuit entry, overnight detection, gap analysis, HTML audit report download. |
