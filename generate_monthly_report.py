#!/usr/bin/env python3
"""
generate_monthly_report.py
Regenerate the January 2026 Benchmark Hours Audit Report from live bh-data cache.

Usage:
    python3 generate_monthly_report.py [--month YYYY-MM] [--out PATH]

Credentials: reads from .streamlit/secrets.toml in this directory, then
~/.streamlit/secrets.toml, then env var BH_GITHUB_TOKEN (with defaults for
data_repo/branch/data_path).
"""

import argparse
import base64
import binascii
import gzip
import json
import os
import pathlib
import sys
from collections import defaultdict
from datetime import date, timedelta

import requests

# ═══════════════════════════════════════════════════════════════════
# Constants — copied from seasonal_aggregator.py
# ═══════════════════════════════════════════════════════════════════

_GZIP_MAGIC = b"\x1f\x8b"
TOWING_TYPES = {"Combination Unit - Wide Wing", "Tow Plow"}
OVERLAP_TOLERANCE_MIN = 2

# ═══════════════════════════════════════════════════════════════════
# Pure computation functions — verbatim copies from seasonal_aggregator.py
# (no Streamlit imports here)
# ═══════════════════════════════════════════════════════════════════

def _decompress_if_gzipped(data: bytes) -> bytes:
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    return data


def _norm_route(r: str) -> str:
    return r.upper().replace("-", "").replace(" ", "")


def _norm_route_ayr_expand(n: str) -> str:
    """Expand AYR shorthand: 'A1A' → 'AYR1A', 'A2B' → 'AYR2B'.
    Cache stores AYR routes as 'A1A'/'A-1A'; benchmarks use 'AYR-1A'."""
    if len(n) >= 2 and n[0] == 'A' and n[1].isdigit():
        return "AYR" + n[1:]
    return n


def _circuit_absolute_windows(record: dict) -> list:
    base_date = date.fromisoformat(record["start_date"])
    windows = []
    for c in record.get("circuits", []):
        sh, sm = map(int, c["start"].split(":"))
        eh, em = map(int, c["end"].split(":"))
        day_off = c.get("day_offset", 0)
        abs_day = (base_date + timedelta(days=day_off)).toordinal()
        w_start = abs_day * 1440 + sh * 60 + sm
        w_end   = abs_day * 1440 + eh * 60 + em
        if w_end <= w_start:
            w_end += 1440
        windows.append((w_start, w_end))
    return windows


def _merge_intervals(intervals: list) -> list:
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


def _record_covered_minutes(record: dict) -> set:
    covered: set = set()
    for s, e in _circuit_absolute_windows(record):
        covered.update(range(s, e))
    return covered


def _fmt_abs_minute_range(s: int, e: int) -> str:
    def _hhmm(m: int) -> str:
        return f"{(m % 1440) // 60:02d}:{m % 60:02d}"
    day_span = (e - 1) // 1440 - s // 1440
    if day_span <= 0:
        return f"{_hhmm(s)}-{_hhmm(e)}"
    return f"{_hhmm(s)}-{_hhmm(e)} (+{day_span}d)"


def _contiguous_intervals_from_minutes(mins: set) -> list:
    if not mins:
        return []
    sorted_mins = sorted(mins)
    intervals = []
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


def _shared_window_summary(rec_a: dict, rec_b: dict,
                           max_intervals: int = 3,
                           tolerance_min: int = OVERLAP_TOLERANCE_MIN):
    a = _record_covered_minutes(rec_a)
    b = _record_covered_minutes(rec_b)
    shared = a & b
    if not shared:
        return 0, ""
    intervals = _contiguous_intervals_from_minutes(shared)
    kept = [(s, e) for (s, e) in intervals if (e - s) > tolerance_min]
    if not kept:
        return 0, ""
    total = sum(e - s for (s, e) in kept)
    txt_parts = [_fmt_abs_minute_range(s, e) for s, e in kept[:max_intervals]]
    if len(kept) > max_intervals:
        txt_parts.append(f"+{len(kept) - max_intervals} more")
    return total, f"{total} min shared ({', '.join(txt_parts)})"


def _record_abs_start(record: dict) -> int:
    circuits = record.get("circuits", [])
    if not circuits:
        return 0
    base = date.fromisoformat(record["start_date"])
    c = circuits[0]
    sh, sm = map(int, c["start"].split(":"))
    off = c.get("day_offset", 0)
    return (base + timedelta(days=off)).toordinal() * 1440 + sh * 60 + sm


def _record_abs_end(record: dict) -> int:
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


def _build_event_chains(records_for_unit: list) -> list:
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


def _combined_circuit_seq(chain: list) -> list:
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
            seq.append({
                "route":    c.get("route") or "—",
                "tow_plow": bool(c.get("tow_plow")),
                "s": s, "e": e,
                "dur": c.get("duration_min", 0),
            })
    seq.sort(key=lambda x: x["s"])
    return seq


def _merged_chain_windows(chain: list) -> list:
    seq = _combined_circuit_seq(chain)
    if not seq:
        return []
    merged = []
    for c in seq:
        if not merged:
            merged.append({"s": c["s"], "e": c["e"], "route": c["route"], "tow_plow": c["tow_plow"]})
            continue
        last = merged[-1]
        if c["s"] <= last["e"]:
            if c["e"] > last["e"]:
                last["e"] = c["e"]
        else:
            merged.append({"s": c["s"], "e": c["e"], "route": c["route"], "tow_plow": c["tow_plow"]})
    return merged


def _compute_chain_hours(chain: list) -> dict:
    if not chain:
        return {"total_operating_min": 0, "tp_operating_min": 0, "std_operating_min": 0,
                "circuit_min_by_route": {}, "gap_operating_min": 0, "refuel_min": 0}
    merged = _merged_chain_windows(chain)
    if not merged:
        return {"total_operating_min": 0, "tp_operating_min": 0, "std_operating_min": 0,
                "circuit_min_by_route": {}, "gap_operating_min": 0, "refuel_min": 0}

    circuit_min_by_route: dict = {}
    total_circuit_min = 0
    tp_circuit_min = 0
    for m in merged:
        dur = m["e"] - m["s"]
        circuit_min_by_route[m["route"]] = circuit_min_by_route.get(m["route"], 0) + dur
        total_circuit_min += dur
        if m["tow_plow"]:
            tp_circuit_min += dur

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
            intra_form_refuels += base_refuel
            continue
        gap_operating_min += min(max(gap, 0), 60)

    end_refuel = base_refuel if (not _continues and _total_refuel > 0) else 0
    total_operating_min = total_circuit_min + gap_operating_min + intra_form_refuels + end_refuel

    # TP/Std split: proportional to raw circuit minutes
    tp_frac = tp_circuit_min / total_circuit_min if total_circuit_min > 0 else 0.0
    tp_operating_min = round(total_operating_min * tp_frac)
    std_operating_min = total_operating_min - tp_operating_min

    return {
        "total_operating_min": total_operating_min,
        "tp_operating_min":    tp_operating_min,
        "std_operating_min":   std_operating_min,
        "circuit_min_by_route": circuit_min_by_route,
        "gap_operating_min":   gap_operating_min,
        "refuel_min":          intra_form_refuels + end_refuel,
    }


def _attribute_chain_hours(chain: list) -> dict:
    merged = _merged_chain_windows(chain)
    if not merged:
        return {}

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


