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
| ⚠️ **Duplicate accepted — coexists with id …** | You chose **Accept Both Entries** on a duplicate detection. The other record's short ID is included so you can cross-reference it. |
| ⚠️ **Replaced existing record(s): …** | You chose **Replace Existing Entry** on a duplicate detection. The replaced record's short ID is retained for audit history. |

Capped gaps and new winter events affect the operating hours total. An overlap flag means the data needs to be verified before the result can be relied on.

---

## When a Duplicate or Overlap is Detected

When you save a form, the tool checks the cache for conflicts with any record that shares the same unit and date. Three outcomes are possible, each with its own prompt:

### 🔴 Duplicate — same unit, same day, matching circuit start times

Shows each existing record in short form (ID · date · unit · patrol · circuits · hours) with a **Show full JSON** expander. You have three choices:

| Button | Effect |
|--------|--------|
| **← Cancel (keep existing)** | No save. The new form is discarded from the conflict queue. |
| **✅ Accept Both Entries** | Both the existing record and the new record are retained in the cache. The new record is flagged `duplicate_confirmed` and an anomaly is injected naming the other record's short ID. Use this when both forms are legitimate partial audits that should roll up together in season totals. |
| **🔁 Replace Existing Entry** | Password-protected (same deletion password). The conflicting record(s) are removed and the new record takes their place. The new record is flagged `duplicate_replaced` and names the replaced ID(s) in its anomaly list and in the commit message. Use this when the new form is the more complete record and supersedes the old one. |

### 🟡 Time overlap — circuit windows intersect (not identical)

| Button | Effect |
|--------|--------|
| **← Cancel** | No save. |
| **⚠️ Save Anyway + Flag as Overlap** | Both records kept; new record flagged `overlap_confirmed`. The auditor is accepting responsibility for the potential double-count. |

### ℹ️ Same unit/day, no time overlap

Common for fragmented contractor forms. Two buttons — **Cancel** or **Confirm & Save** (flags as `multiple_same_day`).

Flagged records appear in the **Conflicts & Flags** view in the Cache Viewer, and any injected anomaly strings also appear in the **Anomaly Log** view — both with a legend explaining every flag type.

---

## Cache Viewer — Finding and Deleting Records

The Cache Viewer (**📊 Cache Viewer & Analytics** tab) shows every saved record with filters for Patrol, Unit, Route, and date range. Two features help locate records when duplicates or conflicts need reconciling:

- **ID column in the Submissions Table.** Every row displays the record's short ID (first 8 chars of the UUID). The full ID is used when looking a record up by the identifier shown in a conflict prompt.
- **Find by ID filter.** A text box under the main filters accepts a full UUID or any prefix (e.g. `50cb1eb1`). Matching is case-insensitive.

### Deleting a record

Each row in the Submissions Table has its own **🗑** button at the far right. The delete flow is password-protected (same `benchmark` password as the other destructive actions) and requires two confirmations.

Workflow:

1. Switch the view radio to **Submissions Table**.
2. Find the row you want to delete (use the filters and the **Find by ID** box as needed).
3. Click the row's **🗑** button. A warning panel appears at the top of the view showing the record's unit / routes / date / short ID.
4. Enter the deletion password and click **Confirm**.
5. Click **✅ Yes, Delete** on the final confirmation screen. Cancel is available at every step.

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
| 2026-04 | Cache Viewer: **ID column** added to the Submissions Table (first 8 chars of each record's UUID). **Find by ID** text filter added under the main filter row — accepts full UUID or any prefix, case-insensitive. |
| 2026-04 | Submissions Table re-rendered with a **🗑 button on every row** (password-protected delete, same `benchmark` password). The old "Delete a Record" expander and the dropdown-picker delete panel were removed — delete is now directly on each row. |
| 2026-04 | "Continues to next form" checkbox added — defers end-of-event refuel to the continuation form. Audit report shows a banner and explicit deferred-refuel line when checked. Overclaim Report simplified: dollar-rate columns removed, excess hours only. Chain-level refuel calculation updated to handle continues flag correctly. |
| 2026-04 | Time entry format toggle added: choose between HHMM, HH:MM, or separate H/M boxes. Default is HHMM. New Form button fully clears all fields. Add Circuit button reliably initialises fresh fields without requiring a browser refresh. |
| 2026-04 | Initial release — circuit entry, overnight detection, gap analysis, HTML audit report download. |
