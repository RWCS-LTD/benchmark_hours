"""
Seasonal Contract Aggregator
Winter Vehicle Operating Hours — Cache & Analytics Tool

Stores every form entry to a GitHub-backed JSON cache, enabling
per-unit / per-route / per-patrol auditing across the full season.
"""

import os
import re
import streamlit as st
import pandas as pd
import json
import base64
import binascii
import gzip
import uuid
import hashlib
import requests
from datetime import date, timedelta, datetime

st.set_page_config(page_title="Seasonal Contract Aggregator", layout="wide")

UNIT_TYPES = [
    "Plow",
    "Spreader",
    "Medium Duty Combination Unit",
    "Combination Unit",
    "Combination Unit - Wide Wing",
    "Tow Plow",
]

TOWING_TYPES = {"Combination Unit - Wide Wing", "Tow Plow"}

SEASON_START    = (10, 15)   # Oct 15
SEASON_END      = (4, 30)    # Apr 30
BENCHMARKS_PATH = "data/benchmarks.json"

# Magic bytes for transparent gzip detection on read.
# Cache (data/season_cache.json) is stored as gzip(JSON) post-2026-04-30 fix.
# Benchmarks stay raw JSON. Old raw-JSON cache files keep loading via the
# magic-byte check until the next save rewrites them.
_GZIP_MAGIC = b"\x1f\x8b"


def _decompress_if_gzipped(data: bytes) -> bytes:
    """Transparently gunzip if magic bytes match, else passthrough."""
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    return data

# Valid patrol numbers — constrains the Patrol # dropdown and controls the
# batch-migration target. Add a new number here when a new patrol is deployed.
PATROL_OPTIONS = ["11", "12", "13", "14", "15", "16"]

# Contiguous overlap intervals ≤ this many minutes are treated as rounding /
# boundary artifacts and are NOT counted as "impossible overlap" in the UI
# flagging layer (Overlap min column, Conflicts & Flags banner, anomaly strings).
# Billing dedupe (_merged_chain_windows) is independent and always runs at the
# strict minute level so billing correctness never depends on this constant.
OVERLAP_TOLERANCE_MIN = 2

# Contract benchmark hours per route, full season.
# Loaded from st.secrets["benchmarks"] (Streamlit Cloud) with fallback to benchmarks.json (local dev).
try:
    BENCHMARK_HOURS_TABLE: dict = dict(st.secrets["benchmarks"])
except (KeyError, FileNotFoundError):
    _BENCHMARKS_FILE = os.path.join(os.path.dirname(__file__), "benchmarks.json")
    try:
        with open(_BENCHMARKS_FILE) as _f:
            BENCHMARK_HOURS_TABLE: dict = json.load(_f)
    except FileNotFoundError:
        BENCHMARK_HOURS_TABLE: dict = {}

# Normalized lookup (case + dash insensitive: "WK1B" == "WK-1B" == "wk-1b")
def _norm_route(r: str) -> str:
    return r.upper().replace("-", "").replace(" ", "")

_BENCHMARK_NORM      = {_norm_route(k): v for k, v in BENCHMARK_HOURS_TABLE.items()}
_BENCHMARK_CANONICAL = {_norm_route(k): k for k in BENCHMARK_HOURS_TABLE}


def _lookup_benchmark(route: str, overrides: dict) -> tuple:
    """
    Returns (benchmark_hrs: float, source: str).
    source: 'override' | 'contract' | 'unknown'
    Checks overrides (benchmarks.json) first, then the hardcoded contract table.
    Both lookups are case/dash-insensitive.
    """
    safe_overrides = overrides if isinstance(overrides, dict) else {}
    norm = _norm_route(route)
    # Override check (exact then normalized)
    if route in safe_overrides:
        return float(safe_overrides[route]), "override"
    for k, v in safe_overrides.items():
        if _norm_route(k) == norm:
            return float(v), "override"
    # Contract table check
    if norm in _BENCHMARK_NORM:
        return float(_BENCHMARK_NORM[norm]), "contract"
    return 0.0, "unknown"


# ═══════════════════════════════════════════════════════════════════
# GitHub Cache Helpers
# ═══════════════════════════════════════════════════════════════════

def get_github_config():
    """Return GitHub config from st.secrets, or None if not set up."""
    try:
        return {
            "token":     st.secrets["github"]["token"],
            "repo":      st.secrets["github"]["repo"],
            "data_repo": st.secrets["github"].get("data_repo", st.secrets["github"]["repo"]),
            "branch":    st.secrets["github"].get("branch", "main"),
            "data_path": st.secrets["github"].get("data_path", "data/season_cache.json"),
        }
    except Exception:
        return None