# ═══════════════════════════════════════════════════════════════════
# GitHub data loading (no Streamlit)
# ═══════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    """
    Load GitHub config from (in order):
      1. .streamlit/secrets.toml in this script's directory
      2. ~/.streamlit/secrets.toml
      3. Environment variable BH_GITHUB_TOKEN (data_repo must also be set or use default)
    """
    script_dir = pathlib.Path(__file__).parent

    try:
        import tomllib as _toml_mod
        def _toml_load(p):
            with open(p, "rb") as f:
                return _toml_mod.load(f)
    except ImportError:
        import tomli as _toml_mod  # type: ignore
        def _toml_load(p):
            with open(p, "rb") as f:
                return _toml_mod.load(f)

    for candidate in [
        script_dir / ".streamlit" / "secrets.toml",
        pathlib.Path.home() / ".streamlit" / "secrets.toml",
    ]:
        if candidate.exists():
            try:
                s = _toml_load(candidate)
                gh = s.get("github", {})
                if gh.get("token"):
                    print(f"Using credentials from {candidate}")
                    return {
                        "token":     gh["token"],
                        "data_repo": gh.get("data_repo", gh.get("repo", "RWCS-LTD/bh-data")),
                        "branch":    gh.get("branch", "main"),
                        "data_path": gh.get("data_path", "data/season_cache.json"),
                    }
            except Exception as e:
                print(f"Warning: could not read {candidate}: {e}", file=sys.stderr)

    token = os.environ.get("BH_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        print("Using credentials from environment variable.")
        return {
            "token":     token,
            "data_repo": os.environ.get("BH_DATA_REPO", "RWCS-LTD/bh-data"),
            "branch":    os.environ.get("BH_BRANCH", "main"),
            "data_path": os.environ.get("BH_DATA_PATH", "data/season_cache.json"),
        }

    raise RuntimeError(
        "No GitHub credentials found.\n"
        "Create .streamlit/secrets.toml in the benchmark_hours directory "
        "(see CLAUDE.md for format) or set BH_GITHUB_TOKEN environment variable."
    )


def _github_get_bytes(cfg: dict, path: str) -> bytes | None:
    """Fetch raw bytes from bh-data repo. Returns None on 404."""
    headers = {
        "Authorization": f"token {cfg['token']}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = (
        f"https://api.github.com/repos/{cfg['data_repo']}"
        f"/contents/{path}?ref={cfg['branch']}"
    )
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    meta = resp.json()
    sha = meta["sha"]
    content_b64 = meta.get("content") or ""
    encoding = meta.get("encoding")

    if encoding == "none" or not content_b64:
        dl = meta.get("download_url")
        if dl:
            raw = requests.get(dl, headers=headers, timeout=30)
            raw.raise_for_status()
            return raw.content
        blob_url = (
            f"https://api.github.com/repos/{cfg['data_repo']}"
            f"/git/blobs/{sha}"
        )
        blob = requests.get(blob_url, headers=headers, timeout=30)
        blob.raise_for_status()
        return base64.b64decode(blob.json()["content"])

    return base64.b64decode(content_b64)


def fetch_cache(cfg: dict) -> list:
    print("Fetching cache from bh-data …", flush=True)
    raw = _github_get_bytes(cfg, cfg["data_path"])
    if raw is None:
        raise RuntimeError(f"Cache file not found: {cfg['data_path']}")
    text = _decompress_if_gzipped(raw).decode("utf-8")
    records = json.loads(text)
    print(f"  Loaded {len(records)} records from cache.")
    return records


def fetch_benchmark_overrides(cfg: dict) -> dict:
    """Fetch data/benchmarks.json overrides. Returns {} on 404."""
    print("Fetching benchmark overrides from bh-data …", flush=True)
    raw = _github_get_bytes(cfg, "data/benchmarks.json")
    if raw is None:
        print("  No benchmarks.json overrides file found (OK — using contract table only).")
        return {}
    overrides = json.loads(raw.decode("utf-8"))
    print(f"  Loaded {len(overrides)} benchmark override(s).")
    return overrides


# ═══════════════════════════════════════════════════════════════════
# Benchmark table — all routes including AYR corridor
# ═══════════════════════════════════════════════════════════════════

_CONTRACT_BENCHMARKS = {
    # Routes with Jan billing records (Section 4 of existing report)
    "P-3A": 200, "P-1": 200, "J-3": 120, "P-6": 120,
    "G-2": 420, "P-2": 210, "G-1": 300, "P-5": 250,
    "C-1": 330, "P-4": 420, "WK-4": 390, "W-5": 260,
    "WK-1A": 346, "K-4": 260, "WK-3": 440, "G-4": 360,
    "K-2A": 410, "WK-2A": 313, "G-3A": 380, "K-1E": 380,
    "W-7": 430, "WK-2B": 170, "J-2": 225, "T-2": 530,
    "K-5": 320, "K-3": 450, "J-1": 370, "W-2": 310,
    "W-1": 310, "T-3": 470, "T-1": 470, "K-1F": 400,
    "W-6": 430, "K-1A": 140, "WK-1B": 193, "K-1C": 140,
    "K-1D": 140, "K-1B": 140, "AC-1": 150, "P-3B": 200,
    "K-2B": 320, "AC-2A": 200, "G-3B": 330,
    "NH-1": 500, "NH-2": 530, "S-2": 460, "S-3": 260, "S-1": 500,
    # No Jan individual records (Section 4 sub-table)
    "AC-2B": 160, "WK-1C": 193, "WK-2C": 170,
    # Combined billing codes (W3 = W-3A+W-3B+W-3C, W4 = W-4A+W-4B+W-4C)
    "W3": 820, "W4": 740,
    # Individual sub-route codes (in case used separately in any record)
    "W-3A": 420, "W-3B": 200, "W-3C": 200,
    "W-4A": 440, "W-4B": 150, "W-4C": 150,
    # AYR corridor (Patrol 13) — canonical keys; cache may store as 'A1A'/'A-1A'
    "AYR-1A": 400, "AYR-1B": 200, "AYR-1C": 200,
    "AYR-2A": 100, "AYR-2B": 100, "AYR-2C": 100, "AYR-2D": 100,
    "AYR-2E": 100, "AYR-2F": 260, "AYR-2G": 280,
    "AYR-3": 370, "AYR-4": 320, "AYR-5": 320,
}

_CONTRACT_NORM = {_norm_route(k): v for k, v in _CONTRACT_BENCHMARKS.items()}
_CONTRACT_CANONICAL = {_norm_route(k): k for k in _CONTRACT_BENCHMARKS}


def _lookup_benchmark(route: str, overrides: dict) -> tuple:
    norm = _norm_route(route)
    if route in overrides:
        return float(overrides[route]), "override"
    for k, v in overrides.items():
        if _norm_route(k) == norm:
            return float(v), "override"
    if norm in _CONTRACT_NORM:
        return float(_CONTRACT_NORM[norm]), "contract"
    expanded = _norm_route_ayr_expand(norm)
    if expanded != norm and expanded in _CONTRACT_NORM:
        return float(_CONTRACT_NORM[expanded]), "contract"
    return 0.0, "unknown"


def build_benchmark_table(overrides: dict) -> dict:
    """Merge contract table with overrides. Keys are canonical route strings."""
    table = dict(_CONTRACT_BENCHMARKS)
    for k, v in overrides.items():
        table[k] = float(v)
    return table


# ═══════════════════════════════════════════════════════════════════
# Report computation
# ═══════════════════════════════════════════════════════════════════

PATROL_ORDER = ["11", "12", "13", "14", "15", "16"]

CLEAN_FLAGS = (None, "", "clean", "multiple_same_day")
# multiple_same_day = same unit/day, no time overlap = normal multi-form submission.
# Not a billing anomaly; excluded from Section 3 flagged groups.

BILLING_FLAGS = {"duplicate_confirmed", "duplicate_replaced", "overlap_confirmed", "spare_overlap"}

FLAG_SEVERITY = {
    "duplicate_confirmed": 3,
    "duplicate_replaced":  3,
    "overlap_confirmed":   2,
}

FLAG_LABEL = {
    "duplicate_confirmed": "DUPLICATE",
    "duplicate_replaced":  "REPLACED",
    "overlap_confirmed":   "OVERLAP",
    "multiple_same_day":   "OVERLAP",
}


def _effective_unit(r: dict) -> str:
    if r.get("is_spare") and r.get("primary_unit_number"):
        return r["primary_unit_number"]
    return r.get("unit_number", "?")


def _effective_patrol(r: dict) -> str:
    p = str(r.get("patrol_number", "")).strip()
    # Strip legacy "Patrol " prefix
    if p.lower().startswith("patrol"):
        p = p[6:].strip()
    return p if p else "Unassigned"


def build_unit_chains(jan_records: list) -> dict:
    """Build event chains per effective unit from January records."""
    unit_recs: dict = defaultdict(list)
    for r in jan_records:
        unit_recs[_effective_unit(r)].append(r)
    return {u: _build_event_chains(recs) for u, recs in unit_recs.items()}


def compute_patrol_summaries(jan_records: list, unit_chains: dict) -> dict:
    """
    Returns per-patrol dict:
    {patrol: {submissions, unique_unit_days, reported_hrs, reported_tp_hrs, reported_std_hrs,
              audited_hrs, audited_tp_hrs, audited_std_hrs, n_flagged_groups}}
    """
    # Patrol of each unit = majority patrol across its Jan records
    unit_patrol: dict = {}
    for r in jan_records:
        u = _effective_unit(r)
        p = _effective_patrol(r)
        unit_patrol.setdefault(u, defaultdict(int))[p] += 1
    resolved_unit_patrol = {
        u: max(counts, key=counts.get)
        for u, counts in unit_patrol.items()
    }

    results = {}
    patrols_seen = set(_effective_patrol(r) for r in jan_records)

    for patrol in PATROL_ORDER + sorted(patrols_seen - set(PATROL_ORDER)):
        p_recs = [r for r in jan_records if _effective_patrol(r) == patrol]
        if not p_recs and patrol not in PATROL_ORDER:
            continue

        submissions   = len(p_recs)
        unique_ud     = len({(_effective_unit(r), r.get("start_date", "")) for r in p_recs})
        reported_hrs  = sum(r.get("total_operating_minutes", 0) / 60 for r in p_recs)
        reported_tp   = sum(
            r.get("total_operating_minutes", 0) / 60
            for r in p_recs if r.get("tow_plow_used")
        )
        reported_std  = reported_hrs - reported_tp

        # Audited hours: sum chain hours for units whose majority patrol = this patrol
        audited_hrs     = 0.0
        audited_tp_hrs  = 0.0
        audited_std_hrs = 0.0
        for unit, chains in unit_chains.items():
            if resolved_unit_patrol.get(unit) != patrol:
                continue
            for chain in chains:
                ch = _compute_chain_hours(chain)
                audited_hrs     += ch["total_operating_min"] / 60
                audited_tp_hrs  += ch["tp_operating_min"] / 60
                audited_std_hrs += ch["std_operating_min"] / 60

        # Count flagged (unit, date) groups — billing anomalies only (dup/overlap)
        flagged_uds = {
            (_effective_unit(r), r.get("start_date", ""))
            for r in p_recs
            if r.get("conflict_status") in BILLING_FLAGS
        }
        n_flagged_groups = len(flagged_uds)

        results[patrol] = {
            "submissions":    submissions,
            "unique_ud":      unique_ud,
            "reported_hrs":   reported_hrs,
            "reported_tp":    reported_tp,
            "reported_std":   reported_std,
            "audited_hrs":    audited_hrs,
            "audited_tp":     audited_tp_hrs,
            "audited_std":    audited_std_hrs,
            "n_flagged":      n_flagged_groups,
            "variance_hrs":   audited_hrs - reported_hrs,
        }

    # Unassigned
    ua_recs = [r for r in jan_records if _effective_patrol(r) not in set(PATROL_ORDER)]
    if ua_recs:
        sub = len(ua_recs)
        rep = sum(r.get("total_operating_minutes", 0) / 60 for r in ua_recs)
        results["Unassigned"] = {
            "submissions":  sub,
            "unique_ud":    len({(_effective_unit(r), r.get("start_date", "")) for r in ua_recs}),
            "reported_hrs": rep,
            "reported_tp":  sum(r.get("total_operating_minutes", 0) / 60 for r in ua_recs if r.get("tow_plow_used")),
            "reported_std": rep - sum(r.get("total_operating_minutes", 0) / 60 for r in ua_recs if r.get("tow_plow_used")),
            "audited_hrs":  rep,
            "audited_tp":   sum(r.get("total_operating_minutes", 0) / 60 for r in ua_recs if r.get("tow_plow_used")),
            "audited_std":  rep - sum(r.get("total_operating_minutes", 0) / 60 for r in ua_recs if r.get("tow_plow_used")),
            "n_flagged":    0,
            "variance_hrs": 0.0,
        }

    return results


def compute_flagged_groups(jan_records: list) -> list:
    """
    Returns list of dicts for Section 3 — per-patrol flagged billing groups.
    Each dict: patrol, unit, date, flag_type, routes, excess_hrs, is_tp, rate, est_dollar
    """
    flagged = [r for r in jan_records if r.get("conflict_status") in BILLING_FLAGS]
    # Group by (patrol, unit, date)
    groups: dict = {}
    for r in flagged:
        key = (_effective_patrol(r), _effective_unit(r), r.get("start_date", ""))
        groups.setdefault(key, []).append(r)

    result = []
    for (patrol, unit, dt), recs in groups.items():
        reported_mins = sum(r.get("total_operating_minutes", 0) for r in recs)
        ch = _compute_chain_hours(recs)
        audited_mins = ch["total_operating_min"]
        excess_hrs = max(0.0, (reported_mins - audited_mins) / 60)

        severity = max(FLAG_SEVERITY.get(r.get("conflict_status", ""), 0) for r in recs)
        flag_label = next(
            (FLAG_LABEL[r.get("conflict_status", "")]
             for r in sorted(recs, key=lambda x: FLAG_SEVERITY.get(x.get("conflict_status", ""), 0), reverse=True)
             if r.get("conflict_status") in FLAG_LABEL),
            "FLAG"
        )
        # DUP+REPLACED if both duplicate and replaced statuses present
        statuses = {r.get("conflict_status", "") for r in recs}
        if "duplicate_confirmed" in statuses and "duplicate_replaced" in statuses:
            flag_label = "DUP+REPLACED"
        elif "duplicate_replaced" in statuses and "overlap_confirmed" not in statuses:
            flag_label = "REPLACED"

        routes = sorted({rt for r in recs for rt in r.get("routes_used", [])})
        is_tp  = any(r.get("tow_plow_used") for r in recs)
        rate   = 100 if is_tp else 75
        est_dollar = round(excess_hrs * rate)

        result.append({
            "patrol":     patrol,
            "unit":       unit,
            "date":       dt,
            "flag_type":  flag_label,
            "routes":     routes,
            "excess_hrs": excess_hrs,
            "is_tp":      is_tp,
            "rate":       rate,
            "est_dollar": est_dollar,
        })

    # Sort by patrol order then excess descending
    patrol_idx = {p: i for i, p in enumerate(PATROL_ORDER)}
    result.sort(key=lambda x: (patrol_idx.get(x["patrol"], 99), -x["excess_hrs"]))
    return result


def compute_route_utilization(unit_chains: dict, benchmarks: dict) -> list:
    """
    Returns list of dicts: route, season_benchmark, jan_actual_hrs, jan_tp_hrs,
    utilisation_pct, status
    """
    route_hrs: dict = defaultdict(float)
    route_tp_hrs: dict = defaultdict(float)

    for unit, chains in unit_chains.items():
        for chain in chains:
            attr = _attribute_chain_hours(chain)
            # TP attribution — proportional from chain computation
            ch = _compute_chain_hours(chain)
            total_chain_min = ch["total_operating_min"]
            tp_chain_min = ch["tp_operating_min"]
            tp_frac = tp_chain_min / total_chain_min if total_chain_min > 0 else 0.0

            for route, hrs in attr.items():
                route_hrs[route] += hrs
                route_tp_hrs[route] += hrs * tp_frac

    rows = []
    warned_unknown = []
    for route, bm in benchmarks.items():
        jan_hrs = route_hrs.get(route, 0.0)
        # Also try norm-match if route label differs by case/dash, or AYR shorthand
        if jan_hrs == 0.0:
            norm = _norm_route(route)
            for r_key, r_val in route_hrs.items():
                rk_norm = _norm_route(r_key)
                if rk_norm == norm or _norm_route_ayr_expand(rk_norm) == norm:
                    jan_hrs = r_val
                    route_tp_hrs[route] = route_tp_hrs.get(r_key, 0.0)
                    break
        jan_tp = route_tp_hrs.get(route, 0.0)
        util_pct = (jan_hrs / bm * 100) if bm > 0 else 0.0
        if util_pct > 100:
            status = "EXCEEDED"
        elif util_pct >= 70:
            status = "Critical"
        elif util_pct >= 40:
            status = "Moderate"
        elif util_pct >= 10:
            status = "Healthy"
        elif util_pct >= 5:
            status = "Low Activity"
        else:
            status = "Minimal"
        rows.append({
            "route":     route,
            "benchmark": bm,
            "jan_hrs":   jan_hrs,
            "jan_tp":    jan_tp,
            "util_pct":  util_pct,
            "status":    status,
        })

    # Routes found in data but not in benchmark table
    for route in route_hrs:
        norm = _norm_route(route)
        expanded = _norm_route_ayr_expand(norm)
        if not any(_norm_route(r) == norm or _norm_route(r) == expanded for r in benchmarks):
            warned_unknown.append(route)
            rows.append({
                "route":     route,
                "benchmark": 0.0,
                "jan_hrs":   route_hrs[route],
                "jan_tp":    route_tp_hrs.get(route, 0.0),
                "util_pct":  0.0,
                "status":    "Unknown",
            })

    if warned_unknown:
        print(f"WARNING: {len(warned_unknown)} route(s) in cache but not in benchmark table: "
              f"{', '.join(sorted(warned_unknown))}", file=sys.stderr)

    rows.sort(key=lambda x: -x["util_pct"])
    return rows


# ═══════════════════════════════════════════════════════════════════
# HTML generation
# ═══════════════════════════════════════════════════════════════════

CSS = """
@page {
  size: A4 landscape;
  margin: 1.5cm 1.8cm;
  @top-center { content: "January 2026 Benchmark Hours Audit — CONFIDENTIAL"; font-size:9pt; color:#888; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size:9pt; color:#888; }
}
*{ box-sizing:border-box; }
body{ font-family:'Segoe UI',Arial,sans-serif; font-size:10.5pt; color:#1a1a1a; line-height:1.45; margin:0; padding:0; }
h1{ font-size:20pt; margin:0 0 4px 0; color:#0d2b45; }
h2{ font-size:13pt; color:#0d2b45; border-bottom:2px solid #0d2b45; padding-bottom:4px; margin:24px 0 10px 0; page-break-after:avoid; }
h3{ font-size:11pt; color:#1a4a6e; margin:16px 0 6px 0; page-break-after:avoid; }
p{ margin:6px 0; } ul{ margin:4px 0; padding-left:18px; } li{ margin:3px 0; }
.meta-block{ background:#f0f4f8; border-left:4px solid #0d2b45; padding:10px 14px; margin:8px 0 12px 0; font-size:9.5pt; }
.meta-block td{ padding:1px 14px 1px 0; vertical-align:top; }
.callout{ background:#fff8e1; border-left:4px solid #f0a500; padding:8px 12px; margin:8px 0; font-size:9.5pt; }
.callout.info{ background:#e8f4fd; border-left-color:#1a6fa0; }
.callout.red{ background:#fff0f0; border-left-color:#c0392b; }
.callout.green{ background:#e8f5e9; border-left-color:#388e3c; }
.recon-box { border:2px solid #0d2b45; background:#f0f4f8; padding:10px 14px; margin:12px 0; page-break-inside:avoid; }
.recon-box h4 { margin:0 0 8px 0; font-size:11pt; color:#0d2b45; }
.recon-grid { width:100%; border-collapse:collapse; }
.recon-grid td { padding:3px 10px 3px 0; border:none; vertical-align:top; font-size:9.5pt; }
.recon-grid td:first-child { font-weight:700; color:#555; width:32%; }
.recon-grid td.val { font-size:11pt; font-weight:700; }
table{ width:100%; border-collapse:collapse; font-size:9.5pt; margin:6px 0 12px 0; page-break-inside:avoid; }
th{ background:#0d2b45; color:#fff; padding:5px 8px; text-align:left; font-weight:600; }
th.num,td.num{ text-align:right; }
td{ padding:4px 8px; border-bottom:1px solid #e0e0e0; vertical-align:top; }
tr:nth-child(even){ background:#f7f9fc; }
tr.total-row td{ background:#dce8f0; font-weight:700; border-top:2px solid #0d2b45; }
.row-high td{ background:#fdecea !important; }
.row-med td{ background:#fff3e0 !important; }
.row-low td{ background:#fffde7 !important; }
.row-low-act td{ background:#e8eaf6 !important; }
.row-minimal td{ background:#ede7f6 !important; }
.row-zero td{ opacity:0.6; }
.badge{ display:inline-block; padding:2px 7px; border-radius:3px; font-size:8.5pt; font-weight:600; }
.badge.red{ background:#fdecea; color:#c0392b; border:1px solid #c0392b; }
.badge.orange{ background:#fff3e0; color:#b35900; border:1px solid #e07000; }
.badge.green{ background:#e8f5e9; color:#1b5e20; border:1px solid #388e3c; }
.badge.grey{ background:#f0f0f0; color:#555; border:1px solid #bbb; }
.badge-type{ display:inline-block; padding:2px 6px; border-radius:3px; font-size:8pt; font-weight:700; }
.badge-dup{ background:#fdecea; color:#c0392b; }
.badge-ovl{ background:#fff3e0; color:#b35900; }
.badge-rep{ background:#e8eaf6; color:#283593; }
.neg{ color:#c0392b; font-weight:600; }
.pos{ color:#1b7a34; font-weight:600; }
.dimmed{ color:#aaa; }
code{ background:#eee; padding:1px 4px; border-radius:2px; font-size:9pt; }
.patrol-section{ margin-bottom:16px; page-break-inside:avoid; }
.no-flag h3{ color:#1b5e20; }
.green-note{ color:#1b5e20; background:#e8f5e9; padding:6px 10px; border-radius:3px; }
.util-exceeded{ color:#c0392b; font-weight:700; }
.util-critical{ color:#b35900; font-weight:700; }
.util-moderate{ color:#7b6000; }
.util-healthy{ color:#1b5e20; }
.util-low_activity{ color:#4a148c; font-weight:600; }
.util-minimal{ color:#880e4f; font-weight:700; }
.util-unknown{ color:#555; font-style:italic; }
.section-break{ page-break-before:always; }
.legend{ font-size:8.5pt; color:#555; margin:4px 0 8px 0; }
.summary-box{ border:1px solid #ccc; padding:10px 14px; margin:10px 0; background:#fafafa; page-break-inside:avoid; }
.summary-box table{ margin:4px 0; }
.summary-box td{ border:none; padding:2px 10px 2px 0; font-size:10pt; }
"""

STATUS_CSS = {
    "EXCEEDED":     "util-exceeded",
    "Critical":     "util-critical",
    "Moderate":     "util-moderate",
    "Healthy":      "util-healthy",
    "Low Activity": "util-low_activity",
    "Minimal":      "util-minimal",
    "Unknown":      "util-unknown",
}

ROW_CLASS = {
    "EXCEEDED":     "row-high",
    "Critical":     "row-med",
    "Moderate":     "row-low",
    "Healthy":      "",
    "Low Activity": "row-low-act",
    "Minimal":      "row-minimal",
    "Unknown":      "",
}

FLAG_CSS = {
    "DUPLICATE":     "badge-dup",
    "DUP+REPLACED":  "badge-dup",
    "REPLACED":      "badge-rep",
    "OVERLAP":       "badge-ovl",
    "FLAG":          "badge-ovl",
}


def _fmt(v: float, decimals: int = 2) -> str:
    return f"{v:,.{decimals}f}"


def _variance_cell(v: float) -> str:
    cls = "neg" if v < -0.005 else "pos" if v > 0.005 else ""
    sign = "−" if v < -0.005 else "+" if v > 0.005 else ""
    return f'<span class="{cls}">{sign}{_fmt(abs(v))}</span>'


def _pct_cell(v: float) -> str:
    cls = "neg" if v < -0.05 else "pos" if v > 0.05 else ""
    sign = "−" if v < -0.05 else "+" if v > 0.05 else ""
    return f'<span class="{cls}">{sign}{_fmt(abs(v), 1)}%</span>'


def _badge(label: str, css: str) -> str:
    return f'<span class="badge {css}">{label}</span>'


def _badge_type(label: str) -> str:
    css = FLAG_CSS.get(label, "badge-ovl")
    return f'<span class="badge-type {css}">{label}</span>'


def build_html(
    month_label: str,
    generated: str,
    jan_records: list,
    patrol_summaries: dict,
    flagged_groups: list,
    route_util: list,
    benchmarks: dict,
    all_records_count: int,
    excluded_count: int,
) -> str:

    total_season_benchmark = sum(benchmarks.values())

    # ── Section 1 totals ─────────────────────────────────────────
    total_subs = sum(ps["submissions"] for ps in patrol_summaries.values())
    total_ud   = sum(ps["unique_ud"]   for ps in patrol_summaries.values())
    total_rep  = sum(ps["reported_hrs"] for ps in patrol_summaries.values())
    total_aud  = sum(ps["audited_hrs"]  for ps in patrol_summaries.values())
    total_var  = total_aud - total_rep
    total_flagged = sum(ps["n_flagged"] for ps in patrol_summaries.values())
    gross_excess = sum(g["excess_hrs"] for g in flagged_groups)
    gross_dollar = sum(g["est_dollar"] for g in flagged_groups)

    # ── Section 2 totals ─────────────────────────────────────────
    total_rep_tp  = sum(ps["reported_tp"]  for ps in patrol_summaries.values())
    total_rep_std = sum(ps["reported_std"] for ps in patrol_summaries.values())
    total_aud_tp  = sum(ps["audited_tp"]   for ps in patrol_summaries.values())
    total_aud_std = sum(ps["audited_std"]  for ps in patrol_summaries.values())

    # ── Route summary stats ───────────────────────────────────────
    exceeded_routes    = [r for r in route_util if r["status"] == "EXCEEDED"]
    critical_routes    = [r for r in route_util if r["status"] == "Critical"]
    low_act_routes     = [r for r in route_util if r["status"] == "Low Activity"]
    minimal_routes     = [r for r in route_util if r["status"] == "Minimal"]

    # ── Budget burn rate (Section 5) ─────────────────────────────
    # January × 6.5 extrapolation is not used — January consumed a large share of the
    # full season benchmark in a single month, making calendar-proportional projection unreliable.
    jan_burn_pct       = total_aud / total_season_benchmark * 100 if total_season_benchmark > 0 else 0
    remaining_budget   = max(0.0, total_season_benchmark - total_aud)
    gross_dollar_low   = gross_excess * 75
    gross_dollar_high  = gross_excess * 100
    # Amount patrols 15/16 undercharged (positive variance, no flags) — offsets gross
    underclaim_hrs = sum(
        max(0.0, ps["audited_hrs"] - ps["reported_hrs"])
        for ps in patrol_summaries.values()
    )
    # Cap overclaim at actual flagged excess: underclaim + net ≈ gross
    # Extra hours above season benchmark on exceeded routes
    exceeded_cap_hrs = sum(
        max(0.0, r["jan_hrs"] - r["benchmark"])
        for r in exceeded_routes
    )

    # ── Section 1 table rows ──────────────────────────────────────
    def _s1_row(patrol_label: str, ps: dict) -> str:
        v = ps["variance_hrs"]
        nf = ps["n_flagged"]
        if nf > 0 and v < -0.01:
            badge = _badge(f"Overclaim — {nf} flag{'s' if nf > 1 else ''}", "red")
        elif nf > 0:
            badge = _badge(f"{nf} flags — undercharged (net)", "orange")
        elif v > 0.01:
            badge = _badge("Undercharged — no flags", "green")
        else:
            badge = _badge("Unverifiable — no patrol assigned", "grey") if patrol_label == "Unassigned" else _badge("Clean", "green")

        return f"""    <tr>
      <td><strong>{patrol_label}</strong></td>
      <td class="num">{ps['submissions']}</td><td class="num">{ps['unique_ud']}</td>
      <td class="num">{_fmt(ps['reported_hrs'])}</td><td class="num">{_fmt(ps['audited_hrs'])}</td>
      <td class="num">{_variance_cell(v)}</td>
      <td class="num">{_pct_cell(v / ps['reported_hrs'] * 100 if ps['reported_hrs'] else 0)}</td>
      <td>{badge}</td>
    </tr>"""

    s1_rows = []
    for p in PATROL_ORDER:
        if p in patrol_summaries:
            s1_rows.append(_s1_row(f"Patrol {p}", patrol_summaries[p]))
    if "Unassigned" in patrol_summaries:
        s1_rows.append(_s1_row("Unassigned", patrol_summaries["Unassigned"]))

    # ── Section 2 table rows ──────────────────────────────────────
    def _s2_row(patrol_label: str, ps: dict) -> str:
        tp_d  = ps["audited_tp"]  - ps["reported_tp"]
        std_d = ps["audited_std"] - ps["reported_std"]
        cost  = tp_d * 100 + std_d * 75
        return f"""    <tr>
      <td><strong>{patrol_label}</strong></td>
      <td class="num">{_fmt(ps['reported_tp'])}</td><td class="num">{_fmt(ps['audited_tp'])}</td>
      <td class="num">{_variance_cell(tp_d)}</td>
      <td class="num">{_fmt(ps['reported_std'])}</td><td class="num">{_fmt(ps['audited_std'])}</td>
      <td class="num">{_variance_cell(std_d)}</td>
      <td class="num">{_variance_cell(cost)}</td>
    </tr>"""

    s2_rows = []
    for p in PATROL_ORDER:
        if p in patrol_summaries:
            s2_rows.append(_s2_row(f"Patrol {p}", patrol_summaries[p]))
    if "Unassigned" in patrol_summaries:
        s2_rows.append(_s2_row("Unassigned", patrol_summaries["Unassigned"]))

    total_tp_d  = total_aud_tp  - total_rep_tp
    total_std_d = total_aud_std - total_rep_std
    total_cost  = total_tp_d * 100 + total_std_d * 75

    # ── Section 3 — grouped by patrol ────────────────────────────
    s3_sections = []
    for patrol in PATROL_ORDER + (["Unassigned"] if any(g["patrol"] == "Unassigned" for g in flagged_groups) else []):
        pg = [g for g in flagged_groups if g["patrol"] == patrol]
        if not pg:
            # Add no-flag section for patrols in summaries with no flags
            if patrol in patrol_summaries and patrol_summaries[patrol]["n_flagged"] == 0 and patrol_summaries[patrol]["submissions"] > 0:
                s3_sections.append(f"""<div class="patrol-section no-flag">
  <h3>Patrol {patrol} — No Billing Anomalies Detected</h3>
  <p class="green-note">&#10003; All Patrol {patrol} submissions passed the deduplication check.</p>
</div>""")
            continue

        total_excess_p = sum(g["excess_hrs"] for g in pg)
        total_dollar_p = sum(g["est_dollar"] for g in pg)
        label = f"Patrol {patrol}" if patrol != "Unassigned" else "Unassigned"
        rows = []
        for g in pg:
            excess = g["excess_hrs"]
            row_cls = "row-high" if excess >= 10 else "row-med" if excess >= 2 else "row-low" if excess > 0 else "row-zero"
            excess_cell = f'<span class="dimmed">{_fmt(excess)}</span>' if excess <= 0.005 else _fmt(excess)
            dollar_cell = f'<span class="dimmed">${g["est_dollar"]}</span>' if g["est_dollar"] == 0 else f'${g["est_dollar"]:,}'
            routes_str = ", ".join(g["routes"]) if g["routes"] else "—"
            tp_cell = "&#10003; TP" if g["is_tp"] else "Std"
            rows.append(f"""        <tr class="{row_cls}">
          <td>{g['date']}</td><td><code>{g['unit']}</code></td>
          <td>{_badge_type(g['flag_type'])}</td><td>{routes_str}</td>
          <td class="num">{excess_cell}</td><td class="num">${g['rate']}/hr</td>
          <td class="num">{dollar_cell}</td><td>{tp_cell}</td>
        </tr>""")

        s3_sections.append(f"""<div class="patrol-section">
  <h3>{label} — {len(pg)} Flagged Group{'s' if len(pg) > 1 else ''} &nbsp;|&nbsp; {_fmt(total_excess_p)} hrs excess &nbsp;|&nbsp; Est. ${total_dollar_p:,} impact</h3>
  <table>
    <thead><tr><th>Date</th><th>Unit</th><th>Flag Type</th><th>Routes</th>
      <th class="num">Excess hrs</th><th class="num">Rate</th><th class="num">Est. $</th><th>TP?</th></tr></thead>
    <tbody>
{chr(10).join(rows)}</tbody>
  </table>
</div>""")

    # ── Section 4 ─────────────────────────────────────────────────
    # Exclude W3/W4 combined family and sub-routes from main table (handled separately)
    combined_codes = {"W3", "W4"}
    sub_routes = {"W-3A", "W-3B", "W-3C", "W-4A", "W-4B", "W-4C"}

    # Routes that belong to the AYR corridor (Patrol 13): canonical 'AYR-*' or shorthand 'A-1A'/'A1A'
    def _is_ayr(route: str) -> bool:
        n = _norm_route(route)
        return n.startswith("AYR") or (len(n) >= 2 and n[0] == 'A' and n[1].isdigit())

    # Main table: all routes except W combined family/sub-routes and pure data-quality unknowns.
    # Ayr routes (Unknown status, A-prefix) are included as normal routes — benchmark shows "—".
    main_util = [r for r in route_util
                 if r["route"] not in combined_codes and r["route"] not in sub_routes
                 and not (r["status"] == "Unknown" and not _is_ayr(r["route"]))]
    ayr_util      = [r for r in route_util if r["status"] == "Unknown" and _is_ayr(r["route"])]
    dq_util       = [r for r in route_util
                     if r["status"] == "Unknown" and not _is_ayr(r["route"])
                     and r["route"] not in combined_codes]
    combined_util = [r for r in route_util if r["route"] in combined_codes]

    def _util_row(r: dict) -> str:
        # Ayr routes have no benchmark in the local table — show as "Pending" not "Unknown"
        is_ayr_route = r["status"] == "Unknown" and _is_ayr(r["route"])
        cls      = "" if is_ayr_route else ROW_CLASS.get(r["status"], "")
        stat_css = "util-unknown" if is_ayr_route else STATUS_CSS.get(r["status"], "util-healthy")
        stat_lbl = "Benchmark pending" if is_ayr_route else r["status"]
        tp_note = f" ({_fmt(r['jan_tp'])} TP)" if r["jan_tp"] > 0.01 else ""
        bm_str = _fmt(r["benchmark"], 0) if r["benchmark"] > 0 else "—"
        jan_str = _fmt(r["jan_hrs"]) + tp_note if r["jan_hrs"] > 0 else "0.00"
        pct_str = f"{_fmt(r['util_pct'], 1)}%" if r["benchmark"] > 0 else "—"
        return f"""    <tr class="{cls}">
      <td><strong>{r['route']}</strong></td>
      <td class="num">{bm_str}</td>
      <td class="num">{jan_str}</td>
      <td class="num">{pct_str}</td>
      <td><span class="{stat_css}">{stat_lbl}</span></td>
    </tr>"""

    s4_rows = "\n".join(_util_row(r) for r in main_util)

    # Summary callouts
    exc_txt  = " &nbsp; ".join(f"<strong>{r['route']}</strong> ({_fmt(r['util_pct'], 1)}%)" for r in exceeded_routes) or "None"
    crit_txt = " &nbsp; ".join(f"{r['route']} ({_fmt(r['util_pct'], 1)}%)" for r in critical_routes) or "None"
    la_txt   = " &nbsp; ".join(f"<strong>{r['route']}</strong> ({_fmt(r['util_pct'], 1)}% — {_fmt(r['jan_hrs'])} hrs)" for r in low_act_routes) or "None"
    min_txt  = " &nbsp; ".join(f"<strong>{r['route']}</strong> ({_fmt(r['util_pct'], 1)}% — {_fmt(r['jan_hrs'])} hrs)" for r in minimal_routes) or "None"

    # No-records routes (benchmarked but zero Jan actuals, and not combined codes)
    no_records = [r for r in main_util if r["jan_hrs"] < 0.01 and r["benchmark"] > 0]
    no_rec_rows = "\n".join(
        f'<tr><td><strong>{r["route"]}</strong></td><td class="num">{_fmt(r["benchmark"],0)}</td>'
        f'<td><span class="badge grey">No January records</span></td></tr>'
        for r in no_records
    )

    # Combined codes sub-table
    combined_rows = "\n".join(
        f'<tr><td><strong>{r["route"]}</strong></td>'
        f'<td class="num">{_fmt(r["benchmark"], 0)}</td>'
        f'<td>{_fmt(r["jan_hrs"])} hrs recorded — sub-route split not available</td></tr>'
        for r in combined_util
    )

    # AYR sub-table rows (Patrol 13 routes — benchmarks not yet in system)
    ayr_rows = "\n".join(
        f'<tr><td><strong>{r["route"]}</strong></td>'
        f'<td class="num">{_fmt(r["jan_hrs"])}'
        f'{(" (" + _fmt(r["jan_tp"]) + " TP)") if r["jan_tp"] > 0.01 else ""}</td>'
        f'<td><span class="badge grey">Benchmark pending</span></td></tr>'
        for r in sorted(ayr_util, key=lambda x: -x["jan_hrs"])
    )
    ayr_total_hrs = sum(r["jan_hrs"] for r in ayr_util)

    # Data quality unknowns — abbreviated/combined/garbled route codes
    dq_with_hrs = sorted([r for r in dq_util if r["jan_hrs"] > 0.01], key=lambda x: -x["jan_hrs"])
    dq_rows = "\n".join(
        f'<tr><td><strong>{r["route"]}</strong></td>'
        f'<td class="num">{_fmt(r["jan_hrs"])}'
        f'{(" (" + _fmt(r["jan_tp"]) + " TP)") if r["jan_tp"] > 0.01 else ""}</td>'
        f'<td><span class="badge grey">Unmatched route code</span></td></tr>'
        for r in dq_with_hrs
    )
    dq_total_hrs = sum(r["jan_hrs"] for r in dq_with_hrs)

    # ── Section 6 actions ─────────────────────────────────────────
    actions = []

    # Action 1: Route cap exceeded — highest priority
    if exceeded_routes:
        route_lines = " ".join(
            f"<li>Route <strong>{r['route']}</strong>: {_fmt(r['jan_hrs'])} hrs billed in January against a "
            f"{_fmt(r['benchmark'],0)}-hr full-season benchmark ({_fmt(r['util_pct'],1)}% consumed). "
            f"Over-benchmark hours this month alone: {_fmt(r['jan_hrs'] - r['benchmark'])} hrs "
            f"(est. ${(r['jan_hrs']-r['benchmark'])*75:,.0f}–${(r['jan_hrs']-r['benchmark'])*100:,.0f}).</li>"
            for r in exceeded_routes
        )
        actions.append(
            f"<li><strong>Investigate routes that have already exceeded their full-season benchmark.</strong> "
            f"The following routes consumed their entire annual allocation in January alone — "
            f"routes that require further investigation:<ul>{route_lines}</ul></li>"
        )

    # Action 2: Critical-pace routes
    if critical_routes:
        crit_lines = " ".join(
            f"<li>Route <strong>{r['route']}</strong>: {_fmt(r['util_pct'],1)}% of season budget in January "
            f"({_fmt(r['jan_hrs'])} of {_fmt(r['benchmark'],0)} hrs). "
            f"Remaining budget: {_fmt(r['benchmark'] - r['jan_hrs'])} hrs for Feb–Apr.</li>"
            for r in critical_routes
        )
        actions.append(
            f"<li><strong>Monitor critical-pace routes closely for the remainder of the season.</strong> "
            f"The following routes used 70–100% of their annual benchmark in January. "
            f"At current intensity they will exhaust their allocations before season end:<ul>{crit_lines}</ul></li>"
        )

    # Action 3: Flagged billing groups
    if flagged_groups:
        actions.append(
            f"<li><strong>Contractor to respond to the {total_flagged} flagged billing group(s) (Section 3).</strong> "
            f"Gross excess: {_fmt(gross_excess)} hrs (est. ${gross_dollar_low:,.0f}–${gross_dollar_high:,.0f}). "
            f"For each group: (a) confirm deduplication is correct and issue a credit note, or "
            f"(b) provide operational documentation demonstrating the hours are distinct and separately billable. "
            f"Note: the net variance (+{_fmt(total_var)} hrs) is not a settlement figure — see Section 5.</li>"
        )

    # Action 4: Zero-activity routes vs unmatched codes
    zero_routes = [r for r in route_util if r["benchmark"] > 0 and r["jan_hrs"] < 0.01]
    if zero_routes and dq_with_hrs:
        zero_names = ", ".join(r["route"] for r in zero_routes)
        dq_names   = ", ".join(r["route"] for r in dq_with_hrs[:8])
        actions.append(
            f"<li><strong>Reconcile zero-activity routes against unmatched billing codes.</strong> "
            f"{len(zero_routes)} contract routes recorded no January hours ({zero_names}), "
            f"yet {_fmt(dq_total_hrs)} hrs appear under unmatched codes ({dq_names}{'…' if len(dq_with_hrs) > 8 else ''}). "
            f"These hours may belong to the zero-activity routes entered under abbreviated or combined codes. "
            f"Contractor should resubmit corrected route codes before final settlement.</li>"
        )

    # Action 5: Minimal activity
    if minimal_routes:
        min_with_hrs = [r for r in minimal_routes if r["jan_hrs"] > 0.01]
        if min_with_hrs:
            min_names = ", ".join(r["route"] for r in min_with_hrs)
            actions.append(
                f"<li><strong>Clarify routes with minimal January activity.</strong> "
                f"Routes {min_names} recorded less than 5% of their season benchmark in the peak winter month. "
                f"Contractor should confirm whether service was provided and, if so, whether hours were billed under a different route code.</li>"
            )

    if not actions:
        actions.append("<li>No immediate actions identified. All patrols within normal variance range.</li>")

    actions_html = "\n  ".join(actions)

    # ── Contract coverage ─────────────────────────────────────────
    covered_bm = sum(r["benchmark"] for r in route_util if r["benchmark"] > 0)
    total_bm_str = f"{total_season_benchmark:,.0f}"
    covered_str  = f"{covered_bm:,.0f}"
    n_unknown_bm = len(ayr_util)
    unknown_hrs_jan = ayr_total_hrs

    # ── Assemble HTML ─────────────────────────────────────────────
    s1_rows_html   = "\n".join(s1_rows)
    s2_rows_html   = "\n".join(s2_rows)
    s3_html        = "\n".join(s3_sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{month_label} Benchmark Hours Audit Report</title>
<style>
{CSS}
</style>
</head>
<body>

<h1>{month_label} — Benchmark Hours Audit Report</h1>

<div class="meta-block">
<table>
<tr>
  <td><strong>Audit Period</strong></td><td>January 1 – January 31, 2026</td>
  <td><strong>Generated</strong></td><td>{generated}</td>
</tr>
<tr>
  <td><strong>Records in Scope</strong></td>
  <td>{total_subs:,} verified records. {excluded_count} out-of-period records excluded.</td>
  <td><strong>Classification</strong></td><td>DRAFT — Pending Supervisor Review</td>
</tr>
<tr>
  <td><strong>Contract Coverage</strong></td>
  <td><strong>Season benchmark:</strong> {total_bm_str} hrs entered. {f"{n_unknown_bm} routes active with no benchmark on file (see Section 4 — Benchmark pending)." if n_unknown_bm > 0 else "All route benchmarks present."}</td>
  <td></td><td></td>
</tr>
</table>
</div>

<div class="callout info">
  <strong>Report structure:</strong> Section 1 — headline figures and reconciliation summary. Section 2 — tow plow vs standard breakdown. Section 3 — per-patrol flagged billing groups. Section 4 — route-level benchmark utilisation. Section 5 — full-season extrapolation. Section 6 — recommended actions.
</div>

<!-- SECTION 1 -->
<h2>Section 1 — Executive Summary</h2>

<div class="callout info">
  <strong>Variance:</strong> <span class="neg">Negative (−)</span> = contractor overclaimed. <span class="pos">Positive (+)</span> = contractor undercharged.
</div>

<table>
  <thead><tr>
    <th>Patrol</th><th class="num">Submissions</th><th class="num">Unique Unit‑Days</th>
    <th class="num">Reported hrs</th><th class="num">Audited hrs</th>
    <th class="num">Variance hrs</th><th class="num">Variance %</th><th>Audit Status</th>
  </tr></thead>
  <tbody>
{s1_rows_html}
    <tr class="total-row">
      <td><strong>TOTAL</strong></td>
      <td class="num">{total_subs:,}</td><td class="num">{total_ud:,}</td>
      <td class="num">{_fmt(total_rep)}</td><td class="num">{_fmt(total_aud)}</td>
      <td class="num">{_variance_cell(total_var)}</td>
      <td class="num">{_pct_cell(total_var / total_rep * 100 if total_rep else 0)}</td>
      <td><strong>{total_flagged} flagged groups | {_fmt(gross_excess)} hrs gross excess</strong></td>
    </tr></tbody>
</table>
<p class="legend">
  <strong>Submissions</strong> = individual billing records. &nbsp;
  <strong>Unique Unit‑Days</strong> = distinct (unit, date) groups. &nbsp;
  <strong>Audited hrs</strong> = after contract deduplication rules.
</p>

<div class="recon-box">
  <h4>Reconciliation Summary — Key Findings</h4>
  <table class="recon-grid">
    <tr>
      <td>&#9312; Billing Accuracy<br><span style="font-weight:normal;font-size:9pt;">Same-unit duplicate/overlap records</span></td>
      <td class="val neg">−{_fmt(gross_excess)} hrs gross<br><span style="font-size:9pt;font-weight:normal;">({_variance_cell(total_var)} net — misleading)</span></td>
      <td style="font-size:9pt;">
        {total_flagged} flagged groups | <strong>{_fmt(gross_excess)} hrs gross excess</strong> | est. ${gross_dollar_low:,.0f}–${gross_dollar_high:,.0f} credit due to MTO.<br>
        Net variance ({_variance_cell(total_var)} hrs) is near-zero because the chain audit also credited {_fmt(total_var + gross_excess)} hrs of inter-form operating time the contractor did not claim. Use gross, not net, for credit discussions.<br>
        <em>See Section 5 for full explanation.</em>
      </td>
    </tr>
{f"""    <tr>
      <td>&#9313; Route Cap Exceeded<br><span style="font-weight:normal;font-size:9pt;">Jan actual &gt; full-season benchmark</span></td>
      <td class="val" style="color:#c0392b;">{' | '.join(f'{r["route"]}: {_fmt(r["jan_hrs"])} hrs (season cap: {_fmt(r["benchmark"],0)} hrs)' for r in exceeded_routes)}</td>
      <td style="font-size:9pt;">
        {'<br>'.join(f'Route {r["route"]} consumed {_fmt(r["util_pct"],1)}% of its full-season allocation in January alone.' for r in exceeded_routes)}<br>
        <em>Requires review before further billing is accepted on these routes.</em>
      </td>
    </tr>""" if exceeded_routes else ""}
{f"""    <tr>
      <td>&#9314; Deployment / Reporting Gap<br><span style="font-weight:normal;font-size:9pt;">Routes at &lt;5% of season budget in peak month</span></td>
      <td class="val" style="color:#880e4f;">{len(minimal_routes)} route{'s' if len(minimal_routes) > 1 else ''} flagged</td>
      <td style="font-size:9pt;">
        {' '.join(f'{r["route"]} ({_fmt(r["util_pct"],1)}%)' for r in minimal_routes)}<br>
        For the core winter month, this level of activity is anomalously low.
      </td>
    </tr>""" if minimal_routes else ""}
  </table>
</div>

<!-- SECTION 2 -->
<h2>Section 2 — Tow Plow vs Standard Hours</h2>
<div class="callout">
  <strong>Rates:</strong> Tow plow = <strong>$100/hr</strong> &nbsp;|&nbsp; Standard = <strong>$75/hr</strong>. Cost impact = (TP Δ × $100) + (Std Δ × $75).
</div>
<table>
  <thead><tr>
    <th>Patrol</th>
    <th class="num">TP Reported</th><th class="num">TP Audited</th><th class="num">TP Δ</th>
    <th class="num">Std Reported</th><th class="num">Std Audited</th><th class="num">Std Δ</th>
    <th class="num">Cost Impact</th>
  </tr></thead>
  <tbody>
{s2_rows_html}
    <tr class="total-row">
      <td><strong>TOTAL</strong></td>
      <td class="num">{_fmt(total_rep_tp)}</td><td class="num">{_fmt(total_aud_tp)}</td>
      <td class="num">{_variance_cell(total_tp_d)}</td>
      <td class="num">{_fmt(total_rep_std)}</td><td class="num">{_fmt(total_aud_std)}</td>
      <td class="num">{_variance_cell(total_std_d)}</td>
      <td class="num">{_variance_cell(total_cost)}</td>
    </tr></tbody>
</table>

<!-- SECTION 3 -->
<h2 class="section-break">Section 3 — Per-Patrol Flagged Billing Groups</h2>
<div class="callout red">
  <strong>Duplicate definition:</strong> A flag applies only when the <em>same unit number</em> appears more than once for the same date with overlapping time windows. Two different units on the same route is normal and not flagged.
</div>
<div class="callout info">
  <strong>Flag types — </strong>
  <strong>DUPLICATE:</strong> same unit, same date, identical/fully overlapping windows — only the longest is billable. &nbsp;
  <strong>OVERLAP:</strong> same unit, partial time overlap — overlapping minutes not separately billable. &nbsp;
  <strong>REPLACED/DUP+REPLACED:</strong> later submission superseded earlier — earlier record not independently billable. &nbsp;
  <em>Zero-excess rows (greyed) are shown for transparency only.</em>
</div>
<div class="callout">
  <strong>Gross excess vs net variance — two distinct measures:</strong>
  The <strong>{_fmt(gross_excess)} hrs gross excess</strong> is the sum of duplicate minutes identified across the {total_flagged} flagged groups.
  The <strong>{_variance_cell(total_var)} hrs net variance</strong> is lower because some patrols audited to more hours than reported (contractor undercharged).
</div>

{s3_html}

<!-- SECTION 4 -->
<h2 class="section-break">Section 4 — Route Benchmark Utilisation</h2>
<div class="callout info">
  <strong>Utilisation %</strong> = January audited hours ÷ full-season benchmark. January is the peak winter month. A route using more than 40% of its season budget in January is on pace to exhaust the contract allocation before season end. Routes above 100% have already exceeded their full-season cap.
</div>
<table>
  <thead><tr>
    <th>Route</th>
    <th class="num">Season Benchmark (hrs)</th>
    <th class="num">Jan Actual (audited hrs)</th>
    <th class="num">Jan / Season %</th>
    <th>Status</th>
  </tr></thead>
  <tbody>
{s4_rows}
  </tbody>
</table>
<p class="legend">
  <span class="util-exceeded">&#9632; EXCEEDED</span> &gt;100% &nbsp;
  <span class="util-critical">&#9632; Critical</span> 70–100% &nbsp;
  <span class="util-moderate">&#9632; Moderate</span> 40–70% &nbsp;
  <span class="util-healthy">&#9632; Healthy</span> 10–40% &nbsp;
  <span class="util-low_activity">&#9632; Low Activity</span> 5–10% &nbsp;
  <span class="util-minimal">&#9632; Minimal</span> &lt;5% (peak-month activity anomalously low) &nbsp;&nbsp;
  TP = tow plow hours within January total.
</p>

<div class="summary-box">
  <strong>Exceeded full-season cap:</strong>&nbsp; {exc_txt}<br><br>
  <strong>Critical pace (70–100%):</strong>&nbsp; {crit_txt}<br><br>
  <strong>Low Activity (5–10%):</strong>&nbsp; {la_txt}<br><br>
  <strong>Minimal (&lt;5%) — effectively no January service recorded:</strong>&nbsp; {min_txt}
</div>

{"" if not no_records else f"""<h3>Routes with no January billing records</h3>
<table>
  <thead><tr><th>Route(s)</th><th class="num">Season Benchmark (hrs)</th><th>Status</th></tr></thead>
  <tbody>
{no_rec_rows}
  </tbody>
</table>"""}

{"" if not combined_rows else f"""<h3>Combined billing codes</h3>
<table>
  <thead><tr><th>Code</th><th class="num">Combined Benchmark (hrs)</th><th>Note</th></tr></thead>
  <tbody>
{combined_rows}
  </tbody>
</table>"""}

{"" if not dq_rows else f"""<h3>Data Quality — Unmatched Route Codes</h3>
<div class="callout info">
  The following route codes appear in billing records but do not match any contract route. These are likely data-entry abbreviations, combined codes, or errors. Hours are included in patrol totals where the unit and date are unambiguous. Codes should be corrected at source.
  <br><strong>Total hours under unmatched codes: {_fmt(dq_total_hrs)} hrs</strong>
</div>
<table>
  <thead><tr><th>Code in Record</th><th class="num">Jan Hours (audited)</th><th>Status</th></tr></thead>
  <tbody>
{dq_rows}
  </tbody>
</table>"""}

<!-- SECTION 5 -->
<h2>Section 5 — Budget Burn Rate &amp; Settlement Context</h2>
<div class="callout red">
  <strong>Note on extrapolation:</strong> A simple January ÷ 6.5 months projection is not used here. January alone consumed {_fmt(jan_burn_pct, 1)}% of the total season benchmark — meaning January is the dominant billing month for this contract, not one-sixth of the season. Applying a calendar-proportional multiplier to January would project a full-season total of approximately 3× the contract value, which is arithmetically correct but operationally meaningless as a forecast. The burn-rate figures below are the appropriate basis for settlement discussion.
</div>
<div class="summary-box">
  <table>
    <tr><td><strong>Season benchmark</strong></td><td>{total_bm_str} hrs</td></tr>
    <tr><td><strong>January audited consumption</strong></td><td><strong>{_fmt(total_aud)} hrs — {_fmt(jan_burn_pct, 1)}% of annual benchmark in one month</strong></td></tr>
    <tr><td>Remaining season budget (Feb–Apr 30)</td><td>{_fmt(remaining_budget)} hrs</td></tr>
    <tr><td colspan="2" style="padding-top:8px;border-top:1px solid #ccc;"><strong>Billing accuracy findings</strong></td></tr>
    <tr><td><strong>Gross flagged excess (duplicates / overlaps)</strong></td><td><strong><span class="neg">−{_fmt(gross_excess)} hrs across {total_flagged} groups</span> &nbsp; est. ${gross_dollar_low:,.0f}–${gross_dollar_high:,.0f} credit due to MTO</strong></td></tr>
    <tr><td>Net variance (all records)</td><td>{_variance_cell(total_var)} hrs &nbsp; <em>Not used for settlement — see note</em></td></tr>
    <tr><td colspan="2" style="padding-top:8px;border-top:1px solid #ccc;"><strong>Route cap exceedances (January only)</strong></td></tr>
    <tr><td>Hours billed above season cap on exceeded routes</td><td><span class="neg">−{_fmt(exceeded_cap_hrs)} hrs above individual route caps</span> &nbsp; ({', '.join(r['route'] for r in exceeded_routes)})</td></tr>
  </table>
</div>
<div class="callout info">
  <strong>Note on the net variance.</strong>
  The net variance of {_variance_cell(total_var)} hrs is the difference between the chain-audited total and the sum of individual form totals. The chain audit credits {_fmt(total_var + gross_excess)} hrs of inter-form operating time — gaps between consecutive forms for the same unit that the contract counts as continuous operation but that were not captured on any individual form. Against this, {_fmt(gross_excess)} hrs of duplicate and overlap billing was removed. The two effects nearly cancel: {_fmt(total_var + gross_excess)} − {_fmt(gross_excess)} = {_variance_cell(total_var)} hrs net. The contractor is not owed the unclaimed inter-form hours — they did not bill for them. The gross flagged excess (<strong>est. ${gross_dollar_low:,.0f}–${gross_dollar_high:,.0f}</strong>) is the appropriate figure for credit discussions.
</div>

<!-- SECTION 6 -->
<h2>Section 6 — Recommended Actions</h2>
<ol>
  {actions_html}
</ol>

<div class="callout info" style="margin-top:18px;">
  <strong>Methodology.</strong> Audited hours apply the contract deduplication rules: gaps ≤ 60 min = continuous; gaps 61–180 min = first 60 min only; gaps &gt; 180 min = new event; 30-min refuel/event allowance. This report does not constitute a final billing determination.
</div>

</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Regenerate the January 2026 Benchmark Hours Audit Report.")
    parser.add_argument("--month", default="2026-01", help="Month to report (YYYY-MM). Default: 2026-01")
    parser.add_argument("--out", default=None, help="Output HTML path (default: <Month>_Audit_Report.html in script dir)")
    args = parser.parse_args()

    year, month = map(int, args.month.split("-"))
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    JAN_START = date(year, month, 1)
    JAN_END   = date(year, month, last_day)
    month_label = JAN_START.strftime("%B %Y")

    script_dir = pathlib.Path(__file__).parent
    out_path = pathlib.Path(args.out) if args.out else (
        script_dir / f"{month_label.replace(' ', '_')}_Audit_Report.html"
    )

    # ── Load data ─────────────────────────────────────────────────
    cfg = _load_config()
    all_records = fetch_cache(cfg)
    overrides   = fetch_benchmark_overrides(cfg)
    benchmarks  = build_benchmark_table(overrides)

    # ── Filter to month ───────────────────────────────────────────
    jan_records = []
    excluded    = []
    for r in all_records:
        try:
            d = date.fromisoformat(r["start_date"])
        except Exception:
            excluded.append(r)
            continue
        if JAN_START <= d <= JAN_END:
            jan_records.append(r)
        else:
            excluded.append(r)

    print(f"\nJanuary 2026 records: {len(jan_records)}")
    print(f"Excluded (out-of-period): {len(excluded)}")

    patrols_in_jan = sorted(set(_effective_patrol(r) for r in jan_records))
    print(f"Patrols found: {patrols_in_jan}")

    # ── Build chains ──────────────────────────────────────────────
    print("\nBuilding event chains …", flush=True)
    unit_chains = build_unit_chains(jan_records)
    print(f"  {len(unit_chains)} units, {sum(len(c) for c in unit_chains.values())} chains total.")

    # ── Compute sections ──────────────────────────────────────────
    print("Computing patrol summaries …", flush=True)
    patrol_summaries = compute_patrol_summaries(jan_records, unit_chains)

    print("Computing flagged groups …", flush=True)
    flagged_groups = compute_flagged_groups(jan_records)
    print(f"  {len(flagged_groups)} flagged (unit, date) groups.")

    print("Computing route utilisation …", flush=True)
    route_util = compute_route_utilization(unit_chains, benchmarks)

    # ── Print summary to console ──────────────────────────────────
    total_rep = sum(ps["reported_hrs"] for ps in patrol_summaries.values())
    total_aud = sum(ps["audited_hrs"]  for ps in patrol_summaries.values())
    print(f"\nSummary:")
    print(f"  Total reported: {total_rep:.2f} hrs")
    print(f"  Total audited:  {total_aud:.2f} hrs")
    print(f"  Net variance:   {total_aud - total_rep:.2f} hrs")
    print(f"  Flagged groups: {len(flagged_groups)}")
    exceeded = [r for r in route_util if r["status"] == "EXCEEDED"]
    if exceeded:
        print(f"  Routes exceeding season cap: {', '.join(r['route'] for r in exceeded)}")

    # ── Generate HTML ─────────────────────────────────────────────
    print("\nGenerating HTML …", flush=True)
    html = build_html(
        month_label    = month_label,
        generated      = date.today().isoformat(),
        jan_records    = jan_records,
        patrol_summaries = patrol_summaries,
        flagged_groups = flagged_groups,
        route_util     = route_util,
        benchmarks     = benchmarks,
        all_records_count = len(all_records),
        excluded_count    = len(excluded),
    )

    out_path.write_text(html, encoding="utf-8")
    print(f"\nReport written to: {out_path}")
    print("Open in a browser, then print / save as PDF if needed.")
    print("Or: nano-pdf January_2026_Audit_Report.html January_2026_Audit_Report.pdf")


if __name__ == "__main__":
    main()