def load_cache(config) -> tuple:
    """
    Returns (records: list, sha: str|None).
    Returns ([], None) if file doesn't exist yet or config is None.

    Above 1 MB the Contents API returns encoding="none" + empty content;
    we follow download_url (preferred — single round-trip, raw bytes) or
    fall back to /git/blobs/{sha} (Git Data API, supports up to 100 MB).
    Cache bytes may be raw JSON or gzip(JSON); _decompress_if_gzipped
    handles both transparently. On any decode/parse/network failure we
    fail loud (st.error + st.stop) — never silent [], which would let
    push_cache wipe the file.
    """
    if config is None:
        return [], None
    headers = {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{config['data_repo']}"
        f"/contents/{config['data_path']}?ref={config['branch']}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return [], None
        resp.raise_for_status()
        meta = resp.json()
        sha = meta["sha"]
        content_b64 = meta.get("content") or ""
        encoding = meta.get("encoding")

        if encoding == "none" or not content_b64:
            # File >1 MB — Contents API does not inline the body.
            # download_url is already pre-authenticated (private repo);
            # passing the Authorization header is harmless.
            dl = meta.get("download_url")
            if dl:
                raw = requests.get(dl, headers=headers, timeout=20)
                raw.raise_for_status()
                blob_bytes = raw.content
            else:
                blob_url = (
                    f"https://api.github.com/repos/{config['data_repo']}"
                    f"/git/blobs/{sha}"
                )
                blob = requests.get(blob_url, headers=headers, timeout=20)
                blob.raise_for_status()
                blob_bytes = base64.b64decode(blob.json()["content"])
        else:
            blob_bytes = base64.b64decode(content_b64)

        text = _decompress_if_gzipped(blob_bytes).decode("utf-8")
        return json.loads(text), sha
    except (
        requests.exceptions.RequestException,
        KeyError,
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,  # gzip.BadGzipFile is OSError; covers older Pythons too.
    ) as e:
        st.error(
            f"Critical: failed to load audit cache. "
            f"Halting to prevent silent data loss. Error: {e}"
        )
        st.stop()


def load_benchmarks(config) -> tuple:
    """
    Returns (data: dict, sha: str|None).
    Keys are route IDs, values are benchmark hours (float).

    A missing data/benchmarks.json (404) is a legitimate first-run
    state and returns ({}, None) quietly. Other errors (parse, decode,
    network) fail loud — silent {} would let save_benchmarks wipe a
    healthy file on a transient blip. Matches load_cache's >1 MB
    fallback shape so benchmarks can grow safely later if needed.
    """
    if config is None:
        return {}, None
    headers = {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{config['data_repo']}"
        f"/contents/{BENCHMARKS_PATH}?ref={config['branch']}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return {}, None  # legitimate first-run state, NOT a hard failure
        resp.raise_for_status()
        meta = resp.json()
        sha = meta["sha"]
        content_b64 = meta.get("content") or ""
        encoding = meta.get("encoding")

        if encoding == "none" or not content_b64:
            dl = meta.get("download_url")
            if dl:
                raw = requests.get(dl, headers=headers, timeout=20)
                raw.raise_for_status()
                blob_bytes = raw.content
            else:
                blob_url = (
                    f"https://api.github.com/repos/{config['data_repo']}"
                    f"/git/blobs/{sha}"
                )
                blob = requests.get(blob_url, headers=headers, timeout=20)
                blob.raise_for_status()
                blob_bytes = base64.b64decode(blob.json()["content"])
        else:
            blob_bytes = base64.b64decode(content_b64)

        text = _decompress_if_gzipped(blob_bytes).decode("utf-8")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            st.error(
                "Critical: benchmarks file is not a JSON object. "
                "Halting to prevent silent data loss."
            )
            st.stop()
        return parsed, sha
    except (
        requests.exceptions.RequestException,
        KeyError,
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as e:
        st.error(
            f"Critical: failed to load benchmarks. "
            f"Halting to prevent silent data loss. Error: {e}"
        )
        st.stop()


def _get_benchmarks_sha(config) -> str | None:
    """Raw GET — return current benchmarks file SHA or None.

    No Streamlit side effects (unlike load_benchmarks, which calls st.error).
    Returns None on any error (404, network, parse) — caller treats None as
    'file absent, attempt create.' Acceptable trade-off for a single-object
    PUT: the retry will recreate cleanly if the file genuinely doesn't
    exist, and last-writer-wins is the documented semantic for benchmarks.
    """
    if config is None:
        return None
    headers = {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{config['data_repo']}"
        f"/contents/{BENCHMARKS_PATH}?ref={config['branch']}"
    )
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("sha")
    except requests.exceptions.RequestException:
        return None


def save_benchmarks(config, data: dict, sha) -> str | None:
    """Write benchmarks dict to GitHub with one-shot 409 retry.

    Mirrors push_cache's retry shape but for a single-object PUT (no mutator
    — last writer wins). On 409 SHA mismatch, re-fetches the current SHA via
    _get_benchmarks_sha and retries once. Returns new SHA on success, None
    on failure.
    """
    if config is None:
        return None
    headers = {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{config['data_repo']}"
        f"/contents/{BENCHMARKS_PATH}"
    )
    content_b64 = base64.b64encode(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False).encode()
    ).decode("ascii")

    def _put(file_sha):
        payload = {
            "message": "Update route benchmarks",
            "content": content_b64,
            "branch":  config["branch"],
        }
        if file_sha:
            payload["sha"] = file_sha
        return requests.put(url, headers=headers, json=payload, timeout=15)

    try:
        resp = _put(sha)
        if resp.status_code == 409:
            # SHA mismatch — re-read SHA only (no Streamlit side effects)
            # and retry once. None on the retry = file was deleted; PUT
            # without sha is a valid recreate.
            resp = _put(_get_benchmarks_sha(config))
        resp.raise_for_status()
        return resp.json()["content"]["sha"]
    except requests.exceptions.RequestException as e:
        st.error(f"GitHub benchmarks write error: {e}")
        return None


def push_cache(config, mutator, commit_message: str) -> tuple[str, list] | None:
    """
    Apply `mutator` to the current cache records and PUT the result to GitHub.
    `mutator` is a callable: list[record] -> list[record]. It is re-applied
    to a freshly-fetched records list on 409 retry so concurrent writes do
    not corrupt edits (double-insert) or deletes (un-delete).

    Returns (new_sha, post_mutator_records) on success, None on failure.
    Callers should update their in-memory cache (sa_cache_data) with
    post_mutator_records rather than re-fetching from GitHub.
    """
    if config is None:
        return None
    headers = {
        "Authorization": f"token {config['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{config['data_repo']}"
        f"/contents/{config['data_path']}"
    )

    def _put(records_list, file_sha):
        # Compact JSON (no indent) + gzip + base64 for the Contents API
        # payload. mtime=0 keeps gzip output byte-deterministic so an
        # identical record set always produces an identical SHA — required
        # for 409 conflict detection to remain reliable. The bytes stored
        # in bh-data are gzip(JSON); the base64 wrap is only the wire
        # format the Contents API requires.
        raw_json = json.dumps(
            records_list, ensure_ascii=False, separators=(",", ":")
        ).encode()
        gzipped = gzip.compress(raw_json, compresslevel=6, mtime=0)
        content_b64 = base64.b64encode(gzipped).decode("ascii")
        payload = {
            "message": commit_message,
            "content": content_b64,
            "branch": config["branch"],
        }
        if file_sha:
            payload["sha"] = file_sha
        return requests.put(url, headers=headers, json=payload, timeout=15)

    try:
        base_records, base_sha = load_cache(config)
        applied_records = mutator(list(base_records))
        resp = _put(applied_records, base_sha)
        if resp.status_code == 409:
            # SHA mismatch — re-read, re-apply mutation, retry once.
            # Reassign applied_records explicitly so the returned value
            # is the records list that was actually committed.
            fresh_records, fresh_sha = load_cache(config)
            applied_records = mutator(list(fresh_records))
            resp = _put(applied_records, fresh_sha)
        resp.raise_for_status()
        return resp.json()["content"]["sha"], applied_records
    except requests.exceptions.RequestException as e:
        st.error(f"GitHub write error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# Conflict Detection
# ═══════════════════════════════════════════════════════════════════

def _circuit_absolute_windows(record: dict) -> list[tuple[int, int]]:
    """
    Return list of (abs_start_min, abs_end_min) for each circuit.
    abs_min = ordinal_day * 1440 + HH*60 + MM  (unique across calendar dates)
    """
    base_date = date.fromisoformat(record["start_date"])
    windows = []
    for c in record.get("circuits", []):
        sh, sm = map(int, c["start"].split(":"))
        eh, em = map(int, c["end"].split(":"))
        day_off = c.get("day_offset", 0)
        abs_day = (base_date + timedelta(days=day_off)).toordinal()
        w_start = abs_day * 1440 + sh * 60 + sm
        w_end   = abs_day * 1440 + eh * 60 + em
        if w_end <= w_start:      # defensive: circuit crosses midnight
            w_end += 1440
        windows.append((w_start, w_end))
    return windows


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    Merge overlapping/adjacent intervals into a minimal non-overlapping set.
    Input: list of (start, end) pairs. Returns sorted list of merged (start, end).
    Critical for minute-level union dedupe: two partially-overlapping intervals
    (e.g. 08:00-10:00 + 09:30-11:00) merge to 08:00-11:00 = 180 min, never 210.
    """
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged = [sorted_iv[0]]
    for s, e in sorted_iv[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def _record_covered_minutes(record: dict) -> set[int]:
    """Set of absolute-minute ints covered by any circuit in the record (union)."""
    covered: set[int] = set()
    for s, e in _circuit_absolute_windows(record):
        covered.update(range(s, e))
    return covered


def _fmt_abs_minute_range(s: int, e: int) -> str:
    """Format an absolute-minute range as HH:MM-HH:MM, adding a day marker if crossing midnight."""
    def _hhmm(m: int) -> str:
        return f"{(m % 1440) // 60:02d}:{m % 60:02d}"
    day_span = (e - 1) // 1440 - s // 1440
    if day_span <= 0:
        return f"{_hhmm(s)}-{_hhmm(e)}"
    return f"{_hhmm(s)}-{_hhmm(e)} (+{day_span}d)"


def _contiguous_intervals_from_minutes(mins: set[int]) -> list[tuple[int, int]]:
    """Return list of (start, end) half-open intervals from a set of absolute minutes.
    Empty set → []. Used for overlap display + tolerance filtering."""
    if not mins:
        return []
    sorted_mins = sorted(mins)
    intervals: list[tuple[int, int]] = []
    run_s = sorted_mins[0]
    prev = run_s
    for m in sorted_mins[1:]:
        if m == prev + 1:
            prev = m
        else:
            intervals.append((run_s, prev + 1))
            run_s = m
            prev = m
    intervals.append((run_s, prev + 1))
    return intervals


def _cross_unit_route_overlaps(
    pending: dict,
    all_records: list,
    tolerance_min: int = OVERLAP_TOLERANCE_MIN,
) -> dict:
    """
    Detect circuits where the *same route* is covered by a *different unit* with
    overlapping time windows. Route matching uses `_norm_route` (case/dash-
    insensitive). Pairs whose time overlap is ≤ tolerance_min are skipped.

    Returns {counterpart_id: {
        "unit": counterpart unit_number,
        "shared_min": total overlap minutes across all matching route pairs,
        "by_route": {canonical_route_label: [(abs_s, abs_e), ...]},
    }}. Empty dict if no cross-unit overlaps.

    Pure helper (no Streamlit, no I/O). Used by both the save-path mutator
    wrapper and the rescan pass so the same detection logic covers new and
    legacy records.
    """
    pending_unit = pending.get("unit_number", "")
    pending_id = pending.get("id", "")
    if not pending_unit:
        return {}
    try:
        pending_wins = _circuit_absolute_windows(pending)
    except Exception:
        return {}
    pending_circuits = pending.get("circuits", [])
    if not pending_circuits or len(pending_circuits) != len(pending_wins):
        return {}

    # Group pending's circuit windows by normalised route.
    pending_by_route: dict[str, list[tuple[int, int, str]]] = {}
    for c, (s, e) in zip(pending_circuits, pending_wins):
        raw = c.get("route", "") or ""
        if not raw:
            continue
        key = _norm_route(raw)
        pending_by_route.setdefault(key, []).append((s, e, raw))
    if not pending_by_route:
        return {}

    result: dict[str, dict] = {}
    for other in all_records:
        other_id = other.get("id", "")
        if not other_id or other_id == pending_id:
            continue
        if other.get("unit_number") == pending_unit:
            continue
        if not other.get("unit_number"):
            continue
        try:
            other_wins = _circuit_absolute_windows(other)
        except Exception:
            continue
        other_circuits = other.get("circuits", [])
        if len(other_circuits) != len(other_wins):
            continue

        overlaps_by_route: dict[str, list[tuple[int, int]]] = {}
        total_shared = 0
        for oc, (os_, oe) in zip(other_circuits, other_wins):
            raw = oc.get("route", "") or ""
            if not raw:
                continue
            key = _norm_route(raw)
            if key not in pending_by_route:
                continue
            for (ps, pe, pending_raw) in pending_by_route[key]:
                ss = max(ps, os_)
                ee = min(pe, oe)
                if ee - ss > tolerance_min:
                    # Use the pending record's route label for display.
                    overlaps_by_route.setdefault(pending_raw, []).append((ss, ee))
                    total_shared += (ee - ss)
        if overlaps_by_route:
            result[other_id] = {
                "unit": other.get("unit_number", ""),
                "shared_min": total_shared,
                "by_route": overlaps_by_route,
            }
    return result


def _format_cross_unit_anomaly(
    other_unit: str,
    other_id: str,
    shared_min: int,
    by_route: dict,
    max_intervals_per_route: int = 2,
) -> str:
    """Human-readable cross-unit anomaly string for the Anomaly Log."""
    route_parts = []
    for route, intervals in by_route.items():
        iv_txt = ", ".join(
            _fmt_abs_minute_range(s, e) for s, e in intervals[:max_intervals_per_route]
        )
        if len(intervals) > max_intervals_per_route:
            iv_txt += f", +{len(intervals) - max_intervals_per_route} more"
        route_parts.append(f"{route} {iv_txt}")
    return (
        f"ℹ️ Cross-unit route coverage — also run by unit {other_unit} "
        f"(id {other_id[:8]}…, {shared_min} min shared on {'; '.join(route_parts)})"
    )


def _shared_window_summary(
    rec_a: dict,
    rec_b: dict,
    max_intervals: int = 3,
    tolerance_min: int = OVERLAP_TOLERANCE_MIN,
) -> tuple[int, str]:
    """
    Compute the minute-level intersection between two records' circuits and
    filter out contiguous sub-intervals ≤ tolerance_min (rounding artifacts).
    Returns (true_impossible_shared_min, human_readable_text).
    Text format: "240 min shared (08:00-12:00)" or "240 min shared (08:00-10:00, 11:00-13:00)".
    Returns (0, "") if no non-trivial overlap remains after tolerance filtering.
    """
    a = _record_covered_minutes(rec_a)
    b = _record_covered_minutes(rec_b)
    shared = a & b
    if not shared:
        return 0, ""
    intervals = _contiguous_intervals_from_minutes(shared)
    # Tolerance filter: drop boundary / rounding artifacts.
    kept = [(s, e) for (s, e) in intervals if (e - s) > tolerance_min]
    if not kept:
        return 0, ""
    total = sum(e - s for (s, e) in kept)
    txt_parts = [_fmt_abs_minute_range(s, e) for s, e in kept[:max_intervals]]
    if len(kept) > max_intervals:
        txt_parts.append(f"+{len(kept) - max_intervals} more")
    return total, f"{total} min shared ({', '.join(txt_parts)})"


def check_conflicts(new_record: dict, existing_records: list) -> tuple[str, list]:
    """
    Returns (conflict_type, conflicting_records).
    conflict_type: "none" | "duplicate" | "overlap" | "same_day_no_overlap"
    """
    unit = new_record["unit_number"]
    new_base  = date.fromisoformat(new_record["start_date"])
    new_end   = new_base + timedelta(days=new_record.get("max_day_offset", 0))
    new_wins  = _circuit_absolute_windows(new_record)
    new_starts = {w[0] for w in new_wins}

    duplicates, overlaps, same_days = [], [], []

    for ex in existing_records:
        if ex.get("unit_number") != unit:
            continue
        # Use benchmark_unit for spare attribution
        ex_unit = ex.get("primary_unit_number") if ex.get("is_spare") else ex.get("unit_number")
        if ex_unit != unit and ex.get("unit_number") != unit:
            continue

        ex_base = date.fromisoformat(ex["start_date"])
        ex_end  = ex_base + timedelta(days=ex.get("max_day_offset", 0))

        # Date range overlap check
        if new_base > ex_end or new_end < ex_base:
            continue

        ex_wins   = _circuit_absolute_windows(ex)
        ex_starts = {w[0] for w in ex_wins}

        if new_starts & ex_starts:
            duplicates.append(ex)
            continue

        time_overlap = any(
            ns < ee and ne > es
            for ns, ne in new_wins
            for es, ee in ex_wins
        )
        if time_overlap:
            overlaps.append(ex)
        else:
            same_days.append(ex)

    if duplicates:
        return "duplicate", duplicates
    if overlaps:
        return "overlap", overlaps
    if same_days:
        return "same_day_no_overlap", same_days
    return "none", []


def rescan_conflicts(records: list) -> tuple[list, int]:
    """
    Walk all records pairwise, detect conflicts, and tag both sides of any
    pair that isn't already flagged. Returns (updated_records, n_updated).

    O(N^2) — fine for thousands; the per-pair work is only date/window math.
    Skips pairs whose either record lacks an id.
    """
    updated = [dict(r) for r in records]
    by_idx = {i: updated[i] for i in range(len(updated))}
    changed = {i: False for i in range(len(updated))}

    for i in range(len(updated)):
        a = by_idx[i]
        if not a.get("id"):
            continue
        for j in range(i + 1, len(updated)):
            b = by_idx[j]
            if not b.get("id"):
                continue
            if a.get("unit_number") != b.get("unit_number"):
                continue
            try:
                a_base = date.fromisoformat(a["start_date"])
                b_base = date.fromisoformat(b["start_date"])
            except Exception:
                continue
            a_end = a_base + timedelta(days=a.get("max_day_offset", 0))
            b_end = b_base + timedelta(days=b.get("max_day_offset", 0))
            if a_base > b_end or a_end < b_base:
                continue
            try:
                a_wins = _circuit_absolute_windows(a)
                b_wins = _circuit_absolute_windows(b)
            except Exception:
                continue
            a_starts = {w[0] for w in a_wins}
            b_starts = {w[0] for w in b_wins}

            # Compute the minute-level shared summary once per pair so both
            # sides' anomaly strings can name the exact doubled minutes.
            _, _shared_text = _shared_window_summary(a, b)
            _shared_suffix = f" — {_shared_text}" if _shared_text else ""

            if a_starts & b_starts:
                flag = "duplicate_confirmed"
                anom_tmpl = "⚠️ Duplicate (rescan){suffix} with id {sid}"
            elif any(an_s < be and an_e > bs
                     for an_s, an_e in a_wins
                     for bs, be in b_wins):
                flag = "overlap_confirmed"
                anom_tmpl = "⚠️ Time overlap (rescan){suffix} with id {sid}"
            else:
                flag = "multiple_same_day"
                anom_tmpl = "ℹ️ Multiple forms same unit/day (rescan){suffix} with id {sid}"

            for tgt_idx, other in ((i, b), (j, a)):
                tgt = by_idx[tgt_idx]
                other_short = (other.get("id", "") or "")[:8] + "…"
                msg = anom_tmpl.format(sid=other_short, suffix=_shared_suffix)
                dirty = False
                if tgt.get("conflict_status") in (None, "", "clean"):
                    tgt["conflict_status"] = flag
                    dirty = True
                anoms = list(tgt.get("anomalies") or [])
                if msg not in anoms:
                    anoms.append(msg)
                    tgt["anomalies"] = anoms
                    dirty = True
                if dirty:
                    changed[tgt_idx] = True

    # ── Second pass: cross-unit route overlaps ────────────────────────
    # Anomaly-only (no conflict_status change). Walk every pair across
    # different units and inject the cross-unit anomaly on both sides if
    # same route + time overlap > tolerance and not already recorded.
    for i in range(len(updated)):
        a = by_idx[i]
        if not a.get("id"):
            continue
        cross_unit_for_a = _cross_unit_route_overlaps(a, [by_idx[j] for j in range(len(updated)) if j != i])
        if not cross_unit_for_a:
            continue
        for other_id, info in cross_unit_for_a.items():
            # Find the index of `other` so we can mutate it too (and mark changed).
            other_idx = next((j for j in range(len(updated)) if by_idx[j].get("id") == other_id), None)
            if other_idx is None:
                continue
            # Anomaly for `a` — name the counterpart.
            msg_a = _format_cross_unit_anomaly(
                info["unit"], other_id, info["shared_min"], info["by_route"],
            )
            # Anomaly for `other` — name `a` instead.
            msg_other = _format_cross_unit_anomaly(
                a.get("unit_number", ""), a.get("id", ""), info["shared_min"], info["by_route"],
            )
            for tgt_idx, msg in ((i, msg_a), (other_idx, msg_other)):
                tgt = by_idx[tgt_idx]
                anoms = list(tgt.get("anomalies") or [])
                if msg not in anoms:
                    anoms.append(msg)
                    tgt["anomalies"] = anoms
                    changed[tgt_idx] = True

    n_updated = sum(1 for v in changed.values() if v)
    return updated, n_updated


# ═══════════════════════════════════════════════════════════════════
# HTML Report Builder
# ═══════════════════════════════════════════════════════════════════

def build_report_html(res, event_start_date, patrol, unit, unit_type,
                      auditor, is_spare=False, primary_unit="",
                      continues_to_next_form=False):
    generated_at = datetime.now().strftime("%B %d, %Y at %H:%M:%S")
    circuit_rows = res["circuit_rows"]
    gap_rows     = res["gap_rows"]
    total_circuit_minutes  = res["total_circuit_minutes"]
    total_gap_operating    = res["total_gap_operating"]
    total_gap_nonoperating = res["total_gap_nonoperating"]
    refuel_minutes         = res["refuel_minutes"]
    total_operating        = res["total_operating"]
    max_offset = max((r["day_offset"] for r in circuit_rows), default=0)

    if max_offset == 0:
        date_range = event_start_date.strftime("%B %d, %Y")
    else:
        end_date = event_start_date + timedelta(days=max_offset)
        date_range = f"{event_start_date.strftime('%B %d')}–{end_date.strftime('%d, %Y')}"

    def fmt_date(off): return (event_start_date + timedelta(days=off)).strftime("%b %d, %Y")
    def td(v, bold=False, center=False, bg=""):
        s = ("font-weight:bold;" if bold else "") + ("text-align:center;" if center else "") + (f"background:{bg};" if bg else "")
        return f"<td style='padding:6px 10px;border:1px solid #ccc;{s}'>{v}</td>"
    def th(v): return f"<th style='padding:7px 10px;border:1px solid #999;background:#2c5f8a;color:white;text-align:left'>{v}</th>"

    routes_used = sorted({c.get("route", "—") for c in circuit_rows if c.get("route")})

    # Circuits table
    c_tbody = ""
    for r in circuit_rows:
        bg = "#f0f7ff" if r["day_offset"] % 2 == 1 else "white"
        tp = "Yes" if r.get("tow_plow") else "No"
        c_tbody += (
            "<tr>"
            + td(str(r["#"]), center=True, bg=bg)
            + td(fmt_date(r["day_offset"]), bg=bg)
            + td(r.get("route", "—"), bg=bg)
            + td(r["Start"], center=True, bg=bg)
            + td(r["End"], center=True, bg=bg)
            + td(r["Duration"], center=True, bold=True, bg=bg)
            + td(tp, center=True, bg=bg)
            + "</tr>\n"
        )

    # Gaps table
    g_tbody = ""
    for r in gap_rows:
        rule = r["Rule"]
        bg = "#ffe0e0" if "NEW" in rule else ("#fff8e0" if "Capped" in rule else "white")
        g_tbody += (
            "<tr>"
            + td(str(r["Gap"]), center=True, bg=bg)
            + td(r["Between"], center=True, bg=bg)
            + td(fmt_date(r["gap_day_offset"]), bg=bg)
            + td(r["Gap Duration"], center=True, bg=bg)
            + td(r["Operating"], center=True, bg=bg)
            + td(r["Non-operating"], center=True, bg=bg)
            + td(r["Rule"], bg=bg)
            + "</tr>\n"
        )

    # Anomalies
    anomaly_html = ""
    for r in gap_rows:
        rule = r["Rule"]
        if "NEW WINTER EVENT" in rule:
            anomaly_html += (
                f"<div style='background:#ffe0e0;border-left:5px solid #cc0000;"
                f"padding:10px 14px;margin:8px 0;border-radius:3px'>"
                f"<strong>🔴 NEW WINTER EVENT — Gap {r['Gap']} ({r['Gap Duration']}):</strong> "
                f"Gap exceeds 3 hours — a new winter event begins here. "
                f"All circuits on this form are included in the total.</div>\n"
            )
        elif "Capped" in rule:
            anomaly_html += (
                f"<div style='background:#fff3cd;border-left:5px solid #cc8800;"
                f"padding:10px 14px;margin:8px 0;border-radius:3px'>"
                f"<strong>🟡 CAPPED GAP — Gap {r['Gap']} ({r['Gap Duration']}):</strong> "
                f"Only 60 min counts as operating. <strong>{r['Non-operating']}</strong> excluded.</div>\n"
            )
    if not anomaly_html:
        anomaly_html = "<div style='background:#e8f5e9;border-left:5px solid #2e7d32;padding:10px 14px;border-radius:3px'>✅ No anomalies detected.</div>"

    overnight_html = ""
    if max_offset > 0:
        overnight_html = (
            f"<div style='background:#e3f2fd;border-left:5px solid #1565c0;"
            f"padding:10px 14px;margin:10px 0;border-radius:3px'>"
            f"🌙 <strong>Overnight Event ({max_offset + 1} days: {date_range}).</strong> "
            f"Calendar dates assigned automatically by entry order.</div>\n"
        )

    spare_html = ""
    if is_spare and primary_unit:
        spare_html = (
            f"<div style='background:#fff3e0;border-left:5px solid #e65100;"
            f"padding:10px 14px;margin:10px 0;border-radius:3px'>"
            f"⚠️ <strong>Spare Unit:</strong> This unit operated as a spare replacing "
            f"<strong>{primary_unit}</strong>. These hours are "
            f"attributed to <strong>{primary_unit}</strong>'s benchmark total.</div>\n"
        )

    continues_html = ""
    if continues_to_next_form:
        continues_html = (
            "<div style='background:#e8f4fd;border-left:5px solid #1976d2;"
            "padding:10px 14px;margin:10px 0;border-radius:3px'>"
            "ℹ️ <strong>Continues to Next Form:</strong> This form's winter event continues on "
            "the next day's form. Refuel allowance is deferred — it will be recorded on the "
            "continuation form.</div>\n"
        )

    bd_html = ""
    bd_rows = [("Circuit operating time", f"{total_circuit_minutes // 60}h {total_circuit_minutes % 60:02d}m", f"{total_circuit_minutes / 60:.2f} hrs")]
    if total_gap_operating:
        bd_rows.append(("Inter-circuit gap (operating, per contract cap)", f"+{total_gap_operating // 60}h {total_gap_operating % 60:02d}m", f"+{total_gap_operating / 60:.2f} hrs"))
    if total_gap_nonoperating:
        bd_rows.append(("Inter-circuit gap (excluded — non-operating)", f"−{total_gap_nonoperating // 60}h {total_gap_nonoperating % 60:02d}m", f"−{total_gap_nonoperating / 60:.2f} hrs"))
    if continues_to_next_form and refuel_minutes == 0:
        bd_rows.append(("End-of-event refuel", "(deferred to next form)", "—"))
    elif refuel_minutes:
        _n_evts = 1 + res.get("intra_form_new_events", 0)
        _base_r = refuel_minutes // _n_evts if _n_evts > 1 else refuel_minutes
        _ref_label = (
            f"Refuel allowance ({_n_evts} events \u00d7 {_base_r} min each)"
            if _n_evts > 1
            else "End-of-event allowance (unload/refuel)"
        )
        bd_rows.append((_ref_label, f"+{refuel_minutes}m", f"+{refuel_minutes / 60:.2f} hrs"))
    for label, hhmm, dec in bd_rows:
        bd_html += (
            f"<tr><td style='padding:6px 10px;border:1px solid #ccc'>{label}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ccc;text-align:center;font-weight:bold'>{hhmm}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ccc;text-align:center;color:#555'>{dec}</td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Audit Record — {unit} / {', '.join(routes_used)} / {event_start_date}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 13px; color: #111; margin: 0; padding: 0; }}
  .page {{ max-width: 960px; margin: 30px auto; padding: 0 30px 40px; }}
  h2 {{ font-size: 15px; color: #2c5f8a; border-bottom: 2px solid #2c5f8a; padding-bottom: 4px; margin: 28px 0 10px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 6px; }}
  .header-bar {{ background: #1a3a5c; color: white; padding: 20px 30px; }}
  .header-bar h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .header-bar .meta {{ margin-top: 10px; font-size: 13px; opacity: 0.9; line-height: 2.0; }}
  .result-box {{ background: #e8f5e9; border: 2px solid #2e7d32; border-radius: 6px; padding: 18px 24px; margin: 16px 0; text-align: center; }}
  .result-box .value {{ font-size: 32px; font-weight: bold; color: #1b5e20; }}
  .result-box .decimal {{ font-size: 16px; color: #2e7d32; margin-top: 4px; }}
  .cert-box {{ background: #f5f5f5; border: 1px solid #ccc; border-radius: 4px; padding: 14px 18px; margin-top: 24px; font-size: 12px; color: #444; line-height: 1.7; }}
  @media print {{ body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }} .page {{ margin: 0; padding: 20px; }} }}
</style>
</head>
<body>
<div class="header-bar">
  <h1>WINTER VEHICLE OPERATING HOURS — AUDIT RECORD</h1>
  <div class="meta">
    <strong>Patrol #:</strong> {patrol} &nbsp;&nbsp;
    <strong>Unit #:</strong> {unit} &nbsp;&nbsp;
    <strong>Unit Type:</strong> {unit_type}<br>
    <strong>Routes:</strong> {', '.join(routes_used) if routes_used else '—'} &nbsp;&nbsp;
    <strong>Event Date(s):</strong> {date_range}<br>
    <strong>Auditor:</strong> {auditor} &nbsp;&nbsp;
    <strong>Generated:</strong> {generated_at}
  </div>
</div>
<div class="page">
{overnight_html}{spare_html}{continues_html}
<h2>Contract Rules Applied</h2>
<ol style="line-height:2">
  <li>The Contractor is eligible for <strong>up to 1 hour</strong> of time between circuits to be counted
      as part of operating hours. Anything over 1 hour is <strong>non-operating time</strong>.</li>
  <li>At the End of Winter Event, operating hours shall include the time required to unload any
      leftover material and to refuel the unit, but shall <strong>not exceed 30 minutes</strong>.</li>
  <li>If there is a gap of <strong>longer than 3 hours</strong> between circuits, that is
      considered a <strong>new winter event boundary</strong>. All circuits on this form are
      included in the total; the 3-hour rule is applied across forms in season analytics.</li>
</ol>
<h2>Circuit Log</h2>
<table>
  <thead><tr>{th("#")}{th("Date")}{th("Route #")}{th("Start")}{th("End")}{th("Duration")}{th("Tow Plow")}</tr></thead>
  <tbody>{c_tbody}</tbody>
</table>
<h2>Gap Analysis</h2>
<table>
  <thead><tr>{th("Gap #")}{th("Between")}{th("Date")}{th("Gap Duration")}{th("Operating")}{th("Non-operating")}{th("Rule Applied")}</tr></thead>
  <tbody>{"<tr><td colspan='7' style='padding:8px 10px;color:#666;font-style:italic'>No inter-circuit gaps (single circuit event).</td></tr>" if not gap_rows else g_tbody}</tbody>
</table>
<h2>Anomalies &amp; Flags</h2>
{anomaly_html}
<h2>Calculation Breakdown</h2>
<table>
  <thead><tr>
    <th style='padding:7px 10px;border:1px solid #999;background:#2c5f8a;color:white;text-align:left'>Item</th>
    <th style='padding:7px 10px;border:1px solid #999;background:#2c5f8a;color:white;text-align:center'>Time (h:mm)</th>
    <th style='padding:7px 10px;border:1px solid #999;background:#2c5f8a;color:white;text-align:center'>Decimal Hours</th>
  </tr></thead>
  <tbody>{bd_html}</tbody>
</table>
<h2>Final Result</h2>
<div class="result-box">
  <div style="font-size:14px;color:#555;margin-bottom:4px">Total Benchmark Operating Hours</div>
  <div class="value">{total_operating // 60}h {total_operating % 60:02d}m</div>
  <div class="decimal">{total_operating / 60:.2f} hours &nbsp;·&nbsp; {total_operating} minutes</div>
</div>
<div class="cert-box">
  <strong>Certification:</strong> This report summarizes operating hours calculated from the circuit log above.<br><br>
  <strong>Auditor:</strong> {auditor} &nbsp;&nbsp;
  <strong>Patrol:</strong> {patrol} &nbsp;&nbsp;
  <strong>Unit:</strong> {unit} &nbsp;&nbsp;
  <strong>Routes:</strong> {', '.join(routes_used) if routes_used else '—'}<br>
  <strong>Event Date(s):</strong> {date_range} &nbsp;&nbsp;
  <strong>Generated:</strong> {generated_at}
</div>
</div>
</body>
</html>"""
    return html


# ═══════════════════════════════════════════════════════════════════
# Attribution Helper
# ═══════════════════════════════════════════════════════════════════

def _attribute_operating_hours(record: dict) -> dict:
    """
    Returns {route: attributed_operating_hrs} for one record.

    Each inter-circuit gap is attributed to the route of the **preceding** circuit
    (the route that was active when the gap began). Refuel time goes to the last
    circuit's route. This correctly handles multi-day events where Route A runs on
    Day 1 and Route B on Day 2 — the overnight gap belongs to Route A.

    For single-route events all operating time goes to that route (no change).
    """
    circuits = record.get("circuits", [])
    if not circuits:
        return {}

    base = date.fromisoformat(record["start_date"])
    seq = []
    for c in circuits:
        sh, sm = map(int, c["start"].split(":"))
        eh, em = map(int, c["end"].split(":"))
        off = c.get("day_offset", 0)
        abs_day = (base + timedelta(days=off)).toordinal()
        s = abs_day * 1440 + sh * 60 + sm
        e = abs_day * 1440 + eh * 60 + em
        if e <= s:
            e += 1440
        seq.append({
            "route": c.get("route") or "—",
            "s": s, "e": e,
            "dur": c.get("duration_min", 0),
        })

    attributed = {}

    # Circuit time
    for item in seq:
        rt = item["route"]
        attributed[rt] = attributed.get(rt, 0) + item["dur"]

    # Gap time: each gap attributed to the route of the preceding circuit
    for i in range(len(seq) - 1):
        gap = seq[i + 1]["s"] - seq[i]["e"]
        if gap > 180:
            break   # new winter event — circuits after this excluded anyway
        gap_op = min(gap, 60)   # capped at 60 min per contract
        attributed[seq[i]["route"]] = attributed.get(seq[i]["route"], 0) + gap_op

    # Refuel → last circuit's route
    refuel = record.get("refuel_minutes", 0)
    if refuel and seq:
        last_rt = seq[-1]["route"]
        attributed[last_rt] = attributed.get(last_rt, 0) + refuel

    return {rt: mins / 60 for rt, mins in attributed.items()}


# ═══════════════════════════════════════════════════════════════════
# Event Chain Helpers
# ═══════════════════════════════════════════════════════════════════

def _record_abs_start(record: dict) -> int:
    """Absolute start time (minutes) of the first circuit in a record."""
    circuits = record.get("circuits", [])
    if not circuits:
        return 0
    base = date.fromisoformat(record["start_date"])
    c = circuits[0]
    sh, sm = map(int, c["start"].split(":"))
    off = c.get("day_offset", 0)
    return (base + timedelta(days=off)).toordinal() * 1440 + sh * 60 + sm


def _record_abs_end(record: dict) -> int:
    """Absolute end time (minutes) of the last circuit in a record."""
    circuits = record.get("circuits", [])
    if not circuits:
        return 0
    base = date.fromisoformat(record["start_date"])
    c = circuits[-1]
    sh, sm = map(int, c["start"].split(":"))
    eh, em = map(int, c["end"].split(":"))
    off = c.get("day_offset", 0)
    abs_day = (base + timedelta(days=off)).toordinal()
    s = abs_day * 1440 + sh * 60 + sm
    e = abs_day * 1440 + eh * 60 + em
    if e <= s:
        e += 1440
    return e


def _get_chain_cache_key(records: list) -> str:
    """SHA256 of all record IDs + saved_at — used to detect cache staleness."""
    sig = "|".join(sorted(f"{r.get('id','')}:{r.get('saved_at','')}" for r in records))
    return hashlib.sha256(sig.encode()).hexdigest()


def _build_event_chains(records_for_unit: list) -> list:
    """
    Group records for ONE unit into event chains using the 3-hour gap rule.
    Input records are sorted by absolute start time before grouping.
    Returns a list of chains, where each chain is a list of 1..N records.
    """
    if not records_for_unit:
        return []
    sorted_recs = sorted(records_for_unit, key=_record_abs_start)
    chains = [[sorted_recs[0]]]
    for rec in sorted_recs[1:]:
        chain_end = _record_abs_end(chains[-1][-1])
        rec_start = _record_abs_start(rec)
        if rec_start - chain_end <= 180:
            chains[-1].append(rec)
        else:
            chains.append([rec])
    return chains


def _build_analytics_view(records: list) -> dict:
    """Deterministic Analytics-tab derivations from the records list.

    Returns the records DataFrame + filter dropdown options + date-range
    bounds + the cross-record route set used by the benchmarks expander.
    Paired with the sa_analytics_view session-state cache, keyed by
    _get_chain_cache_key(records) — see sa_chain_cache for the same
    pattern. The two caches are ALWAYS invalidated together.

    Empty-cache safety: returns date.today() for min/max when records is
    empty (intentional improvement; the prior inline code at the call
    site already had this guard, but moving it into the helper documents
    the contract).
    """
    rows = []
    for r in records:
        routes_str = ", ".join(r.get("routes_used", [])) or "—"
        hours = r.get("total_operating_minutes", 0) / 60
        _rid = r.get("id", "")
        rows.append({
            "ID":           (_rid[:8] + "…") if _rid else "",
            "Date":         r.get("start_date", ""),
            "Patrol":       r.get("patrol_number", ""),
            "Unit":         r.get("unit_number", ""),
            "Unit Type":    r.get("unit_type", ""),
            "Routes":       routes_str,
            "Total Hours":  round(hours, 2),
            "Tow Plow":     "Yes" if r.get("tow_plow_used") else "No",
            "Overnight":    "Yes" if r.get("has_overnight") else "No",
            "Spare":        "Yes" if r.get("is_spare") else "No",
            "Out of Season":"⚠️" if r.get("out_of_season") else "",
            "Flags":        r.get("conflict_status", "clean"),
            "Anomalies":    "; ".join(r.get("anomalies", [])) or "",
            "_id":          _rid,
        })
    df = pd.DataFrame(rows)
    dates = pd.to_datetime(df["Date"]) if not df.empty else None
    return {
        "df":               df,
        "patrol_opts":      ["All"] + sorted(df["Patrol"].unique().tolist()),
        "unit_opts":        ["All"] + sorted(df["Unit"].unique().tolist()),
        "all_routes":       sorted({r for rr in df["Routes"]
                                    for r in rr.split(", ") if r and r != "—"}),
        "min_date":         dates.min().date() if dates is not None else date.today(),
        "max_date":         dates.max().date() if dates is not None else date.today(),
        "all_cache_routes": sorted({c.get("route", "") for r in records
                                    for c in r.get("circuits", []) if c.get("route")}),
    }


def _combined_circuit_seq(chain: list) -> list:
    """Build a single sorted circuit sequence from all records in a chain."""
    seq = []
    for record in chain:
        base = date.fromisoformat(record["start_date"])
        for c in record.get("circuits", []):
            sh, sm = map(int, c["start"].split(":"))
            eh, em = map(int, c["end"].split(":"))
            off = c.get("day_offset", 0)
            abs_day = (base + timedelta(days=off)).toordinal()
            s = abs_day * 1440 + sh * 60 + sm
            e = abs_day * 1440 + eh * 60 + em
            if e <= s:
                e += 1440
            seq.append({"route": c.get("route") or "—", "s": s, "e": e,
                        "dur": c.get("duration_min", 0)})
    seq.sort(key=lambda x: x["s"])
    return seq


def _merged_chain_windows(chain: list) -> list[dict]:
    """
    Union-merge all circuit time windows across a chain into non-overlapping intervals.
    Route attribution: each merged interval is attributed to the route of the
    earliest-starting circuit contributing to it. Later-starting duplicates are
    absorbed — their minutes are NOT added again (minute-level union dedupe).

    Example: [(WK1B, 08:00-10:00), (WK2B, 09:30-11:00)]
      → [{"s":08:00, "e":11:00, "route":"WK1B"}]  (180 min total, not 210).

    Returns a list of {"s": abs_min, "e": abs_min, "route": str} sorted by start.
    """
    seq = _combined_circuit_seq(chain)   # already sorted by start
    if not seq:
        return []
    merged: list[dict] = []
    for c in seq:
        if not merged:
            merged.append({"s": c["s"], "e": c["e"], "route": c["route"]})
            continue
        last = merged[-1]
        if c["s"] <= last["e"]:
            # Overlap / adjacency — extend the end, keep the earlier route.
            if c["e"] > last["e"]:
                last["e"] = c["e"]
        else:
            merged.append({"s": c["s"], "e": c["e"], "route": c["route"]})
    return merged


def _compute_chain_hours(chain: list) -> dict:
    """
    Re-derive operating hours across all records in a chain using MINUTE-LEVEL
    UNION of circuit windows — overlapping circuits between forms never
    double-count. Refuel is taken from the LAST record only (one refuel per event).
    Returns dict with total_operating_min, gap_operating_min, refuel_min, has_overnight.
    """
    if not chain:
        return {"total_operating_min": 0, "circuit_min_by_route": {},
                "gap_operating_min": 0, "refuel_min": 0, "has_overnight": False}
    merged = _merged_chain_windows(chain)
    if not merged:
        return {"total_operating_min": 0, "circuit_min_by_route": {},
                "gap_operating_min": 0, "refuel_min": 0, "has_overnight": False}

    circuit_min_by_route: dict = {}
    total_circuit_min = 0
    for m in merged:
        dur = m["e"] - m["s"]
        circuit_min_by_route[m["route"]] = circuit_min_by_route.get(m["route"], 0) + dur
        total_circuit_min += dur

    # Derive per-event refuel from last record (handles both old and new saves)
    _last_rec = chain[-1]
    _continues = _last_rec.get("continues_to_next_form", False)
    _intra = _last_rec.get("intra_form_new_events", 0)
    _n_completed = _intra + (0 if _continues else 1)
    _total_refuel = _last_rec.get("refuel_minutes", 0 if _continues else 30)
    base_refuel = (_total_refuel // _n_completed) if _n_completed > 0 else 0

    gap_operating_min = 0
    intra_form_refuels = 0
    for i in range(len(merged) - 1):
        gap = merged[i + 1]["s"] - merged[i]["e"]
        if gap > 180:
            # Intra-form event boundary (cross-form gaps are ≤180 by chain construction)
            intra_form_refuels += base_refuel
            continue
        gap_operating_min += min(max(gap, 0), 60)

    end_refuel = base_refuel if (not _continues and _total_refuel > 0) else 0
    has_overnight = (max(m["e"] // 1440 for m in merged) > min(m["s"] // 1440 for m in merged))
    total_operating_min = total_circuit_min + gap_operating_min + intra_form_refuels + end_refuel

    return {
        "total_operating_min": total_operating_min,
        "circuit_min_by_route": circuit_min_by_route,
        "gap_operating_min": gap_operating_min,
        "refuel_min": intra_form_refuels + end_refuel,
        "has_overnight": has_overnight,
    }


def _attribute_chain_hours(chain: list) -> dict:
    """
    Sequential attribution of all operating hours across a chain using
    MINUTE-LEVEL UNION merging (same dedupe as _compute_chain_hours).
    Each inter-merged-interval gap is attributed to the preceding interval's route.
    Refuel (last record only) goes to the last merged interval's route.
    Returns {route: attributed_operating_hrs}.
    """
    merged = _merged_chain_windows(chain)
    if not merged:
        return {}

    # Derive per-event refuel (matches _compute_chain_hours logic)
    _last_rec = chain[-1]
    _continues = _last_rec.get("continues_to_next_form", False)
    _intra = _last_rec.get("intra_form_new_events", 0)
    _n_completed = _intra + (0 if _continues else 1)
    _total_refuel = _last_rec.get("refuel_minutes", 0 if _continues else 30)
    base_refuel = (_total_refuel // _n_completed) if _n_completed > 0 else 0
    end_refuel = base_refuel if (not _continues and _total_refuel > 0) else 0

    attributed: dict = {}
    for m in merged:
        attributed[m["route"]] = attributed.get(m["route"], 0) + (m["e"] - m["s"])

    for i in range(len(merged) - 1):
        gap = merged[i + 1]["s"] - merged[i]["e"]
        if gap > 180:
            if base_refuel:
                attributed[merged[i]["route"]] = attributed.get(merged[i]["route"], 0) + base_refuel
            continue
        gap_op = min(max(gap, 0), 60)
        attributed[merged[i]["route"]] = attributed.get(merged[i]["route"], 0) + gap_op

    if end_refuel and merged:
        attributed[merged[-1]["route"]] = attributed.get(merged[-1]["route"], 0) + end_refuel

    return {rt: mins / 60 for rt, mins in attributed.items()}


def _record_to_report_result(record: dict) -> dict:
    """
    Re-derive the result dict needed by build_report_html from a saved cache record.
    Replicates the Tab 1 calculate loop using saved circuit data.
    All circuits are included (no new-event exclusion — Tab 1 rule).
    """
    circuits = record.get("circuits", [])
    if not circuits:
        return {
            "errors": [], "circuit_rows": [], "gap_rows": [],
            "total_circuit_minutes": 0, "total_gap_operating": 0,
            "total_gap_nonoperating": 0, "refuel_minutes": 0,
            "intra_form_new_events": 0,
            "total_operating": record.get("refuel_minutes", 0),
            "has_overnight": False, "max_day_offset": 0,
            "tow_plow_used": False, "routes_used": [], "anomalies": [],
        }

    windows = _circuit_absolute_windows(record)
    circuit_rows, gap_rows = [], []
    total_circuit_minutes = 0
    total_gap_operating   = 0
    total_gap_nonoperating = 0

    for i, (c, (w_start, w_end)) in enumerate(zip(circuits, windows)):
        day_off = c.get("day_offset", 0)
        dur = c.get("duration_min", 0)
        circuit_rows.append({
            "#": i + 1,
            "Day": f"Day {day_off + 1}",
            "Route": c.get("route") or "—",
            "Start": c["start"],
            "End": c["end"],
            "Duration": fmt_hhmm(dur),
            "Tow Plow": "Yes" if c.get("tow_plow") else "No",
            "day_offset": day_off,
            "tow_plow": c.get("tow_plow", False),
            "route": c.get("route", ""),
        })
        total_circuit_minutes += dur

        if i < len(circuits) - 1:
            gap = windows[i + 1][0] - w_end
            if gap < 0:
                gap_op, gap_nonop, note = 0, 0, "⚠️ Overlap"
            elif gap > 180:
                gap_op, gap_nonop = 0, gap
                note = "🔴 NEW WINTER EVENT"
            elif gap > 60:
                gap_op, gap_nonop = 60, gap - 60
                note = f"Capped at 1h (+{gap - 60}m non-operating)"
            else:
                gap_op, gap_nonop = gap, 0
                if 0 < gap < 10:
                    note = f"⚠️ Short transition ({gap}m) — verify departure/arrival times"
                else:
                    note = "Full gap counts"
            total_gap_operating    += gap_op
            total_gap_nonoperating += gap_nonop
            gap_rows.append({
                "Gap": i + 1, "Between": f"C{i+1} → C{i+2}",
                "Gap Duration": fmt_hhmm(gap),
                "Operating": fmt_hhmm(gap_op),
                "Non-operating": fmt_hhmm(gap_nonop),
                "Rule": note,
                "gap_day_offset": day_off,
            })

    refuel_minutes = record.get("refuel_minutes", 0)
    intra_form_new_events = record.get("intra_form_new_events", 0)
    total_operating = total_circuit_minutes + total_gap_operating + refuel_minutes
    max_day_offset = max((c.get("day_offset", 0) for c in circuits), default=0)
    tow_plow_used = any(c.get("tow_plow") for c in circuits)
    routes_used = sorted({c.get("route", "") for c in circuits if c.get("route")})
    anomalies = [r["Rule"] for r in gap_rows if "NEW" in r["Rule"] or "Capped" in r["Rule"] or "⚠️" in r["Rule"]]
    for _cr in circuit_rows:
        if not _cr.get("route"):
            anomalies.append(f"⚠️ Missing route label on Circuit {_cr['#']}")

    return {
        "errors": [], "circuit_rows": circuit_rows, "gap_rows": gap_rows,
        "total_circuit_minutes":   total_circuit_minutes,
        "total_gap_operating":     total_gap_operating,
        "total_gap_nonoperating":  total_gap_nonoperating,
        "refuel_minutes":          refuel_minutes,
        "intra_form_new_events":   intra_form_new_events,
        "continues_to_next_form":  record.get("continues_to_next_form", False),
        "total_operating":         total_operating,
        "has_overnight":           max_day_offset > 0,
        "max_day_offset":          max_day_offset,
        "tow_plow_used":           tow_plow_used,
        "routes_used":             routes_used,
        "anomalies":               anomalies,
    }


# ═══════════════════════════════════════════════════════════════════
# Time Parsing Helpers
# ═══════════════════════════════════════════════════════════════════

def parse_hhmm(text: str):
    """Parse 4-digit HHMM string (e.g. '0930'). Returns (h, m) or None."""
    t = text.strip()
    if len(t) == 4 and t.isdigit():
        h, m = int(t[:2]), int(t[2:])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    return None


def parse_hh_mm(text: str):
    """Parse H:MM or HH:MM string (e.g. '9:30', '09:30'). Returns (h, m) or None."""
    t = text.strip()
    parts = t.split(":")
    if len(parts) == 2:
        hp, mp = parts[0], parts[1]
        if hp.isdigit() and 1 <= len(hp) <= 2 and mp.isdigit() and len(mp) == 2:
            h, m = int(hp), int(mp)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
    return None


def parse_either(text: str):
    """Accept '930' (3-digit), '0930' (4-digit), or '09:30' (formatted). Returns (h, m) or None."""
    t = text.strip()
    if t.isdigit():
        if len(t) == 3:
            h, m = int(t[0]), int(t[1:])
            if 0 <= m <= 59:
                return h, m
            return None
        return parse_hhmm(t)
    return parse_hh_mm(t)


def _sa_reformat_to_hh_mm(key: str):
    """on_change callback: normalise any valid time input to HH:MM display."""
    raw = st.session_state.get(key, "").strip()
    p = parse_either(raw)
    if p:
        st.session_state[key] = f"{p[0]:02d}:{p[1]:02d}"


def _normalize_patrol(raw: str) -> str:
    """
    Strip any leading 'Patrol' (case-insensitive, punctuation, whitespace) and
    return the bare identifier. Used by (1) the batch-migration button,
    (2) the Row-select Edit hydration guard so pre-migration records with
    prefixed patrol values don't crash the new selectbox.

    Examples:
      'Patrol 11' → '11'
      'Patrol  11' → '11'   (double space collapsed)
      'patrol-16' → '16'
      'PATROL: 12' → '12'
      '12' → '12'
      'Patrol ' → ''
      '' → ''
    """
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r'^(?i:patrol)\s*[#:\-]?\s*', '', s).strip()
    s = re.sub(r'\s+', ' ', s)
    return s


def _clear_form_state():
    """
    Atomically reset every Entry-tab widget key and non-widget state to a
    blank-form baseline. Must be called from inside an `on_click` callback —
    widget-bound writes are illegal in inline post-widget handlers (raises
    StreamlitAPIException). Used by both the 🔄 New Form button and the
    Row-select Edit flow to guarantee that the form does NOT carry stale
    fields into the new session ("Frankenstein" prevention).
    """
    # Pop per-circuit widget keys for every row currently on screen
    for _c in st.session_state.get("sa_circuits", []):
        _cid = _c["id"]
        for _pfx in ["sa_sh", "sa_sm", "sa_eh", "sa_em",
                     "sa_rt", "sa_tp", "sa_st", "sa_et"]:
            st.session_state.pop(f"{_pfx}_{_cid}", None)
    # Fresh circuit row with a never-before-used ID (counter monotone, per CLAUDE.md)
    st.session_state.sa_circuit_counter = st.session_state.get("sa_circuit_counter", 0) + 1
    _new_id = st.session_state.sa_circuit_counter
    st.session_state.sa_circuits = [
        {"id": _new_id, "start_h": 0, "start_m": 0,
         "end_h": 0, "end_m": 0, "route": "", "tow_plow": False}
    ]
    # Header widget keys — reset so the form is actually blank
    st.session_state["sa_patrol"]       = ""
    st.session_state["sa_unit"]         = ""
    st.session_state["sa_is_spare"]     = False
    st.session_state["sa_primary_unit"] = ""
    st.session_state["sa_start_date"]   = date.today()
    # End-of-event allowance widgets
    st.session_state["sa_refuel_cb"]    = True
    st.session_state["sa_refuel_min"]   = 30
    # Continues-to-next-form checkbox
    st.session_state["sa_continues"]    = False
    # Non-widget state
    st.session_state.sa_calc_results      = None
    st.session_state.sa_conflict_state    = None
    st.session_state.sa_editing_record_id = None
    # sa_time_mode intentionally preserved (user's format choice)
    st.session_state.sa_prev_time_mode    = st.session_state.get("sa_time_mode", "HHMM (e.g. 0930)")


# ═══════════════════════════════════════════════════════════════════
# Session State
# ═══════════════════════════════════════════════════════════════════

if "sa_circuit_counter" not in st.session_state:
    st.session_state.sa_circuit_counter = 0

if "sa_circuits" not in st.session_state:
    st.session_state.sa_circuits = [
        {"id": 0, "start_h": 0, "start_m": 0, "end_h": 0, "end_m": 0, "route": "", "tow_plow": False}
    ]

# Backward-compat: patch any existing circuits missing the id field
for _i, _c in enumerate(st.session_state.sa_circuits):
    if "id" not in _c:
        _c["id"] = _i
if st.session_state.sa_circuit_counter < len(st.session_state.sa_circuits) - 1:
    st.session_state.sa_circuit_counter = len(st.session_state.sa_circuits) - 1

if "sa_time_mode" not in st.session_state:
    st.session_state.sa_time_mode = "HHMM (e.g. 0930)"
if "sa_prev_time_mode" not in st.session_state:
    st.session_state.sa_prev_time_mode = st.session_state.sa_time_mode
if "sa_calc_results" not in st.session_state:
    st.session_state.sa_calc_results = None
if "sa_conflict_state" not in st.session_state:
    st.session_state.sa_conflict_state = None   # None | {"type":..., "records":[...], "pending_record":{...}}
if "sa_benchmarks" not in st.session_state:
    st.session_state.sa_benchmarks = {}
if "sa_benchmarks_sha" not in st.session_state:
    st.session_state.sa_benchmarks_sha = None
if "sa_benchmarks_loaded" not in st.session_state:
    st.session_state.sa_benchmarks_loaded = False
if "sa_chain_cache" not in st.session_state:
    st.session_state.sa_chain_cache = None
if "sa_analytics_view" not in st.session_state:
    st.session_state.sa_analytics_view = None
if "sa_pending_delete" not in st.session_state:
    st.session_state.sa_pending_delete = None
if "sa_pending_delete_confirmed" not in st.session_state:
    st.session_state.sa_pending_delete_confirmed = False
if "sa_editing_record_id" not in st.session_state:
    st.session_state.sa_editing_record_id = None
if "sa_just_loaded" not in st.session_state:
    st.session_state.sa_just_loaded = False
# Destructive Replace cascade — tuple of sorted counterpart ids when armed,
# None when the arm button hasn't been clicked yet for the current conflict.
if "sa_dup_replace_armed_target" not in st.session_state:
    st.session_state.sa_dup_replace_armed_target = None


def sa_add_circuit():
    st.session_state.sa_circuit_counter += 1
    st.session_state.sa_circuits.append(
        {"id": st.session_state.sa_circuit_counter,
         "start_h": 0, "start_m": 0, "end_h": 0, "end_m": 0, "route": "", "tow_plow": False}
    )


def sa_remove_circuit(idx):
    if len(st.session_state.sa_circuits) > 1:
        removed = st.session_state.sa_circuits.pop(idx)
        cid = removed["id"]
        for _pfx in ["sa_sh", "sa_sm", "sa_eh", "sa_em", "sa_rt", "sa_tp", "sa_st", "sa_et"]:
            st.session_state.pop(f"{_pfx}_{cid}", None)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def is_in_season(d: date) -> bool:
    sm, sd = SEASON_START
    em, ed = SEASON_END
    if d.month > sm or (d.month == sm and d.day >= sd):
        return True   # Oct 15 – Dec 31
    if d.month < em or (d.month == em and d.day <= ed):
        return True   # Jan 1 – Apr 30
    return False


def fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m"


# ═══════════════════════════════════════════════════════════════════
# Main UI
# ═══════════════════════════════════════════════════════════════════

st.title("📋 Seasonal Benchmark Hours Aggregator")
st.caption("Winter Vehicle Operating Hours — Cache & Analytics")

# ── Hoisted module-scope: GitHub config + cache loaders ───────────────
# These run on every outer rerun (NOT inside any fragment), so both the
# Entry and Analytics fragments can read sa_cache_data / sa_benchmarks
# regardless of which tab the user lands on first. Keep one-shot gates
# on session_state presence — same logic as before, just hoisted out of
# the Analytics tab body.
gh_config = get_github_config()
if gh_config is None:
    st.warning(
        "GitHub cache not configured. Add `[github]` section to Streamlit secrets "
        "to enable. See the plan documentation for required keys."
    )
    st.stop()

if "sa_cache_data" not in st.session_state:
    with st.spinner("Loading cache from GitHub..."):
        records, _ = load_cache(gh_config)
        st.session_state.sa_cache_data = records

if not st.session_state.get("sa_benchmarks_loaded", False):
    with st.spinner("Loading benchmarks..."):
        bm_data, bm_sha = load_benchmarks(gh_config)
        st.session_state.sa_benchmarks       = bm_data
        st.session_state.sa_benchmarks_sha   = bm_sha
        st.session_state.sa_benchmarks_loaded = True

tab_entry, tab_analytics, tab_guide = st.tabs(["📝 Entry & Calculate", "📊 Cache Viewer & Analytics", "📖 Auditor Guide"])


# ───────────────────────────────────────────────────────────────────
# TAB 1: ENTRY & CALCULATE
# ───────────────────────────────────────────────────────────────────
@st.fragment
def render_entry_tab():

    # ── Edit mode banner ──────────────────────────────────────────────
    _edit_id = st.session_state.get("sa_editing_record_id")
    if _edit_id:
        _eb1, _eb2 = st.columns([6, 1])
        with _eb1:
            st.warning(
                f"✏️ **Edit mode** — record `{_edit_id[:8]}...` is loaded. "
                "Recalculate and save to replace the existing record."
            )
        with _eb2:
            st.markdown("<div style='padding-top:8px'></div>", unsafe_allow_html=True)
            if st.button("✖ Cancel Edit", key="sa_cancel_edit"):
                st.session_state.sa_editing_record_id = None
                st.rerun(scope="app")

    with st.expander("ℹ️ Contract Rules"):
        st.markdown("""
        - **Inter-circuit gap ≤ 1 hour:** Entire gap counts as operating time.
        - **Inter-circuit gap > 1 hour:** Only the first 60 minutes counts; remainder is non-operating.
        - **Gap > 3 hours:** Marks a new winter event boundary. All circuits on this form are included in the form total. Season analytics (Tab 2) applies the 3-hour rule across forms.
        - **End of Winter Event:** Up to 30 minutes for unloading/refuelling.
        - **Spare unit:** Hours attributed to the primary unit's benchmark.
        - **Overnight events:** Enter circuits in order — day changes detected automatically.
        """)

    # ── Event Header ─────────────────────────────────────────────────
    st.subheader("Event Header")
    h1, h2, h3, h4 = st.columns(4)
    with h1:
        patrol_number = st.selectbox(
            "Patrol #",
            options=[""] + PATROL_OPTIONS,
            format_func=lambda v: "— Select —" if v == "" else v,
            key="sa_patrol",
        )
    with h2:
        unit_number = st.text_input("Unit #", placeholder="e.g. Unit 12", key="sa_unit")
    with h3:
        unit_type = st.selectbox("Unit Type", UNIT_TYPES, key="sa_unit_type")
    with h4:
        event_start_date = st.date_input("Event Start Date", value=date.today(), key="sa_start_date")

    # Spare unit fields
    is_spare = st.checkbox("This unit operated as a spare", key="sa_is_spare")
    primary_unit_number = ""
    if is_spare:
        primary_unit_number = st.text_input(
            "Primary Unit # (hours attributed to this unit's benchmark)",
            placeholder="e.g. Unit 07", key="sa_primary_unit"
        )

    # Season date validation
    if not is_in_season(event_start_date):
        st.warning(
            f"⚠️ {event_start_date.strftime('%b %d, %Y')} falls outside the contracted "
            f"winter period (Oct 15 – Apr 30). Verify this entry before saving."
        )

    # ── Circuits ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("Circuits")
    st.caption("Enter each circuit in chronological order. Overnight day changes are detected automatically.")

    # Time input mode selector
    _SA_MODE_OPTIONS = ["H/M Boxes", "HHMM (e.g. 0930)", "HH:MM (e.g. 09:30)"]
    _sa_mode_index = _SA_MODE_OPTIONS.index(st.session_state.sa_time_mode) if st.session_state.sa_time_mode in _SA_MODE_OPTIONS else 1
    sa_mode = st.radio(
        "Time entry format",
        _SA_MODE_OPTIONS,
        index=_sa_mode_index,
        horizontal=True,
        key="sa_time_mode",
        help="Choose how you prefer to enter start and end times for each circuit.",
    )

    # Detect mode change and sync canonical → widget keys when switching back to H/M Boxes
    _sa_prev_mode = st.session_state.sa_prev_time_mode
    _sa_mode_changed = sa_mode != _sa_prev_mode
    st.session_state.sa_prev_time_mode = sa_mode

    if _sa_mode_changed and sa_mode == "H/M Boxes":
        for _c in st.session_state.sa_circuits:
            _cid = _c["id"]
            st.session_state[f"sa_sh_{_cid}"] = _c["start_h"]
            st.session_state[f"sa_sm_{_cid}"] = _c["start_m"]
            st.session_state[f"sa_eh_{_cid}"] = _c["end_h"]
            st.session_state[f"sa_em_{_cid}"] = _c["end_m"]
    elif _sa_mode_changed and sa_mode == "HHMM (e.g. 0930)":
        for _c in st.session_state.sa_circuits:
            _cid = _c["id"]
            sh, sm = _c["start_h"], _c["start_m"]
            eh, em = _c["end_h"], _c["end_m"]
            st.session_state[f"sa_st_{_cid}"] = f"{sh:02d}{sm:02d}" if (sh or sm) else ""
            st.session_state[f"sa_et_{_cid}"] = f"{eh:02d}{em:02d}" if (eh or em) else ""
    elif _sa_mode_changed and sa_mode == "HH:MM (e.g. 09:30)":
        for _c in st.session_state.sa_circuits:
            _cid = _c["id"]
            sh, sm = _c["start_h"], _c["start_m"]
            eh, em = _c["end_h"], _c["end_m"]
            st.session_state[f"sa_st_{_cid}"] = f"{sh:02d}:{sm:02d}" if (sh or sm) else ""
            st.session_state[f"sa_et_{_cid}"] = f"{eh:02d}:{em:02d}" if (eh or em) else ""

    for i, c in enumerate(st.session_state.sa_circuits):
        cid = c["id"]

        if sa_mode == "H/M Boxes":
            col_lbl, col_rt, col_sh, col_sm, col_arr, col_eh, col_em, col_tp, col_del = st.columns(
                [0.7, 1.2, 0.5, 0.5, 0.2, 0.5, 0.5, 1.0, 0.3]
            )
            with col_lbl:
                st.markdown(f"**Circuit {i + 1}**")
            with col_rt:
                c["route"] = st.text_input("Route #", c.get("route", ""), key=f"sa_rt_{cid}",
                                           placeholder="e.g. K-1B", label_visibility="collapsed")
            with col_sh:
                c["start_h"] = st.number_input("SH", 0, 23, c["start_h"], key=f"sa_sh_{cid}",
                                               label_visibility="collapsed")
            with col_sm:
                c["start_m"] = st.number_input("SM", 0, 59, c["start_m"], key=f"sa_sm_{cid}",
                                               label_visibility="collapsed")
            with col_arr:
                st.markdown("<div style='text-align:center;padding-top:8px'>→</div>", unsafe_allow_html=True)
            with col_eh:
                c["end_h"] = st.number_input("EH", 0, 23, c["end_h"], key=f"sa_eh_{cid}",
                                             label_visibility="collapsed")
            with col_em:
                c["end_m"] = st.number_input("EM", 0, 59, c["end_m"], key=f"sa_em_{cid}",
                                             label_visibility="collapsed")
            with col_tp:
                c["tow_plow"] = st.checkbox("Tow Plow", c.get("tow_plow", False), key=f"sa_tp_{cid}")
            with col_del:
                st.button(
                    "🗑", key=f"sa_rm_{cid}",
                    disabled=len(st.session_state.sa_circuits) == 1,
                    on_click=sa_remove_circuit, args=(i,),
                )

        elif sa_mode == "HHMM (e.g. 0930)":
            if f"sa_st_{cid}" not in st.session_state:
                st.session_state[f"sa_st_{cid}"] = ""
            if f"sa_et_{cid}" not in st.session_state:
                st.session_state[f"sa_et_{cid}"] = ""

            col_lbl, col_rt, col_start, col_arr, col_end, col_tp, col_del = st.columns(
                [0.7, 1.2, 0.95, 0.2, 0.95, 1.0, 0.3]
            )
            with col_lbl:
                st.markdown(f"**Circuit {i + 1}**")
            with col_rt:
                c["route"] = st.text_input("Route #", c.get("route", ""), key=f"sa_rt_{cid}",
                                           placeholder="e.g. K-1B", label_visibility="collapsed")
            with col_start:
                _sa_start_text = st.text_input(
                    "Start time", key=f"sa_st_{cid}",
                    placeholder="e.g. 0930", label_visibility="collapsed",
                )
                _sa_ps = parse_hhmm(_sa_start_text) if _sa_start_text.strip() else (c["start_h"], c["start_m"])
                if _sa_start_text.strip() and _sa_ps is None:
                    st.caption("⚠️ Use format: e.g. 0930")
                elif _sa_ps:
                    c["start_h"], c["start_m"] = _sa_ps
            with col_arr:
                st.markdown("<div style='text-align:center;padding-top:8px'>→</div>", unsafe_allow_html=True)
            with col_end:
                _sa_end_text = st.text_input(
                    "End time", key=f"sa_et_{cid}",
                    placeholder="e.g. 0930", label_visibility="collapsed",
                )
                _sa_pe = parse_hhmm(_sa_end_text) if _sa_end_text.strip() else (c["end_h"], c["end_m"])
                if _sa_end_text.strip() and _sa_pe is None:
                    st.caption("⚠️ Use format: e.g. 0930")
                elif _sa_pe:
                    c["end_h"], c["end_m"] = _sa_pe
            with col_tp:
                c["tow_plow"] = st.checkbox("Tow Plow", c.get("tow_plow", False), key=f"sa_tp_{cid}")
            with col_del:
                st.button(
                    "🗑", key=f"sa_rm_{cid}",
                    disabled=len(st.session_state.sa_circuits) == 1,
                    on_click=sa_remove_circuit, args=(i,),
                )

        else:
            # HH:MM mode — single box, type 4 digits, auto-formats to HH:MM on tab-away
            if f"sa_st_{cid}" not in st.session_state:
                st.session_state[f"sa_st_{cid}"] = ""
            if f"sa_et_{cid}" not in st.session_state:
                st.session_state[f"sa_et_{cid}"] = ""

            col_lbl, col_rt, col_start, col_arr, col_end, col_tp, col_del = st.columns(
                [0.7, 1.2, 0.95, 0.2, 0.95, 1.0, 0.3]
            )
            with col_lbl:
                st.markdown(f"**Circuit {i + 1}**")
            with col_rt:
                c["route"] = st.text_input("Route #", c.get("route", ""), key=f"sa_rt_{cid}",
                                           placeholder="e.g. K-1B", label_visibility="collapsed")
            with col_start:
                _sa_start_text = st.text_input(
                    "Start time", key=f"sa_st_{cid}",
                    placeholder="e.g. 09:30", label_visibility="collapsed",
                    on_change=_sa_reformat_to_hh_mm,
                    args=(f"sa_st_{cid}",),
                )
                _sa_ps = parse_either(_sa_start_text) if _sa_start_text.strip() else (c["start_h"], c["start_m"])
                if _sa_start_text.strip() and _sa_ps is None:
                    st.caption("⚠️ Use 3–4 digits: e.g. 930 or 0930")
                elif _sa_ps:
                    c["start_h"], c["start_m"] = _sa_ps
            with col_arr:
                st.markdown("<div style='text-align:center;padding-top:8px'>→</div>", unsafe_allow_html=True)
            with col_end:
                _sa_end_text = st.text_input(
                    "End time", key=f"sa_et_{cid}",
                    placeholder="e.g. 09:30", label_visibility="collapsed",
                    on_change=_sa_reformat_to_hh_mm,
                    args=(f"sa_et_{cid}",),
                )
                _sa_pe = parse_either(_sa_end_text) if _sa_end_text.strip() else (c["end_h"], c["end_m"])
                if _sa_end_text.strip() and _sa_pe is None:
                    st.caption("⚠️ Use 3–4 digits: e.g. 930 or 0930")
                elif _sa_pe:
                    c["end_h"], c["end_m"] = _sa_pe
            with col_tp:
                c["tow_plow"] = st.checkbox("Tow Plow", c.get("tow_plow", False), key=f"sa_tp_{cid}")
            with col_del:
                st.button(
                    "🗑", key=f"sa_rm_{cid}",
                    disabled=len(st.session_state.sa_circuits) == 1,
                    on_click=sa_remove_circuit, args=(i,),
                )

    # on_click callback → automatic fragment-only rerun (no explicit
    # st.rerun = no app-scope rerun = Analytics tab body NOT re-executed).
    st.button("➕ Add Circuit", on_click=sa_add_circuit)

    # ── End-of-event allowance ────────────────────────────────────────
    st.divider()
    st.subheader("End-of-Event Allowance")
    include_refuel = st.checkbox(
        "Include unload/refuel time (up to 30 min per contract)", value=True, key="sa_refuel_cb"
    )
    refuel_minutes = 0
    if include_refuel:
        raw_refuel = st.number_input("Actual minutes", 0, 60, 30, key="sa_refuel_min")
        if raw_refuel > 30:
            st.caption("Capped at 30 min.")
        refuel_minutes = min(raw_refuel, 30)

    continues_to_next_form = st.checkbox(
        "This form continues on the next day's form (refuel will be counted on the next form)",
        key="sa_continues",
    )

    # ── Calculate ─────────────────────────────────────────────────────
    st.divider()
    if st.button("▶ Calculate Operating Hours", type="primary", key="sa_calc"):

        errors = []
        circuit_rows = []
        gap_rows = []
        total_circuit_minutes = 0
        total_gap_operating   = 0
        total_gap_nonoperating = 0
        intra_form_new_events  = 0
        day_offset   = 0
        prev_end_abs = 0

        for i, c in enumerate(st.session_state.sa_circuits):
            cid = c["id"]

            # Resolve start/end h:m — for text modes re-parse from widget state
            if sa_mode == "HHMM (e.g. 0930)":
                _st_text = st.session_state.get(f"sa_st_{cid}", "").strip() or "0000"
                _et_text = st.session_state.get(f"sa_et_{cid}", "").strip() or "0000"
                _ps = parse_hhmm(_st_text)
                _pe = parse_hhmm(_et_text)
                if _ps is None:
                    errors.append(f"Circuit {i + 1}: invalid start time '{_st_text}' — use format 0930.")
                    continue
                if _pe is None:
                    errors.append(f"Circuit {i + 1}: invalid end time '{_et_text}' — use format 0930.")
                    continue
                start_h, start_m, end_h, end_m = _ps[0], _ps[1], _pe[0], _pe[1]
            elif sa_mode == "HH:MM (e.g. 09:30)":
                _st_text = st.session_state.get(f"sa_st_{cid}", "").strip() or "00:00"
                _et_text = st.session_state.get(f"sa_et_{cid}", "").strip() or "00:00"
                _ps = parse_either(_st_text)
                _pe = parse_either(_et_text)
                if _ps is None:
                    errors.append(f"Circuit {i + 1}: invalid start time '{_st_text}' — type 3–4 digits, e.g. 930 or 0930.")
                    continue
                if _pe is None:
                    errors.append(f"Circuit {i + 1}: invalid end time '{_et_text}' — type 3–4 digits, e.g. 930 or 0930.")
                    continue
                start_h, start_m, end_h, end_m = _ps[0], _ps[1], _pe[0], _pe[1]
            else:
                start_h, start_m = c["start_h"], c["start_m"]
                end_h, end_m     = c["end_h"], c["end_m"]

            raw_start = start_h * 60 + start_m
            raw_end   = end_h   * 60 + end_m
            start_abs = day_offset * 1440 + raw_start

            if start_abs < prev_end_abs:
                day_offset += 1
                start_abs  += 1440

            end_abs = day_offset * 1440 + raw_end
            if end_abs < start_abs:
                end_abs += 1440

            duration = end_abs - start_abs

            if duration <= 0:
                errors.append(f"Circuit {i + 1}: end time must be after start time.")
                prev_end_abs = end_abs
                continue

            circuit_rows.append({
                "#": i + 1,
                "Day":      f"Day {day_offset + 1}",
                "Route":    c.get("route") or "—",
                "Start":    f"{start_h:02d}:{start_m:02d}",
                "End":      f"{end_h:02d}:{end_m:02d}",
                "Duration": fmt_hhmm(duration),
                "Tow Plow": "Yes" if c.get("tow_plow") else "No",
                "day_offset": day_offset,
                "tow_plow":   c.get("tow_plow", False),
                "route":      c.get("route", ""),
            })

            total_circuit_minutes += duration

            if i < len(st.session_state.sa_circuits) - 1:
                nc  = st.session_state.sa_circuits[i + 1]
                _nc_cid = nc["id"]
                if sa_mode == "HHMM (e.g. 0930)":
                    _nc_st = st.session_state.get(f"sa_st_{_nc_cid}", "").strip() or "0000"
                    _pnc = parse_hhmm(_nc_st)
                    if _pnc is None:
                        prev_end_abs = end_abs
                        continue
                    nrs = _pnc[0] * 60 + _pnc[1]
                elif sa_mode == "HH:MM (e.g. 09:30)":
                    _nc_st = st.session_state.get(f"sa_st_{_nc_cid}", "").strip() or "00:00"
                    _pnc = parse_either(_nc_st)
                    if _pnc is None:
                        prev_end_abs = end_abs
                        continue
                    nrs = _pnc[0] * 60 + _pnc[1]
                else:
                    nrs = nc["start_h"] * 60 + nc["start_m"]
                nd = day_offset
                ns_abs = nd * 1440 + nrs
                if ns_abs < end_abs:
                    nd += 1
                    ns_abs += 1440

                gap = ns_abs - end_abs

                if gap < 0:
                    errors.append(f"Circuit {i + 2} starts before Circuit {i + 1} ends.")
                    gap_op, gap_nonop, note = 0, 0, "⚠️ Overlap"
                elif gap > 180:
                    gap_op, gap_nonop = 0, gap
                    note = "🔴 NEW WINTER EVENT"
                    intra_form_new_events += 1
                elif gap > 60:
                    gap_op    = 60
                    gap_nonop = gap - 60
                    note = f"Capped at 1h (+{gap_nonop}m non-operating)"
                else:
                    gap_op, gap_nonop = gap, 0
                    if 0 < gap < 10:
                        note = f"⚠️ Short transition ({gap}m) — verify departure/arrival times"
                    else:
                        note = "Full gap counts"

                total_gap_operating    += gap_op
                total_gap_nonoperating += gap_nonop

                gap_rows.append({
                    "Gap": i + 1, "Between": f"C{i+1} → C{i+2}",
                    "Gap Duration": fmt_hhmm(gap),
                    "Operating": fmt_hhmm(gap_op),
                    "Non-operating": fmt_hhmm(gap_nonop),
                    "Rule": note,
                    "gap_day_offset": day_offset,
                })

            prev_end_abs = end_abs

        if errors:
            st.session_state.sa_calc_results = {"errors": errors}
        else:
            base_refuel_per_event = refuel_minutes   # per-event allowance from widget (≤30 min)
            # completed_events = events that truly ended on this form
            # If base_refuel_per_event=0 (unchecked), refuel_minutes stays 0 regardless of continues
            completed_events = intra_form_new_events + (0 if continues_to_next_form else 1)
            refuel_minutes = base_refuel_per_event * completed_events
            total_operating = total_circuit_minutes + total_gap_operating + refuel_minutes
            max_day_offset  = max((r["day_offset"] for r in circuit_rows), default=0)
            tow_plow_used   = any(c.get("tow_plow") for c in st.session_state.sa_circuits)
            routes_used     = sorted({c.get("route", "") for c in circuit_rows if c.get("route")})
            anomalies       = [
                r["Rule"] for r in gap_rows
                if "NEW" in r["Rule"] or "Capped" in r["Rule"] or "⚠️" in r["Rule"]
            ]
            for _cr in circuit_rows:
                if not _cr.get("route"):
                    anomalies.append(f"⚠️ Missing route label on Circuit {_cr['#']}")
            st.session_state.sa_calc_results = {
                "errors": [],
                "circuit_rows": circuit_rows,
                "gap_rows": gap_rows,
                "total_circuit_minutes":   total_circuit_minutes,
                "total_gap_operating":     total_gap_operating,
                "total_gap_nonoperating":  total_gap_nonoperating,
                "refuel_minutes":          refuel_minutes,
                "intra_form_new_events":   intra_form_new_events,
                "continues_to_next_form":  continues_to_next_form,
                "total_operating":         total_operating,
                "has_overnight":           max_day_offset > 0,
                "max_day_offset":          max_day_offset,
                "tow_plow_used":           tow_plow_used,
                "routes_used":             routes_used,
                "anomalies":               anomalies,
            }
        st.session_state.sa_conflict_state = None   # reset on new calculation

    # ── Render Results ────────────────────────────────────────────────
    res = st.session_state.sa_calc_results
    if res is not None:
        if res["errors"]:
            for e in res["errors"]:
                st.error(e)
        else:
            total_operating        = res["total_operating"]
            total_circuit_minutes  = res["total_circuit_minutes"]
            total_gap_operating    = res["total_gap_operating"]
            total_gap_nonoperating = res["total_gap_nonoperating"]
            refuel_minutes         = res["refuel_minutes"]

            st.subheader("📊 Results")

            if res["has_overnight"]:
                st.info(f"🌙 Overnight event — {res['max_day_offset'] + 1} calendar days. Dates assigned automatically.")

            if res.get("tow_plow_used"):
                st.info("⚠️ Tow plow used in at least one circuit — enhanced risk mitigation rate applies for this event.")

            display_cols = ["#", "Day", "Route", "Start", "End", "Duration", "Tow Plow"]
            st.markdown("**Circuits:**")
            st.dataframe(pd.DataFrame(res["circuit_rows"])[display_cols],
                         hide_index=True, use_container_width=True)

            if res["gap_rows"]:
                st.markdown("**Gap Analysis:**")
                gap_cols = ["Gap", "Between", "Gap Duration", "Operating", "Non-operating", "Rule"]
                st.dataframe(pd.DataFrame(res["gap_rows"])[gap_cols],
                             hide_index=True, use_container_width=True)

            for row in res["gap_rows"]:
                rule = row["Rule"]
                if "NEW WINTER EVENT" in rule:
                    st.error(f"🔴 Gap {row['Gap']} ({row['Gap Duration']}) > 3 hours — new winter event boundary. All circuits on this form are included in the total.")
                elif "Capped" in rule:
                    st.warning(f"🟡 Gap {row['Gap']} ({row['Gap Duration']}) > 1 hour — only 60 min counts; {row['Non-operating']} excluded.")

            st.divider()
            st.markdown("**Calculation Breakdown:**")
            bd = [("Circuit operating time", fmt_hhmm(total_circuit_minutes))]
            if total_gap_operating:    bd.append(("Inter-circuit gap (operating)", f"+{fmt_hhmm(total_gap_operating)}"))
            if total_gap_nonoperating: bd.append(("Inter-circuit gap (excluded)", f"−{fmt_hhmm(total_gap_nonoperating)}"))
            if res.get("continues_to_next_form") and refuel_minutes == 0:
                bd.append(("End-of-event refuel", "*(deferred to next form)*"))
            elif refuel_minutes:
                _n_evts = 1 + res.get("intra_form_new_events", 0)
                _base_r = refuel_minutes // _n_evts if _n_evts > 1 else refuel_minutes
                _ref_label = (
                    f"End-of-event allowance ({_n_evts} events \u00d7 {_base_r} min each)"
                    if _n_evts > 1
                    else "End-of-event allowance"
                )
                bd.append((_ref_label, f"+{refuel_minutes}m"))
            for label, val in bd:
                c1, c2 = st.columns([3, 1])
                with c1: st.markdown(label)
                with c2: st.markdown(f"**{val}**")

            st.success(
                f"### ✅ Total Benchmark Hours: {fmt_hhmm(total_operating)}"
                f"&nbsp;&nbsp;({total_operating} min · {total_operating / 60:.2f} hrs)"
            )

            # ── Save to Cache ─────────────────────────────────────────
            # gh_config is hoisted to module scope; st.stop() at module
            # level ensures we never reach here with gh_config is None.
            st.divider()
            st.subheader("💾 Save to Cache")

            conf_state = st.session_state.sa_conflict_state
            _edit_id   = st.session_state.get("sa_editing_record_id")

            def _do_save_push(gh_cfg, mutator, msg, edit_id=None):
                """Push via mutator and update caches in place. Returns new sha.

                Callers see the original Optional[str] contract — the records
                list returned by push_cache is consumed internally to update
                sa_cache_data, avoiding a follow-up GitHub re-fetch.
                """
                result = push_cache(gh_cfg, mutator, msg)
                if result is None:
                    return None
                new_sha, new_records = result
                st.session_state.sa_cache_data     = new_records
                st.session_state.sa_chain_cache    = None
                st.session_state.sa_analytics_view = None
                if edit_id:
                    st.session_state.sa_editing_record_id = None
                return new_sha

            def _upsert_mutator(clean_record, edit_id):
                """Return a mutator that replaces (if edit_id) or appends the record."""
                def _m(recs):
                    return [r for r in recs if r.get("id") != edit_id] + [clean_record]
                return _m

            def _with_cross_unit_detection(inner_mutator, clean_record):
                """
                Wrap any save mutator so that, on the fresh-cache records list,
                cross-unit route overlaps with `clean_record` are detected and
                anomaly strings are injected on BOTH sides (pending + each
                counterpart). No `conflict_status` change — cross-unit overlap
                is operational, not billing, so it's Anomaly-Log-only.
                Idempotent: 409-retry runs the detection again against the new
                fresh cache, and the anomaly-append check avoids duplicates.
                """
                def _m(recs):
                    cross_unit = _cross_unit_route_overlaps(clean_record, recs)
                    intermediate = inner_mutator(recs)
                    if not cross_unit:
                        return intermediate
                    pending_id = clean_record.get("id", "")
                    pending_unit = clean_record.get("unit_number", "")
                    pending_anoms = [
                        _format_cross_unit_anomaly(
                            info["unit"], _oid, info["shared_min"], info["by_route"],
                        )
                        for _oid, info in cross_unit.items()
                    ]
                    cp_anom_for_id = {
                        _oid: _format_cross_unit_anomaly(
                            pending_unit, pending_id, info["shared_min"], info["by_route"],
                        )
                        for _oid, info in cross_unit.items()
                    }

                    out = []
                    for r in intermediate:
                        r_id = r.get("id", "")
                        if r_id == pending_id and pending_anoms:
                            r = dict(r)
                            _a = list(r.get("anomalies") or [])
                            for _s in pending_anoms:
                                if _s not in _a:
                                    _a.append(_s)
                            r["anomalies"] = _a
                        elif r_id in cp_anom_for_id:
                            r = dict(r)
                            _a = list(r.get("anomalies") or [])
                            _s = cp_anom_for_id[r_id]
                            if _s not in _a:
                                _a.append(_s)
                            r["anomalies"] = _a
                        out.append(r)
                    return out
                return _m

            def _counterpart_mutator(clean_record, edit_id, counterpart_ids,
                                    counterpart_flag, counterpart_anom_by_id):
                """
                Upsert `clean_record` AND tag every existing record whose id is in
                `counterpart_ids` with `counterpart_flag` (only if currently clean)
                plus an appended anomaly from `counterpart_anom_by_id[r_id]`.
                Ensures both sides of a confirmed conflict show up in the Anomaly
                Log and Conflicts & Flags views, each with a *record-specific*
                shared-minute summary.

                counterpart_anom_by_id: dict[str, str] — key is counterpart id,
                value is the anomaly string to append to that specific record.
                """
                def _m(recs):
                    out = []
                    for r in recs:
                        if r.get("id") == edit_id:
                            continue
                        if r.get("id") in counterpart_ids:
                            counterpart_anom = counterpart_anom_by_id.get(r.get("id", ""), "")
                            r = dict(r)
                            if r.get("conflict_status") in (None, "", "clean"):
                                r["conflict_status"] = counterpart_flag
                            anoms = list(r.get("anomalies") or [])
                            if counterpart_anom and counterpart_anom not in anoms:
                                anoms.append(counterpart_anom)
                            r["anomalies"] = anoms
                        out.append(r)
                    out.append(clean_record)
                    return out
                return _m

            if conf_state is None:
                _save_lbl = "💾 Save Changes (Replace Record)" if _edit_id else "💾 Save This Entry to Cache"
                if st.button(_save_lbl, disabled=gh_config is None):
                    with st.spinner("Checking cache for conflicts..."):
                        existing, _sha = load_cache(gh_config)

                    # When editing, exclude the old record so it doesn't flag itself
                    existing_for_check = (
                        [r for r in existing if r.get("id") != _edit_id]
                        if _edit_id else existing
                    )

                    # Build the pending record (no leading-underscore internal fields)
                    pending = {
                        "id":           str(uuid.uuid4()),
                        "saved_at":     datetime.now().isoformat(),
                        "patrol_number": patrol_number,
                        "start_date":   event_start_date.isoformat(),
                        "unit_number":  unit_number,
                        "unit_type":    unit_type,
                        "is_spare":     is_spare,
                        "primary_unit_number": primary_unit_number if is_spare else "",
                        "out_of_season": not is_in_season(event_start_date),
                        "tow_plow_used": res["tow_plow_used"],
                        "routes_used":   res["routes_used"],
                        "circuits": [
                            {
                                "route":      r.get("route", ""),
                                "start":      r["Start"],
                                "end":        r["End"],
                                "tow_plow":   r.get("tow_plow", False),
                                "day_offset": r["day_offset"],
                                "duration_min": int(r["Duration"].split("h")[0]) * 60 +
                                                int(r["Duration"].split("h")[1].strip().replace("m",""))
                                                if "h" in r["Duration"] else int(r["Duration"].replace("m",""))
                            }
                            for r in res["circuit_rows"]
                        ],
                        "refuel_minutes":          res["refuel_minutes"],
                        "intra_form_new_events":   res.get("intra_form_new_events", 0),
                        "continues_to_next_form":  res.get("continues_to_next_form", False),
                        "total_circuit_minutes":   res["total_circuit_minutes"],
                        "total_gap_operating":     res["total_gap_operating"],
                        "total_gap_nonoperating":  res["total_gap_nonoperating"],
                        "total_operating_minutes": res["total_operating"],
                        "has_overnight":   res["has_overnight"],
                        "max_day_offset":  res["max_day_offset"],
                        "anomalies":       res["anomalies"],
                        "conflict_status": "clean",
                    }

                    ctype, crecs = check_conflicts(pending, existing_for_check)

                    if ctype == "none":
                        _msg = (
                            f"Edit record {_edit_id[:8]}: {unit_number} / {','.join(res['routes_used']) or '—'} / {event_start_date}"
                            if _edit_id else
                            f"Add entry: {unit_number} / {','.join(res['routes_used']) or '—'} / {event_start_date}"
                        )
                        new_sha = _do_save_push(
                            gh_config,
                            _with_cross_unit_detection(
                                _upsert_mutator(pending, _edit_id), pending,
                            ),
                            _msg, _edit_id,
                        )
                        if new_sha:
                            st.success("✅ Record updated." if _edit_id else "✅ Saved to cache successfully.")
                    else:
                        st.session_state.sa_conflict_state = {
                            "type": ctype, "records": crecs,
                            "pending": pending, "edit_id": _edit_id,
                        }
                        st.session_state.sa_dup_replace_armed_target = None
                        st.rerun(scope="app")

            elif conf_state["type"] == "duplicate":
                st.error(
                    f"🔴 **Duplicate detected:** {unit_number} already has an entry on "
                    f"{event_start_date} with matching circuit start times. "
                    f"This entry has **not** been saved."
                )
                st.markdown("**Existing conflicting entry(ies):**")
                for rec in conf_state["records"]:
                    _summ = (
                        f"`{rec.get('id','')[:8]}…` — "
                        f"{rec.get('start_date','?')} / Unit {rec.get('unit_number','?')} / "
                        f"Patrol {rec.get('patrol_number','?')} / "
                        f"{len(rec.get('circuits', []))} circuit(s) / "
                        f"{round(rec.get('total_operating_minutes',0)/60, 2)} hrs"
                    )
                    st.markdown(f"- {_summ}")
                    with st.expander("Show full JSON"):
                        st.json(rec)

                # ── Primary action row: SAFE actions only ─────────────
                dup_col1, dup_col2 = st.columns(2)
                with dup_col1:
                    if st.button("← Cancel (keep existing)", key="sa_dup_cancel"):
                        st.session_state.sa_conflict_state = None
                        st.session_state.sa_dup_replace_armed_target = None
                        st.rerun(scope="app")
                with dup_col2:
                    if st.button("✅ Accept Both Entries",
                                 key="sa_dup_accept_both", type="primary"):
                        pending = dict(conf_state["pending"])
                        eid = conf_state.get("edit_id")
                        pending["conflict_status"] = "duplicate_confirmed"
                        _counterpart_ids = {r.get("id", "") for r in conf_state["records"]}
                        _new_short = pending.get("id", "")[:8] + "…"
                        _pending_anoms: list[str] = []
                        _cp_anom_map: dict[str, str] = {}
                        for _cp in conf_state["records"]:
                            _cp_id = _cp.get("id", "")
                            _n, _summary = _shared_window_summary(pending, _cp)
                            if _summary:
                                _pending_anoms.append(
                                    f"⚠️ Duplicate accepted — {_summary} with id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"⚠️ Duplicate accepted — {_summary} with id {_new_short}"
                                )
                            else:
                                _pending_anoms.append(
                                    f"⚠️ Duplicate accepted — coexists with id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"⚠️ Duplicate accepted — coexists with id {_new_short}"
                                )
                        pending["anomalies"] = list(pending.get("anomalies") or []) + _pending_anoms
                        new_sha = _do_save_push(
                            gh_config,
                            _with_cross_unit_detection(
                                _counterpart_mutator(
                                    pending, eid, _counterpart_ids,
                                    "duplicate_confirmed", _cp_anom_map,
                                ),
                                pending,
                            ),
                            f"{'Edit' if eid else 'Add'} entry (duplicate accepted, both retained): {unit_number} / {event_start_date}",
                            eid,
                        )
                        if new_sha:
                            st.success("✅ Both entries retained and flagged `duplicate_confirmed` with shared-minute detail.")
                            st.session_state.sa_conflict_state = None
                            st.session_state.sa_dup_replace_armed_target = None
                            st.rerun(scope="app")
                        # On failure: push_cache already displayed st.error; keep
                        # conflict UI up so the user can retry without losing pending.

                # ── DESTRUCTIVE path: hidden behind an expander ───────
                # Four intentional clicks (expand + arm + final Yes) plus
                # password. No typed input anywhere outside the password.
                # Record-scoped keys so switching targets clears state.
                _cp_ids_sorted = tuple(sorted(
                    r.get("id", "") for r in conf_state["records"] if r.get("id")
                ))
                _cp_short_str = ", ".join(i[:8] + "…" for i in _cp_ids_sorted)
                _armed_target = st.session_state.sa_dup_replace_armed_target
                # If the armed target doesn't match the current conflict (user
                # dismissed + hit a different conflict), clear it.
                if _armed_target and _armed_target != _cp_ids_sorted:
                    st.session_state.sa_dup_replace_armed_target = None
                    _armed_target = None

                with st.expander(
                    "🗑 Destructive: delete the existing record(s) and save this one instead",
                    expanded=False,
                ):
                    st.error(
                        "**Warning — permanent deletion.** Clicking through this flow will "
                        "remove the record(s) listed above from the cache. The new form is "
                        "then saved as a replacement in a single commit. **This cannot be undone.**"
                    )
                    st.markdown("**Will be permanently deleted:**")
                    for _cp in conf_state["records"]:
                        _cp_total_hrs = round(_cp.get("total_operating_minutes", 0) / 60, 2)
                        st.markdown(
                            f"- `{_cp.get('id','')[:8]}…` — "
                            f"{_cp.get('start_date','?')} / Unit {_cp.get('unit_number','?')} / "
                            f"Patrol {_cp.get('patrol_number','?')} / "
                            f"{len(_cp.get('circuits', []))} circuit(s) / "
                            f"{_cp_total_hrs} hrs / "
                            f"saved {_cp.get('saved_at','')[:19]}"
                        )

                    # Record-scoped widget keys — prevents autofill / residual
                    # state from carrying across different conflict targets.
                    _scope_key = "-".join(i[:8] for i in _cp_ids_sorted) or "empty"
                    _pw_key   = f"sa_dup_replace_pw_{_scope_key}"
                    _arm_key  = f"sa_dup_replace_arm_{_scope_key}"
                    _yes_key  = f"sa_dup_replace_final_{_scope_key}"
                    _no_key   = f"sa_dup_replace_cancel_{_scope_key}"

                    _pw = st.text_input(
                        "Deletion password",
                        type="password",
                        key=_pw_key,
                        placeholder="Password",
                    )

                    if _armed_target != _cp_ids_sorted:
                        # Arm state — require password + click to move to final confirm.
                        if st.button(
                            f"🗑 Delete record(s) {_cp_short_str} and save this new one",
                            key=_arm_key,
                        ):
                            if _pw != "benchmark":
                                st.error("Incorrect password. Destructive action not armed.")
                            else:
                                st.session_state.sa_dup_replace_armed_target = _cp_ids_sorted
                                st.rerun(scope="app")
                    else:
                        # Final confirmation layer.
                        st.error(
                            f"⚠️ **Last chance.** Click **Yes, permanently delete** below "
                            f"to remove {_cp_short_str} and save this new entry. "
                            f"Click **No, cancel** to back out — nothing will change."
                        )
                        _fc1, _fc2 = st.columns(2)
                        with _fc1:
                            if st.button(
                                f"✅ Yes, permanently delete {_cp_short_str}",
                                key=_yes_key,
                                type="primary",
                            ):
                                pending = dict(conf_state["pending"])
                                eid = conf_state.get("edit_id")
                                pending["conflict_status"] = "duplicate_replaced"
                                _replaced_ids = [r.get("id", "") for r in conf_state["records"]]
                                _replaced_short = ", ".join(i[:8] + "…" for i in _replaced_ids)
                                _anom = f"⚠️ Replaced existing record(s): {_replaced_short}"
                                pending["anomalies"] = list(pending.get("anomalies") or []) + [_anom]

                                _drop_ids = set(_replaced_ids)
                                if eid:
                                    _drop_ids.add(eid)

                                def _replace_mutator(recs, _drop=_drop_ids, _new=pending):
                                    return [r for r in recs if r.get("id") not in _drop] + [_new]

                                _commit_msg = (
                                    f"Replace record(s) [{_replaced_short}] with new entry: "
                                    f"{unit_number} / {event_start_date} — replaced by auditor"
                                )
                                new_sha = _do_save_push(
                                    gh_config,
                                    _with_cross_unit_detection(_replace_mutator, pending),
                                    _commit_msg, eid,
                                )
                                if new_sha:
                                    st.success(
                                        f"✅ Replaced {len(_replaced_ids)} existing record(s). "
                                        f"New entry flagged `duplicate_replaced`."
                                    )
                                    st.session_state.sa_conflict_state = None
                                    st.session_state.sa_dup_replace_armed_target = None
                                    st.rerun(scope="app")
                        with _fc2:
                            if st.button("← No, cancel", key=_no_key):
                                st.session_state.sa_dup_replace_armed_target = None
                                st.rerun(scope="app")

            elif conf_state["type"] == "overlap":
                st.warning(
                    f"🟡 **Time overlap detected:** {unit_number} / {event_start_date}. "
                    f"Circuit windows conflict with {len(conf_state['records'])} existing entry(ies). "
                    f"Saving would **double-count** these hours."
                )
                st.markdown("**Conflicting entries:**")
                for rec in conf_state["records"]:
                    _summ = (
                        f"`{rec.get('id','')[:8]}…` — "
                        f"{rec.get('start_date','?')} / Unit {rec.get('unit_number','?')} / "
                        f"Patrol {rec.get('patrol_number','?')} / "
                        f"{len(rec.get('circuits', []))} circuit(s) / "
                        f"{round(rec.get('total_operating_minutes',0)/60, 2)} hrs"
                    )
                    st.markdown(f"- {_summ}")
                    with st.expander("Show full JSON"):
                        st.json(rec)
                ov_col1, ov_col2 = st.columns(2)
                with ov_col1:
                    if st.button("← Cancel"):
                        st.session_state.sa_conflict_state = None
                        st.rerun(scope="app")
                with ov_col2:
                    if st.button("⚠️ Save Anyway + Flag as Overlap"):
                        pending = dict(conf_state["pending"])
                        eid = conf_state.get("edit_id")
                        pending["conflict_status"] = "overlap_confirmed"
                        _counterpart_ids = {r.get("id", "") for r in conf_state["records"]}
                        _new_short = pending.get("id", "")[:8] + "…"
                        _pending_anoms: list[str] = []
                        _cp_anom_map: dict[str, str] = {}
                        for _cp in conf_state["records"]:
                            _cp_id = _cp.get("id", "")
                            _n, _summary = _shared_window_summary(pending, _cp)
                            if _summary:
                                _pending_anoms.append(
                                    f"⚠️ Time overlap — {_summary} with id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"⚠️ Time overlap — {_summary} with id {_new_short}"
                                )
                            else:
                                _pending_anoms.append(
                                    f"⚠️ Time overlap with id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"⚠️ Time overlap with id {_new_short}"
                                )
                        pending["anomalies"] = list(pending.get("anomalies") or []) + _pending_anoms
                        new_sha = _do_save_push(
                            gh_config,
                            _with_cross_unit_detection(
                                _counterpart_mutator(
                                    pending, eid, _counterpart_ids,
                                    "overlap_confirmed", _cp_anom_map,
                                ),
                                pending,
                            ),
                            f"{'Edit' if eid else 'Add'} entry (overlap flagged — both sides, minute-level): {unit_number} / {event_start_date}",
                            eid,
                        )
                        if new_sha:
                            st.success("Saved with overlap flag; counterpart record(s) also flagged with shared-minute detail.")
                            st.session_state.sa_conflict_state = None
                            st.rerun(scope="app")
                        # On failure: push_cache already displayed st.error; keep
                        # conflict UI up so the user can retry without losing pending.

            elif conf_state["type"] == "same_day_no_overlap":
                n = len(conf_state["records"])
                st.info(
                    f"ℹ️ {unit_number} already has {n} entry(ies) on {event_start_date} "
                    f"with **no time overlap**. This is normal for fragmented contractor forms. "
                    f"Both entries will be included in season totals."
                )
                st.markdown("**Same-day entries:**")
                for rec in conf_state["records"]:
                    _summ = (
                        f"`{rec.get('id','')[:8]}…` — "
                        f"{rec.get('start_date','?')} / Unit {rec.get('unit_number','?')} / "
                        f"Patrol {rec.get('patrol_number','?')} / "
                        f"{len(rec.get('circuits', []))} circuit(s) / "
                        f"{round(rec.get('total_operating_minutes',0)/60, 2)} hrs"
                    )
                    st.markdown(f"- {_summ}")
                    with st.expander("Show full JSON"):
                        st.json(rec)
                sd_col1, sd_col2 = st.columns(2)
                with sd_col1:
                    if st.button("← Cancel"):
                        st.session_state.sa_conflict_state = None
                        st.rerun(scope="app")
                with sd_col2:
                    if st.button("✅ Confirm & Save"):
                        pending = dict(conf_state["pending"])
                        eid = conf_state.get("edit_id")
                        pending["conflict_status"] = "multiple_same_day"
                        _counterpart_ids = {r.get("id", "") for r in conf_state["records"]}
                        _new_short = pending.get("id", "")[:8] + "…"
                        # No time overlap expected here by definition, so the
                        # summary is usually empty. Still run it so any edge-case
                        # minute-adjacency is recorded.
                        _pending_anoms: list[str] = []
                        _cp_anom_map: dict[str, str] = {}
                        for _cp in conf_state["records"]:
                            _cp_id = _cp.get("id", "")
                            _n, _summary = _shared_window_summary(pending, _cp)
                            if _summary:
                                _pending_anoms.append(
                                    f"ℹ️ Multiple forms same unit/day — {_summary} with id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"ℹ️ Multiple forms same unit/day — {_summary} with id {_new_short}"
                                )
                            else:
                                _pending_anoms.append(
                                    f"ℹ️ Multiple forms same unit/day — see id {_cp_id[:8]}…"
                                )
                                _cp_anom_map[_cp_id] = (
                                    f"ℹ️ Multiple forms same unit/day — see id {_new_short}"
                                )
                        pending["anomalies"] = list(pending.get("anomalies") or []) + _pending_anoms
                        new_sha = _do_save_push(
                            gh_config,
                            _with_cross_unit_detection(
                                _counterpart_mutator(
                                    pending, eid, _counterpart_ids,
                                    "multiple_same_day", _cp_anom_map,
                                ),
                                pending,
                            ),
                            f"{'Edit' if eid else 'Add'} entry (multi-form same day — both sides flagged): {unit_number} / {event_start_date}",
                            eid,
                        )
                        if new_sha:
                            st.success("✅ Saved. Counterpart record(s) also flagged.")
                            st.session_state.sa_conflict_state = None
                            st.rerun(scope="app")
                        # On failure: push_cache already displayed st.error; keep
                        # conflict UI up so the user can retry without losing pending.

            # ── Download report ───────────────────────────────────────
            st.divider()
            st.subheader("📄 Download Audit Report")
            auditor = st.text_input("Auditor Name", placeholder="Full name", key="sa_auditor")
            meta_ok = bool(
                patrol_number.strip() and unit_number.strip() and auditor.strip()
            )
            if not meta_ok:
                st.caption("⚠️ Patrol #, Unit #, and Auditor Name required to enable download.")

            dl_col, reset_col = st.columns([2, 1])
            with dl_col:
                safe_unit  = unit_number.strip().replace(" ", "_") or "unit"
                safe_route = "_".join(res["routes_used"]) or "route"
                st.download_button(
                    "⬇ Download HTML Report",
                    data=build_report_html(
                        res, event_start_date,
                        patrol_number.strip() or "—",
                        unit_number.strip() or "—",
                        unit_type,
                        auditor.strip() or "—",
                        is_spare=is_spare,
                        primary_unit=primary_unit_number,
                        continues_to_next_form=res.get("continues_to_next_form", False),
                    ),
                    file_name=f"audit_{safe_unit}_{safe_route}_{event_start_date}.html",
                    mime="text/html",
                    disabled=not meta_ok,
                    key="sa_dl_btn",
                )
            with reset_col:
                # on_click callback — runs BEFORE the next render, so writes to
                # widget-bound keys (sa_continues, sa_patrol, sa_refuel_*, etc.)
                # are legal. Setting any widget-bound key in an inline post-widget
                # handler raises StreamlitAPIException.
                st.button("🔄 New Form", key="sa_reset", on_click=_clear_form_state)


# ───────────────────────────────────────────────────────────────────
# TAB 2: CACHE VIEWER & ANALYTICS
# ───────────────────────────────────────────────────────────────────
@st.fragment
def render_analytics_tab():
    st.subheader("Cache Viewer & Analytics")

    # gh_config + sa_cache_data + sa_benchmarks_loaded are hoisted to
    # module scope. Refresh Cache stays here — clearing the keys then
    # st.rerun(scope="app") forces the hoisted loaders to re-fetch on the next run.

    if st.button("🔄 Refresh Cache", key="sa_refresh"):
        for k in ("sa_cache_data", "sa_benchmarks_loaded"):
            if k in st.session_state:
                del st.session_state[k]
        st.session_state.sa_benchmarks = {}
        st.session_state.sa_benchmarks_sha = None
        st.session_state.sa_chain_cache = None
        st.session_state.sa_analytics_view = None
        st.rerun(scope="app")

    records = st.session_state.sa_cache_data

    if not records:
        st.info("No entries in cache yet. Use the Entry tab to add form data.")
        return  # exit fragment only — st.stop() would halt the whole app

    # ── Event Chain Cache ─────────────────────────────────────────────
    _ck = _get_chain_cache_key(records)
    if st.session_state.sa_chain_cache is None or st.session_state.sa_chain_cache.get("key") != _ck:
        _unit_recs: dict = {}
        for _r in records:
            _u = _r.get("primary_unit_number") if _r.get("is_spare") else _r.get("unit_number", "?")
            if not _u:
                _u = _r.get("unit_number", "?")
            _unit_recs.setdefault(_u, []).append(_r)
        st.session_state.sa_chain_cache = {
            "key": _ck,
            "chains": {_u: _build_event_chains(_recs) for _u, _recs in _unit_recs.items()},
        }
    all_chains = st.session_state.sa_chain_cache["chains"]

    # ── Analytics View Cache ──────────────────────────────────────────
    # Records → DataFrame + filter dropdown options + date bounds. Keyed
    # by the same SHA as sa_chain_cache and ALWAYS invalidated together.
    _av = st.session_state.get("sa_analytics_view")
    if _av is None or _av.get("key") != _ck:
        st.session_state.sa_analytics_view = {
            "key":  _ck,
            "view": _build_analytics_view(records),
        }
    _view            = st.session_state.sa_analytics_view["view"]
    df               = _view["df"]
    patrol_opts      = _view["patrol_opts"]
    unit_opts        = _view["unit_opts"]
    all_routes       = _view["all_routes"]
    route_opts       = ["All"] + all_routes
    min_date         = _view["min_date"]
    max_date         = _view["max_date"]
    all_cache_routes = _view["all_cache_routes"]

    # ── Filters ───────────────────────────────────────────────────────
    st.markdown("**Filters**")
    f1, f2, f3, f4, f5 = st.columns(5)
    with f1:
        f_patrol = st.selectbox("Patrol #", patrol_opts, key="sa_f_patrol")
    with f2:
        f_unit = st.selectbox("Unit #", unit_opts, key="sa_f_unit")
    with f3:
        f_route = st.selectbox("Route #", route_opts, key="sa_f_route")
    with f4:
        f_date_from = st.date_input("From", value=min_date, key="sa_f_from")
    with f5:
        f_date_to = st.date_input("To", value=max_date, key="sa_f_to")

    # Apply filters
    fdf = df.copy()
    if f_patrol != "All":
        fdf = fdf[fdf["Patrol"] == f_patrol]
    if f_unit != "All":
        fdf = fdf[fdf["Unit"] == f_unit]
    if f_route != "All":
        fdf = fdf[fdf["Routes"].str.contains(f_route, na=False)]
    _fdates = pd.to_datetime(fdf["Date"]).dt.date
    fdf = fdf[(_fdates >= f_date_from) & (_fdates <= f_date_to)]

    st.caption(f"Showing {len(fdf)} of {len(df)} entries")
    st.divider()

    # ── Manage Route Benchmarks ───────────────────────────────────────
    with st.expander("📋 Manage Route Benchmarks"):
        safe_bm = st.session_state.sa_benchmarks if isinstance(st.session_state.sa_benchmarks, dict) else {}
        st.caption(
            "Benchmark hours are pre-loaded from the contract table. "
            "Routes found in the cache are shown below with their contract values. "
            "Override a value only if it was amended — overrides are saved to GitHub."
        )

        # all_cache_routes is sourced from the analytics view cache above —
        # do not re-derive here.
        bm_edits = {}
        if all_cache_routes:
            bm_cols = st.columns([1, 1])
            for idx, rt in enumerate(all_cache_routes):
                contract_val, source = _lookup_benchmark(rt, safe_bm)
                label = f"{rt}  ({'contract' if source == 'contract' else 'override' if source == 'override' else '⚠️ not in table'})"
                col = bm_cols[idx % 2]
                with col:
                    bm_edits[rt] = st.number_input(
                        label,
                        min_value=0.0,
                        value=contract_val,
                        step=0.5,
                        key=f"bme_{rt}",
                        help="Edit only if contract value was amended.",
                    )
        else:
            st.info("No routes in cache yet.")

        st.markdown("**Override a route not in the contract table:**")
        add_col1, add_col2, add_col3 = st.columns([1, 1, 0.5])
        with add_col1:
            new_rt_id = st.text_input("Route ID", placeholder="e.g. WK3B", key="sa_bm_new_rt")
        with add_col2:
            new_rt_hrs = st.number_input("Benchmark hrs", min_value=0.0, value=0.0,
                                         step=0.5, key="sa_bm_new_hrs")
        with add_col3:
            st.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
            if st.button("➕ Add", key="sa_bm_add"):
                if new_rt_id.strip():
                    safe_bm[new_rt_id.strip()] = new_rt_hrs
                    st.session_state.sa_benchmarks = safe_bm
                    st.rerun(scope="app")

        if st.button("💾 Save Overrides to GitHub", key="sa_bm_save"):
            # Only save values that differ from the contract table (true overrides)
            overrides_only = {
                rt: v for rt, v in bm_edits.items()
                if v != _lookup_benchmark(rt, {})[0]   # differs from contract
            }
            new_sha = save_benchmarks(gh_config, overrides_only, st.session_state.sa_benchmarks_sha)
            if new_sha:
                st.session_state.sa_benchmarks = overrides_only
                st.session_state.sa_benchmarks_sha = new_sha
                st.toast(f"{len(overrides_only)} override(s) saved to GitHub.", icon="✅")
                st.rerun(scope="app")

    st.divider()

    # ── View Selector ─────────────────────────────────────────────────
    view = st.radio(
        "View",
        ["Submissions Table", "Hours by Unit", "Hours by Route",
         "Hours by Patrol", "Anomaly Log", "Conflicts & Flags", "Timeline",
         "Overclaim Report"],
        horizontal=True, key="sa_view"
    )
    st.divider()

    display_df = fdf.drop(columns=["_id"])

    if view == "Submissions Table":
        # ── Contained scrollable table with native row selection ──────
        # Align positional index with the ids list so selection.rows maps
        # deterministically even when fdf was derived via filtering.
        _fdf_r = fdf.reset_index(drop=True)
        _ids_by_pos = _fdf_r["_id"].tolist()
        _display_df_sel = _fdf_r.drop(columns=["_id"])

        # Toast for completed edit loads — surfaced at the top so operators see
        # it regardless of where they scrolled.
        if st.session_state.pop("sa_just_loaded", False):
            st.success("✅ Record loaded — switch to the **📝 Entry & Calculate** tab to review and edit.")

        st.caption(
            f"Showing {len(_fdf_r)} of {len(df)} records. "
            "Click a row to select it, then use **✏️ Edit** or **🗑 Delete** below."
        )

        if _fdf_r.empty:
            st.info("No records match the current filters.")
            _sel_event = None
        else:
            _sel_event = st.dataframe(
                _display_df_sel,
                use_container_width=True,
                hide_index=True,
                height=min(500, 42 + 36 * min(len(_fdf_r), 12)),
                on_select="rerun",
                selection_mode="single-row",
                key="sa_tbl_select",
            )

        csv = _display_df_sel.to_csv(index=False)
        st.download_button("⬇ Download Filtered CSV", csv,
                           "season_filtered.csv", "text/csv", key="sa_csv_dl")

        # ── Selection-driven delete flow ──────────────────────────────
        _selected_rows = []
        if _sel_event is not None:
            try:
                _selected_rows = list(_sel_event.selection.rows)
            except Exception:
                _selected_rows = []

        _selected_id = ""
        if _selected_rows:
            _sel_pos = _selected_rows[0]
            if 0 <= _sel_pos < len(_ids_by_pos):
                _selected_id = _ids_by_pos[_sel_pos]

        # If the currently-armed pending delete no longer matches selection
        # (user clicked a different row), clear the pending state so the
        # confirmation panel tracks the selection.
        _pending_del_id = st.session_state.sa_pending_delete
        if _pending_del_id and _pending_del_id != _selected_id:
            st.session_state.sa_pending_delete = None
            st.session_state.sa_pending_delete_confirmed = False
            _pending_del_id = None

        st.divider()
        st.markdown("**Actions on selected record**")

        if not _selected_id:
            st.caption("Select a row in the table above to enable Edit / Delete.")
        else:
            _rec_to_del = next((r for r in records if r.get("id") == _selected_id), None)
            if _rec_to_del is None:
                st.warning("Selected record no longer exists in the cache.")
            else:
                _del_label = (
                    f"Unit {_rec_to_del.get('unit_number', '?')} / "
                    f"{', '.join(_rec_to_del.get('routes_used', [])) or '—'} / "
                    f"{_rec_to_del.get('start_date', '?')} / "
                    f"id:{_selected_id[:8]}…"
                )

                if _pending_del_id != _selected_id:
                    # Idle state — show EDIT and DELETE side by side.
                    st.info(f"Selected: **{_del_label}**")

                    # Build the arguments _do_load_edit_from_selection needs
                    # NOW (at render time) — the callback closure captures them.
                    _erec = _rec_to_del
                    _circs = _erec.get("circuits", [])
                    _sa_circs_edit = []
                    for _c in _circs:
                        _sp = _c.get("start", "00:00").split(":")
                        _ep = _c.get("end",   "00:00").split(":")
                        _sa_circs_edit.append({
                            "start_h": int(_sp[0]), "start_m": int(_sp[1]),
                            "end_h":   int(_ep[0]), "end_m":   int(_ep[1]),
                            "route":    _c.get("route", ""),
                            "tow_plow": _c.get("tow_plow", False),
                        })
                    _n_evts_r = 1 + _erec.get("intra_form_new_events", 0)
                    _base_ref = _erec.get("refuel_minutes", 30) // max(1, _n_evts_r)

                    def _do_load_edit_from_selection(
                        _erec=_erec, _esel_id=_selected_id,
                        _sa_circs=_sa_circs_edit, _base_ref=_base_ref,
                    ):
                        # Frankenstein prevention: blank every widget-bound key
                        # for the current form BEFORE hydrating from the loaded
                        # record. If hydration later raises, the form is blank,
                        # not a hybrid of the previous and new data.
                        _clear_form_state()

                        # Hydrate header keys from loaded record
                        # Hydration guard: normalize legacy "Patrol 11" → "11"
                        # so the selectbox doesn't crash on pre-migration records.
                        # Unknown values (not in PATROL_OPTIONS) fall back to "".
                        _loaded_patrol = _normalize_patrol(_erec.get("patrol_number", ""))
                        st.session_state["sa_patrol"] = _loaded_patrol if _loaded_patrol in PATROL_OPTIONS else ""
                        st.session_state["sa_unit"]         = _erec.get("unit_number", "")
                        st.session_state["sa_is_spare"]     = _erec.get("is_spare", False)
                        st.session_state["sa_primary_unit"] = _erec.get("primary_unit_number", "")
                        st.session_state["sa_refuel_cb"]    = _base_ref > 0
                        st.session_state["sa_continues"]    = _erec.get("continues_to_next_form", False)
                        if _base_ref > 0:
                            st.session_state["sa_refuel_min"] = _base_ref
                        _ut = _erec.get("unit_type", "")
                        if _ut in UNIT_TYPES:
                            st.session_state["sa_unit_type"] = _ut
                        try:
                            st.session_state["sa_start_date"] = date.fromisoformat(_erec["start_date"])
                        except Exception:
                            pass
                        # Assign fresh circuit IDs so widget keys never collide
                        _start_id = st.session_state.sa_circuit_counter + 1
                        _load_mode = st.session_state.get("sa_time_mode", "HHMM (e.g. 0930)")
                        for _ci, _cc in enumerate(_sa_circs):
                            _cid = _start_id + _ci
                            _cc["id"] = _cid
                            if _load_mode == "H/M Boxes":
                                st.session_state[f"sa_sh_{_cid}"] = _cc["start_h"]
                                st.session_state[f"sa_sm_{_cid}"] = _cc["start_m"]
                                st.session_state[f"sa_eh_{_cid}"] = _cc["end_h"]
                                st.session_state[f"sa_em_{_cid}"] = _cc["end_m"]
                            elif _load_mode == "HHMM (e.g. 0930)":
                                st.session_state[f"sa_st_{_cid}"] = f"{_cc['start_h']:02d}{_cc['start_m']:02d}"
                                st.session_state[f"sa_et_{_cid}"] = f"{_cc['end_h']:02d}{_cc['end_m']:02d}"
                            else:  # HH:MM single box
                                st.session_state[f"sa_st_{_cid}"] = f"{_cc['start_h']:02d}:{_cc['start_m']:02d}"
                                st.session_state[f"sa_et_{_cid}"] = f"{_cc['end_h']:02d}:{_cc['end_m']:02d}"
                            st.session_state[f"sa_rt_{_cid}"] = _cc["route"]
                            st.session_state[f"sa_tp_{_cid}"] = _cc["tow_plow"]
                        st.session_state.sa_circuit_counter = _start_id + len(_sa_circs) - 1
                        st.session_state.sa_circuits          = _sa_circs if _sa_circs else st.session_state.sa_circuits
                        st.session_state.sa_editing_record_id = _esel_id
                        st.session_state.sa_calc_results      = None
                        st.session_state.sa_conflict_state    = None
                        st.session_state.sa_just_loaded       = True

                    _act_edit, _act_del = st.columns(2)
                    with _act_edit:
                        st.button(
                            "✏️ Edit this record",
                            key="sa_rowedit_btn",
                            type="primary",
                            on_click=_do_load_edit_from_selection,
                        )
                    with _act_del:
                        if st.button("🗑 Delete this record", key="sa_del_btn_tbl"):
                            st.session_state.sa_pending_delete = _selected_id
                            st.session_state.sa_pending_delete_confirmed = False
                            st.rerun(scope="app")
                elif not st.session_state.sa_pending_delete_confirmed:
                    # Second step: password.
                    st.warning(f"⚠️ Delete **{_del_label}**? Password required.")
                    _pw_col, _confirm_col, _cancel_col = st.columns([2, 1, 1])
                    with _pw_col:
                        _pw_val = st.text_input(
                            "Deletion password",
                            type="password",
                            key="sa_del_pw_tbl",
                        )
                    with _confirm_col:
                        st.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
                        if st.button("Confirm", key="sa_del_confirm_tbl"):
                            if _pw_val == "benchmark":
                                st.session_state.sa_pending_delete_confirmed = True
                                st.rerun(scope="app")
                            else:
                                st.error("Incorrect password.")
                    with _cancel_col:
                        st.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
                        if st.button("Cancel", key="sa_del_cancel1_tbl"):
                            st.session_state.sa_pending_delete = None
                            st.session_state.sa_pending_delete_confirmed = False
                            st.rerun(scope="app")
                else:
                    # Third step: final confirm.
                    st.error(f"⚠️ Permanently delete: **{_del_label}**? This cannot be undone.")
                    _yes_col, _no_col = st.columns(2)
                    with _yes_col:
                        if st.button("✅ Yes, Delete", type="primary", key="sa_del_yes_tbl"):
                            _commit_msg = (
                                f"Delete record {_selected_id[:8]}: "
                                f"{_rec_to_del.get('unit_number', '?')} / "
                                f"{','.join(_rec_to_del.get('routes_used', [])) or '—'} / "
                                f"{_rec_to_del.get('start_date', '?')} — deleted by auditor"
                            )
                            _del_id_cap = _selected_id
                            _delete_mutator_tbl = lambda recs: [r for r in recs if r.get("id") != _del_id_cap]
                            _result = push_cache(gh_config, _delete_mutator_tbl, _commit_msg)
                            if _result is not None:
                                _new_sha, _new_records = _result
                                st.success("✅ Record deleted.")
                                st.session_state.sa_cache_data     = _new_records
                                st.session_state.sa_chain_cache    = None
                                st.session_state.sa_analytics_view = None
                                st.session_state.sa_pending_delete = None
                                st.session_state.sa_pending_delete_confirmed = False
                                st.rerun(scope="app")
                    with _no_col:
                        if st.button("Cancel", key="sa_del_cancel2_tbl"):
                            st.session_state.sa_pending_delete = None
                            st.session_state.sa_pending_delete_confirmed = False
                            st.rerun(scope="app")

        # ── Per-row audit report downloads ────────────────────────────
        st.divider()
        with st.expander("📄 Download Per-Form Audit Reports", expanded=False):
            if fdf.empty:
                st.info("No records to download.")
            else:
                _tab2_auditor = st.text_input(
                    "Auditor name (appears on each report)",
                    placeholder="Full name",
                    key="sa_tab2_auditor",
                )
                st.caption(
                    f"{len(fdf)} report(s) available. "
                    "Report totals reflect all circuits on each individual form."
                )
                _srt_c1, _srt_c2 = st.columns([3, 1])
                with _srt_c1:
                    _dl_sort_col = st.selectbox(
                        "Sort by", ["Date", "Unit", "Routes", "Total Hours", "Patrol"],
                        key="sa_dl_sort_col"
                    )
                with _srt_c2:
                    _dl_sort_asc = (
                        st.radio("Order", ["↑ Asc", "↓ Desc"], horizontal=True, key="sa_dl_sort_dir")
                        == "↑ Asc"
                    )
                _dl_fdf = fdf.sort_values(_dl_sort_col, ascending=_dl_sort_asc).reset_index(drop=True)

                _hdr1, _hdr2, _hdr3, _hdr4, _hdr5 = st.columns([1.5, 1.5, 2.5, 1.2, 1.5])
                with _hdr1: st.markdown("**Date**")
                with _hdr2: st.markdown("**Unit**")
                with _hdr3: st.markdown("**Routes**")
                with _hdr4: st.markdown("**Hours**")
                with _hdr5: st.markdown("**Report**")
                for _, _row in _dl_fdf.iterrows():
                    _rec = next((r for r in records if r.get("id") == _row["_id"]), None)
                    if _rec is None:
                        continue
                    _dc1, _dc2, _dc3, _dc4, _dc5 = st.columns([1.5, 1.5, 2.5, 1.2, 1.5])
                    with _dc1: st.caption(_row["Date"])
                    with _dc2: st.caption(_row["Unit"])
                    with _dc3: st.caption(_row["Routes"])
                    with _dc4: st.caption(f"{_row['Total Hours']} hrs")
                    with _dc5:
                        _rr = _record_to_report_result(_rec)
                        _su = str(_rec.get("unit_number", "unit")).replace(" ", "_")
                        _sr = "_".join(_rec.get("routes_used", [])) or "route"
                        st.download_button(
                            "⬇ Report",
                            data=build_report_html(
                                _rr,
                                date.fromisoformat(_rec["start_date"]),
                                _rec.get("patrol_number") or "—",
                                _rec.get("unit_number") or "—",
                                _rec.get("unit_type") or "—",
                                _tab2_auditor.strip() or "Auditor",
                                is_spare=_rec.get("is_spare", False),
                                primary_unit=_rec.get("primary_unit_number") or "",
                                continues_to_next_form=_rr.get("continues_to_next_form", False),
                            ),
                            file_name=f"audit_{_su}_{_sr}_{_rec['start_date']}.html",
                            mime="text/html",
                            key=f"sa_dl_rec_{_rec.get('id', _row.name)}",
                        )


    elif view == "Hours by Unit":
        # Chain-based aggregation: refuel counted once per event, cross-form gaps capped correctly
        unit_rows = []
        for unit_key, chains in all_chains.items():
            if f_unit != "All" and unit_key != f_unit:
                continue
            total_hrs = 0.0
            tow_plow = False
            unit_type_val = ""
            form_count = 0
            event_count = 0

            for chain in chains:
                # At least one record in the chain must pass all active filters
                chain_ok = False
                for r in chain:
                    if f_patrol != "All" and r.get("patrol_number") != f_patrol:
                        continue
                    if f_route != "All" and f_route not in r.get("routes_used", []):
                        continue
                    rd = date.fromisoformat(r["start_date"])
                    if not (f_date_from <= rd <= f_date_to):
                        continue
                    chain_ok = True
                    break
                if not chain_ok:
                    continue

                ch = _compute_chain_hours(chain)
                total_hrs += ch["total_operating_min"] / 60
                form_count += len(chain)
                event_count += 1
                for r in chain:
                    if r.get("tow_plow_used"):
                        tow_plow = True
                    if not unit_type_val:
                        unit_type_val = r.get("unit_type", "")

            if event_count > 0:
                unit_rows.append({
                    "Unit": unit_key,
                    "Unit Type": unit_type_val,
                    "Total Hours": round(total_hrs, 2),
                    "# Events": event_count,
                    "# Forms": form_count,
                    "Tow Plow Used": tow_plow,
                })

        if unit_rows:
            summary_df = pd.DataFrame(unit_rows).sort_values("Total Hours", ascending=False)
            summary_df["Risk Rate"] = summary_df.apply(
                lambda row: "Enhanced" if row["Unit Type"] in TOWING_TYPES or row["Tow Plow Used"] else "Standard",
                axis=1,
            )
            st.dataframe(summary_df, hide_index=True, use_container_width=True)
            st.caption(
                "ℹ️ **Total Hours** are chain-recalculated — refuel counted once per event, "
                "gaps at form boundaries evaluated against the 60-min contract cap. "
                "**# Forms** = total forms submitted by contractor; **# Events** = grouped continuous winter events."
            )

            st.markdown("**Benchmark Progress** *(enter contracted hours to see % completion)*")
            for row in unit_rows:
                u = row["Unit"]
                bk = st.number_input(
                    f"Benchmark hrs — {u}", min_value=0.0, value=0.0,
                    step=0.5, key=f"bk_{u}"
                )
                if bk > 0:
                    pct = row["Total Hours"] / bk * 100
                    bar_len = min(int(pct / 5), 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    if pct > 110:
                        status = "🔴 Over benchmark"
                    elif pct >= 90:
                        status = "✅ On pace"
                    else:
                        status = "⚠️ Below pace"
                    st.markdown(
                        f"`{u}` &nbsp; Accumulated: **{row['Total Hours']:.1f} hrs** / {bk:.0f} hrs "
                        f"({pct:.1f}%) &nbsp; [{bar}] &nbsp; {status}"
                    )
        else:
            st.info("No data for current filters.")

    elif view == "Hours by Route":
        st.caption(
            "**Circuit Run Time** = actual driving time on each route. "
            "**Attributed Operating Hrs** = circuit time + sequential share of inter-circuit "
            "gaps and refuel, where each gap is assigned to the route of the preceding circuit. "
            "Hours are derived from event chains — refuel counted once per event, "
            "cross-form gaps evaluated against the 60-min contract cap. "
            "Use Attributed Operating Hrs for benchmark comparison."
        )

        # Build per-route circuit time AND attributed operating hours (chain-level)
        route_circuit: dict = {}
        route_attributed: dict = {}
        route_circuits_count: dict = {}

        for unit_key, chains in all_chains.items():
            if f_unit != "All" and unit_key != f_unit:
                continue
            for chain in chains:
                # At least one record in the chain must pass filters
                chain_ok = False
                for r in chain:
                    if f_patrol != "All" and r.get("patrol_number") != f_patrol:
                        continue
                    if f_route != "All" and f_route not in r.get("routes_used", []):
                        continue
                    rd = date.fromisoformat(r["start_date"])
                    if not (f_date_from <= rd <= f_date_to):
                        continue
                    chain_ok = True
                    break
                if not chain_ok:
                    continue
                # Raw circuit time per route
                for r in chain:
                    for c in r.get("circuits", []):
                        rt = c.get("route", "—") or "—"
                        if f_route != "All" and rt != f_route:
                            continue
                        route_circuit[rt] = route_circuit.get(rt, 0) + c.get("duration_min", 0)
                        route_circuits_count[rt] = route_circuits_count.get(rt, 0) + 1
                # Chain-level attributed hours
                for rt, hrs in _attribute_chain_hours(chain).items():
                    if f_route != "All" and rt != f_route:
                        continue
                    route_attributed[rt] = route_attributed.get(rt, 0) + hrs

        if route_circuit:
            all_routes_in_view = sorted(route_circuit.keys())
            table_rows = []
            for rt in all_routes_in_view:
                table_rows.append({
                    "Route": rt,
                    "Circuit Run Time (hrs)": round(route_circuit[rt] / 60, 2),
                    "Attributed Operating (hrs)": round(route_attributed.get(rt, 0), 2),
                    "# Circuits": route_circuits_count.get(rt, 0),
                })
            st.dataframe(pd.DataFrame(table_rows), hide_index=True, use_container_width=True)

            # ── Benchmark Progress ─────────────────────────────────────
            safe_bm = st.session_state.sa_benchmarks if isinstance(
                st.session_state.sa_benchmarks, dict) else {}

            routes_with_bm    = [rt for rt in all_routes_in_view
                                  if _lookup_benchmark(rt, safe_bm)[0] > 0]
            routes_without_bm = [rt for rt in all_routes_in_view
                                  if _lookup_benchmark(rt, safe_bm)[0] == 0]

            if routes_with_bm:
                st.divider()
                st.markdown("**Benchmark Progress by Route**")
                st.caption(
                    "Attributed Operating Hrs vs contracted benchmark. Status band: ✅ 0–110% · 🔴 >110%."
                )

                prog_rows = []
                for rt in routes_with_bm:
                    attr_hrs = round(route_attributed.get(rt, 0), 2)
                    bk_hrs, source = _lookup_benchmark(rt, safe_bm)
                    pct  = attr_hrs / bk_hrs * 100
                    low  = bk_hrs * 0.90
                    high = bk_hrs * 1.10
                    if pct > 110:
                        status = "🔴 Over benchmark"
                    elif pct >= 90:
                        status = "✅ On pace"
                    else:
                        status = "⚠️ Below pace"
                    canonical = _BENCHMARK_CANONICAL.get(_norm_route(rt), rt)
                    prog_rows.append({
                        "Route": rt,
                        "Contract ID": canonical,
                        "Attr. Operating (hrs)": attr_hrs,
                        "Benchmark (hrs)": bk_hrs,
                        "% Complete": round(pct, 1),
                        "Band (±10%)": f"{low:.0f}–{high:.0f} hrs",
                        "Status": status,
                        "Source": source,
                    })

                st.dataframe(pd.DataFrame(prog_rows), hide_index=True, use_container_width=True)

                for row in prog_rows:
                    bar_len = min(int(row["% Complete"] / 5), 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    src_note = "" if row["Source"] == "contract" else " *(override)*"
                    st.markdown(
                        f"`{row['Route']}` &nbsp; **{row['Attr. Operating (hrs)']:.2f} hrs** "
                        f"/ {row['Benchmark (hrs)']:.0f} hrs{src_note} ({row['% Complete']:.1f}%) "
                        f"&nbsp; [{bar}] &nbsp; {row['Status']} "
                        f"&nbsp; <span style='color:#888;font-size:12px'>"
                        f"band: {row['Band (±10%)']}</span>",
                        unsafe_allow_html=True,
                    )

            if routes_without_bm:
                st.caption(
                    f"Routes not found in contract table: **{', '.join(routes_without_bm)}** — "
                    f"add an override in *Manage Route Benchmarks* above."
                )
        else:
            st.info("No data for current filters.")

    elif view == "Hours by Patrol":
        psummary = (
            fdf.groupby("Patrol")
            .agg(Total_Hours=("Total Hours", "sum"), Units=("Unit", "nunique"), Forms=("Total Hours", "count"))
            .reset_index()
            .rename(columns={"Total_Hours": "Total Hours", "Units": "# Units", "Forms": "# Forms"})
            .sort_values("Total Hours", ascending=False)
        )
        psummary["Total Hours"] = psummary["Total Hours"].round(2)
        st.dataframe(psummary, hide_index=True, use_container_width=True)
        st.caption("**# Forms** = total submitted forms across all units in this patrol. Each vehicle may contribute multiple forms per winter event.")

    elif view == "Anomaly Log":
        adf = display_df[display_df["Anomalies"] != ""][
            ["Date", "Patrol", "Unit", "Routes", "Total Hours", "Anomalies"]
        ]
        if adf.empty:
            st.success("No anomalies in the current filtered set.")
        else:
            st.dataframe(adf, hide_index=True, use_container_width=True)
            st.caption(f"{len(adf)} entries with anomalies.")

    elif view == "Conflicts & Flags":
        # Admin controls: Rescan + Normalize Patrol.
        _rs_col1, _rs_col2, _rs_col3 = st.columns([3, 1, 1])
        with _rs_col1:
            st.caption(
                "**Rescan cache** walks every record and tags both sides of any "
                "same-unit/same-day conflict that currently shows as `clean`. "
                "**Normalize Patrol numbers** strips any leading 'Patrol' from stored "
                "records so filter values consolidate. Both are safe to run any time."
            )
        with _rs_col2:
            st.markdown("<div style='padding-top:8px'></div>", unsafe_allow_html=True)
            if st.button("🔍 Rescan cache for conflicts", key="sa_rescan_btn"):
                # Preview count from the already-loaded records…
                with st.spinner("Scanning cache for unflagged conflicts..."):
                    _preview_updated, _n_upd = rescan_conflicts(records)
                if _n_upd == 0:
                    st.success("✅ No unflagged conflicts found — cache is clean.")
                else:
                    # …but the mutator re-scans the *fresh* cache so a 409
                    # retry stays idempotent and safe against concurrent writes.
                    def _rescan_mutator(_recs):
                        _upd, _ = rescan_conflicts(_recs)
                        return _upd
                    _commit_msg = f"Rescan: tag {_n_upd} record(s) with missing conflict flags"
                    _result = push_cache(gh_config, _rescan_mutator, _commit_msg)
                    if _result is not None:
                        _new_sha, _new_records = _result
                        st.session_state.sa_cache_data     = _new_records
                        st.session_state.sa_chain_cache    = None
                        st.session_state.sa_analytics_view = None
                        st.success(f"✅ Updated {_n_upd} record(s). Reloading…")
                        st.rerun(scope="app")
        with _rs_col3:
            st.markdown("<div style='padding-top:8px'></div>", unsafe_allow_html=True)
            if st.button("📋 Normalize Patrol numbers", key="sa_normalize_patrol_btn"):
                # Preview count on the already-loaded records.
                _n_changes = sum(
                    1 for _r in records
                    if _normalize_patrol(_r.get("patrol_number", "")) != _r.get("patrol_number", "")
                )
                if _n_changes == 0:
                    st.success("✅ All patrol numbers already normalized.")
                else:
                    # Mutator re-runs on fresh cache — idempotent under 409 retry.
                    def _normalize_patrol_mutator(_recs):
                        _out = []
                        for _r in _recs:
                            _norm = _normalize_patrol(_r.get("patrol_number", ""))
                            if _norm != _r.get("patrol_number", ""):
                                _r = dict(_r)
                                _r["patrol_number"] = _norm
                            _out.append(_r)
                        return _out
                    _commit_msg = (
                        f"Normalize patrol_number: {_n_changes} record(s) "
                        f"updated (strip 'Patrol' prefix)"
                    )
                    _result = push_cache(gh_config, _normalize_patrol_mutator, _commit_msg)
                    if _result is not None:
                        _new_sha, _new_records = _result
                        st.session_state.sa_cache_data     = _new_records
                        st.session_state.sa_chain_cache    = None
                        st.session_state.sa_analytics_view = None
                        st.success(f"✅ Normalized {_n_changes} record(s). Reloading…")
                        st.rerun(scope="app")

        # Precompute per-record covered minute sets once for the Shared column.
        # Only same-unit pairs can share minutes; bucket first, then intersect.
        _covered_by_id: dict[str, set[int]] = {}
        _units_by_id: dict[str, str] = {}
        for _r in records:
            _rid = _r.get("id", "")
            if not _rid:
                continue
            _covered_by_id[_rid] = _record_covered_minutes(_r)
            _units_by_id[_rid] = _r.get("unit_number", "")

        # Fast lookup: for each unit, the set of (rid, covered_set) pairs.
        _by_unit: dict[str, list[tuple[str, set[int]]]] = {}
        for _rid, _u in _units_by_id.items():
            _by_unit.setdefault(_u, []).append((_rid, _covered_by_id[_rid]))

        def _tolerance_filtered_overlap(a_cov: set[int], b_cov: set[int]) -> int:
            """Size of the minute intersection AFTER dropping contiguous sub-intervals
            ≤ OVERLAP_TOLERANCE_MIN (boundary / rounding artifacts)."""
            inter = a_cov & b_cov
            if not inter:
                return 0
            kept = sum(
                (e - s) for (s, e) in _contiguous_intervals_from_minutes(inter)
                if (e - s) > OVERLAP_TOLERANCE_MIN
            )
            return kept

        def _shared_min_for_record(rid: str) -> int:
            """Unique minutes this record shares (above tolerance) with any same-unit counterpart.
            Per-record figure — summing across rows double-counts each pair. Use the banner
            computation below for the authoritative dataset-wide total."""
            covered = _covered_by_id.get(rid, set())
            if not covered:
                return 0
            unit = _units_by_id.get(rid, "")
            # Union the kept-overlap minute sets across all counterparts, so the
            # per-record figure counts each of this record's at-risk minutes once.
            kept_union: set[int] = set()
            for other_rid, other_covered in _by_unit.get(unit, []):
                if other_rid == rid:
                    continue
                inter = covered & other_covered
                if not inter:
                    continue
                for (s, e) in _contiguous_intervals_from_minutes(inter):
                    if (e - s) > OVERLAP_TOLERANCE_MIN:
                        kept_union.update(range(s, e))
            return len(kept_union)

        # Join per-record overlap minutes onto the filtered frame.
        _fdf_with_shared = fdf.copy()
        _fdf_with_shared["Overlap min (this record)"] = (
            _fdf_with_shared["_id"].map(_shared_min_for_record).fillna(0).astype(int)
        )

        flagged = _fdf_with_shared[
            (_fdf_with_shared["Flags"] != "clean") | (_fdf_with_shared["Out of Season"] == "⚠️")
        ][["Date", "Patrol", "Unit", "Routes", "Total Hours",
           "Overlap min (this record)", "Flags", "Out of Season", "Anomalies"]]
        if flagged.empty:
            st.success("No conflicts or flags in the current filtered set.")
        else:
            # Authoritative pairwise total: walk each same-unit pair within the
            # flagged set EXACTLY ONCE and sum the tolerance-filtered overlap.
            _flagged_ids = set(_fdf_with_shared[
                (_fdf_with_shared["Flags"] != "clean") | (_fdf_with_shared["Out of Season"] == "⚠️")
            ]["_id"].tolist())
            _pair_count = 0
            _pairwise_min = 0
            for _u, _bucket in _by_unit.items():
                _flagged_bucket = [(r, c) for (r, c) in _bucket if r in _flagged_ids]
                for _i in range(len(_flagged_bucket)):
                    _a_rid, _a_cov = _flagged_bucket[_i]
                    for _j in range(_i + 1, len(_flagged_bucket)):
                        _b_rid, _b_cov = _flagged_bucket[_j]
                        _pm = _tolerance_filtered_overlap(_a_cov, _b_cov)
                        if _pm > 0:
                            _pairwise_min += _pm
                            _pair_count += 1

            if _pair_count > 0:
                st.error(
                    f"⚠️ **{_pair_count} pair(s) with impossible overlap** — "
                    f"**{_pairwise_min / 60:.2f} hrs total unique overlap** across the flagged set. "
                    "The Hours by Unit / Route / Patrol views already dedupe these at the "
                    "minute level, so billing totals are correct. The Anomalies column names "
                    "the exact time ranges that overlap with each counterpart."
                )
            else:
                st.warning(
                    f"{len(flagged)} flagged entries require review (no impossible overlap "
                    f"above the {OVERLAP_TOLERANCE_MIN}-min tolerance)."
                )
            st.dataframe(flagged, hide_index=True, use_container_width=True)
            st.caption(
                f"**Overlap min (this record)** = minutes of this record's operating window "
                f"that are also claimed on another same-unit form, filtered to contiguous "
                f"sub-intervals longer than {OVERLAP_TOLERANCE_MIN} minutes. This is a "
                f"**per-record** figure — **summing the column across rows double-counts** "
                f"each pair. The red banner above computes the authoritative pairwise total. "
                f"**Flag types:** "
                f"`overlap_confirmed` = time overlap saved by auditor (both sides flagged); "
                f"`multiple_same_day` = multiple forms same unit/date (both sides flagged); "
                f"`spare_overlap` = spare + primary both on same date; "
                f"`duplicate_confirmed` = duplicate detected, auditor chose to retain both forms (both sides flagged); "
                f"`duplicate_replaced` = duplicate detected, this new record replaced one or more prior entries."
            )

    elif view == "Timeline":
        st.caption(
            "Chronological event chains per unit. Each chain = one continuous winter event "
            "(3-hour gap rule). Multiple contractor forms covering the same event are grouped "
            "together. Multi-form events auto-expand — gap at each form boundary is shown with "
            "its contract classification."
        )

        filtered_units: set = set()
        for r in records:
            u = r.get("primary_unit_number") if r.get("is_spare") else r.get("unit_number", "?")
            if not u:
                u = r.get("unit_number", "?")
            if f_unit != "All" and u != f_unit:
                continue
            if f_patrol != "All" and r.get("patrol_number") != f_patrol:
                continue
            if f_route != "All" and f_route not in r.get("routes_used", []):
                continue
            rd = date.fromisoformat(r["start_date"])
            if not (f_date_from <= rd <= f_date_to):
                continue
            filtered_units.add(u)

        if not filtered_units:
            st.info("No data for current filters.")
        else:
            for unit_key in sorted(filtered_units):
                chains = all_chains.get(unit_key, [])
                if not chains:
                    continue
                st.markdown(f"#### Unit: {unit_key}")

                for chain_idx, chain in enumerate(chains):
                    chain_ok = any(
                        (f_patrol == "All" or r.get("patrol_number") == f_patrol) and
                        (f_route == "All" or f_route in r.get("routes_used", [])) and
                        f_date_from <= date.fromisoformat(r["start_date"]) <= f_date_to
                        for r in chain
                    )
                    if not chain_ok:
                        continue

                    ch = _compute_chain_hours(chain)
                    total_hrs = ch["total_operating_min"] / 60
                    n_forms = len(chain)

                    all_chain_dates = [date.fromisoformat(r["start_date"]) for r in chain]
                    chain_first = min(all_chain_dates)
                    last_rec = chain[-1]
                    last_off = max((c.get("day_offset", 0) for c in last_rec.get("circuits", [])), default=0)
                    chain_last = date.fromisoformat(last_rec["start_date"]) + timedelta(days=last_off)
                    date_range_str = (
                        chain_first.strftime("%b %d, %Y") if chain_first == chain_last
                        else f"{chain_first.strftime('%b %d')} – {chain_last.strftime('%b %d, %Y')}"
                    )
                    routes_in_chain = sorted({
                        c.get("route", "—")
                        for r in chain for c in r.get("circuits", [])
                        if c.get("route")
                    })
                    multi_label = f"  ⚠️ **{n_forms} forms — chain detected**" if n_forms > 1 else ""

                    with st.expander(
                        f"Event {chain_idx + 1}: {date_range_str}  │  "
                        f"{n_forms} form{'s' if n_forms > 1 else ''}  │  "
                        f"{total_hrs:.2f} hrs  │  "
                        f"Routes: {', '.join(routes_in_chain)}{multi_label}",
                        expanded=(n_forms > 1),
                    ):
                        rows_data = []
                        for i, rec in enumerate(chain):
                            rec_base = date.fromisoformat(rec["start_date"])
                            circuits = rec.get("circuits", [])
                            if circuits:
                                fc, lc = circuits[0], circuits[-1]
                                start_str = fc["start"]
                                end_day = rec_base + timedelta(days=lc.get("day_offset", 0))
                                end_str = lc["end"]
                                date_str = (
                                    rec_base.strftime("%b %d")
                                    if rec_base == end_day
                                    else f"{rec_base.strftime('%b %d')} – {end_day.strftime('%b %d')}"
                                )
                            else:
                                start_str = end_str = "—"
                                date_str = rec_base.strftime("%b %d")

                            if i < len(chain) - 1:
                                gap_min = _record_abs_start(chain[i + 1]) - _record_abs_end(rec)
                                if gap_min <= 0:
                                    gap_label = f"{abs(gap_min)} min (overlap)"
                                elif gap_min <= 60:
                                    gap_label = f"{gap_min} min → Operating (full)"
                                elif gap_min <= 180:
                                    excl = gap_min - 60
                                    gap_label = f"{gap_min} min → Capped at 60 min (+{excl} min excluded)"
                                else:
                                    gap_label = f"{gap_min} min → NEW EVENT (split)"
                            else:
                                gap_label = "(end of event)"

                            rec_routes = ", ".join(sorted({
                                c.get("route", "—") for c in circuits if c.get("route")
                            })) or "—"

                            rows_data.append({
                                "Form #": i + 1,
                                "Date": date_str,
                                "Start → End": f"{start_str} → {end_str}",
                                "Routes": rec_routes,
                                "Gap to Next Form": gap_label,
                            })

                        st.dataframe(pd.DataFrame(rows_data), hide_index=True, use_container_width=True)

                        m1, m2, m3 = st.columns(3)
                        with m1:
                            st.metric("Chain Total", f"{total_hrs:.2f} hrs")
                        with m2:
                            st.metric("Refuel (1×)", f"{ch['refuel_min']} min")
                        with m3:
                            if n_forms > 1:
                                naive_min = sum(r.get("total_operating_minutes", 0) for r in chain)
                                overclaim = naive_min - ch["total_operating_min"]
                                if overclaim > 0:
                                    st.metric(
                                        "Contractor Overclaim",
                                        f"{overclaim / 60:.1f} hrs",
                                        delta=f"-{overclaim / 60:.1f} hrs corrected",
                                        delta_color="inverse",
                                    )
                                else:
                                    st.metric("Forms", str(n_forms))
                            else:
                                st.metric("Forms", "1")

                st.divider()

    elif view == "Overclaim Report":
        st.caption(
            "One row per **unit** per continuous service period (chain). Each vehicle working "
            "during the same storm appears as a separate row — the number of rows is not the "
            "number of storms. Only chains with multiple forms can have an overclaim (single-form "
            "chains always show 0 excess)."
        )
        _oc_rows = []
        for _oc_unit in sorted(all_chains.keys()):
            if f_unit != "All" and _oc_unit != f_unit:
                continue
            for _oc_chain in all_chains[_oc_unit]:
                _chain_ok = any(
                    (f_patrol == "All" or r.get("patrol_number") == f_patrol) and
                    (f_route  == "All" or f_route in r.get("routes_used", [])) and
                    f_date_from <= date.fromisoformat(r["start_date"]) <= f_date_to
                    for r in _oc_chain
                )
                if not _chain_ok:
                    continue
                _ch = _compute_chain_hours(_oc_chain)
                _contractor_min = sum(r.get("total_operating_minutes", 0) for r in _oc_chain)
                _audited_min    = _ch["total_operating_min"]
                _excess_min     = max(0, _contractor_min - _audited_min)
                _all_dates   = [date.fromisoformat(r["start_date"]) for r in _oc_chain]
                _chain_first = min(_all_dates)
                _last_r      = _oc_chain[-1]
                _last_off    = max(
                    (c.get("day_offset", 0) for c in _last_r.get("circuits", [])), default=0
                )
                _chain_last  = date.fromisoformat(_last_r["start_date"]) + timedelta(days=_last_off)
                _routes = ", ".join(sorted({
                    c.get("route", "")
                    for r in _oc_chain for c in r.get("circuits", []) if c.get("route")
                }))
                _oc_rows.append({
                    "Unit":                   _oc_unit,
                    "Patrol":                 _oc_chain[0].get("patrol_number", "—"),
                    "Event Start":            _chain_first.isoformat(),
                    "Event End":              _chain_last.isoformat(),
                    "Routes":                 _routes,
                    "# Forms":                len(_oc_chain),
                    "Contractor Claimed Hrs": round(_contractor_min / 60, 2),
                    "Audited Hrs":            round(_audited_min / 60, 2),
                    "Excess Hrs":             round(_excess_min / 60, 2),
                })

        if not _oc_rows:
            st.info("No data for current filters.")
        else:
            _oc_df = pd.DataFrame(_oc_rows)
            _n_overclaim = int((_oc_df["Excess Hrs"] > 0).sum())
            st.caption(f"{len(_oc_df)} unit-chain(s) · {_n_overclaim} unit-chain(s) with overclaim")
            st.dataframe(_oc_df, hide_index=True, use_container_width=True)
            _om1, _om2 = st.columns(2)
            with _om1:
                st.metric("Total Excess Hours",    f"{_oc_df['Excess Hrs'].sum():.2f} hrs")
            with _om2:
                st.metric("Unit-Chains with Overclaim", str(_n_overclaim))
            st.download_button(
                "⬇ Download Overclaim Report CSV",
                _oc_df.to_csv(index=False),
                "overclaim_report.csv", "text/csv", key="sa_oc_csv",
            )


# ───────────────────────────────────────────────────────────────────
# TAB 3: AUDITOR GUIDE
# ───────────────────────────────────────────────────────────────────
@st.fragment
def render_guide_tab():
    st.subheader("Auditor Guide")
    gh_config_guide = get_github_config()
    if gh_config_guide is None:
        st.warning("GitHub not configured — guide cannot be fetched.")
    else:
        if st.button("🔄 Refresh Guide", key="sa_guide_refresh"):
            if "sa_guide_content" in st.session_state:
                del st.session_state["sa_guide_content"]

        if "sa_guide_content" not in st.session_state:
            with st.spinner("Loading guide from GitHub..."):
                _gh = gh_config_guide
                _g_headers = {
                    "Authorization": f"token {_gh['token']}",
                    "Accept": "application/vnd.github.v3+json",
                }
                _g_url = (
                    f"https://api.github.com/repos/{_gh['repo']}"
                    f"/contents/docs/auditor_guide.md?ref={_gh['branch']}"
                )
                try:
                    _g_resp = requests.get(_g_url, headers=_g_headers, timeout=10)
                    if _g_resp.status_code == 404:
                        st.session_state.sa_guide_content = None
                    else:
                        _g_resp.raise_for_status()
                        _g_meta = _g_resp.json()
                        _g_b64 = _g_meta.get("content") or ""
                        if _g_meta.get("encoding") == "none" or not _g_b64:
                            _g_dl = _g_meta.get("download_url")
                            if _g_dl:
                                _g_raw = requests.get(_g_dl, headers=_g_headers, timeout=20)
                                _g_raw.raise_for_status()
                                _g_bytes = _g_raw.content
                            else:
                                _g_blob_url = (
                                    f"https://api.github.com/repos/{_gh['repo']}"
                                    f"/git/blobs/{_g_meta['sha']}"
                                )
                                _g_blob = requests.get(_g_blob_url, headers=_g_headers, timeout=20)
                                _g_blob.raise_for_status()
                                _g_bytes = base64.b64decode(_g_blob.json()["content"])
                        else:
                            _g_bytes = base64.b64decode(_g_b64)
                        st.session_state.sa_guide_content = (
                            _decompress_if_gzipped(_g_bytes).decode("utf-8")
                        )
                except (
                    requests.exceptions.RequestException,
                    KeyError,
                    binascii.Error,
                    UnicodeDecodeError,
                    OSError,
                ) as _g_e:
                    # Guide is non-critical — warn (don't halt) and let
                    # the rest of the app render.
                    st.warning(f"Could not load guide: {_g_e}")
                    st.session_state.sa_guide_content = None

        _guide_content = st.session_state.get("sa_guide_content")
        if _guide_content is None:
            st.info(
                f"Guide not found. Create `docs/auditor_guide.md` in "
                f"`{gh_config_guide['repo']}` to enable this tab."
            )
        else:
            st.markdown(_guide_content)


# ═══════════════════════════════════════════════════════════════════
# Render — each fragment populates its tab. Widget interactions
# inside a fragment trigger fragment-only reruns (the speedup);
# tab-switch clicks are outside fragments and trigger app reruns.
# ═══════════════════════════════════════════════════════════════════
with tab_entry:
    render_entry_tab()
with tab_analytics:
    render_analytics_tab()
with tab_guide:
    render_guide_tab()
