# SPDX-License-Identifier: Apache-2.0
"""Stage 1 Adaptive Protocol Cartographer: 2D timing-surface mapper (X=sclk_low, Y=gap_wait).

Evaluates uniform_grid, blind_random, and adaptive_cartographer under equal query budgets
on dev surfaces (S0..S2: notch, bimodal, diagonal+watchdog) and held-out surfaces
(S3..S6: tilted ridge, twin pinholes, 4-zone center core, periodic comb) against live ASIC RTL.
"""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import build_spi_2d_program  # noqa: E402
from tools.timing_explorer import IcarusSimTransport, ProtocolClient  # noqa: E402

X_VALS: Tuple[int, ...] = tuple(range(2, 14))  # 12 values: sclk_low_cycles 2..13
Y_VALS: Tuple[int, ...] = tuple(range(0, 16))  # 16 values: gap_wait 0..15

SURFACE_SPECS: Dict[int, Dict[str, Any]] = {
    0: {"name": "S0_notch_watchdog", "role": "dev", "desc": "Setup wall (X<=3), watchdog (Y>=13), 2x2 notch (X=7..8,Y=6..7)"},
    1: {"name": "S1_bimodal_islands", "role": "dev", "desc": "Setup wall (X<=2) + two disconnected islands"},
    2: {"name": "S2_diagonal_resonance", "role": "dev", "desc": "Diagonal coupled wall (2X+Y<16) + watchdog + 2x2 core"},
    3: {"name": "S3_held_tilted_ridge", "role": "held_out", "desc": "Tilted diagonal ridge + fast-gap drop + setup edge"},
    4: {"name": "S4_held_twin_pinholes", "role": "held_out", "desc": "Two tiny disconnected 2x2 pinholes + fast-gap drop"},
    5: {"name": "S5_held_4zone_core", "role": "held_out", "desc": "Lower-left fail, upper-right warn, 2x2 center pinhole"},
    6: {"name": "S6_held_periodic_comb", "role": "held_out", "desc": "Setup wall + 3 disconnected periodic phase stripes"},
    7: {"name": "S7_new_odd_pinholes", "role": "new_heldout", "desc": "Odd-parity 2x2 pinholes + Y=0 fast-gap floor"},
    8: {"name": "S8_new_curved_circle", "role": "new_heldout", "desc": "Setup wall + circular glitch disk ((X-8)^2+(Y-8)^2<=9)"},
    9: {"name": "S9_new_lshape_and_core", "role": "new_heldout", "desc": "Two asymmetric L-corners + 2x2 center island"},
    10: {"name": "S10_new_antidiag_ridge", "role": "new_heldout", "desc": "Setup edge + reverse anti-diagonal ridge (X+Y in 14..16)"},
    11: {"name": "S11_new_3band_step", "role": "new_heldout", "desc": "Three staggered rectangular steps across quadrants"},
    12: {"name": "S12_new_tri_islands", "role": "new_heldout", "desc": "Y=15 ceiling + 3 disconnected 2x3/3x2 islands"},
}

DEV_SURFACES: Tuple[int, ...] = (0, 1, 2)
HELD_SURFACES: Tuple[int, ...] = (3, 4, 5, 6)
NEW_HELD_SURFACES: Tuple[int, ...] = (7, 8, 9, 10, 11, 12)


def probe_rtl_surface_point(
    transport: IcarusSimTransport,
    client: ProtocolClient,
    surface_id: int,
    x: int,
    y: int,
    *,
    cmd0: int = 0x06,
    cmd1: int = 0x05,
) -> Dict[str, Any]:
    """Drive one real 2D `(X=sclk_low, Y=gap_wait)` probe on the ASIC RTL."""
    words = build_spi_2d_program(cmd0, cmd1, sclk_low_cycles=x)
    client.sync_program(words)
    transport.configure_target(10, delay_ns=surface_id)
    res = client.run_trial(y)
    return {
        "x": x,
        "y": y,
        "sample_reg": int(res["sample_reg"]),
        "cycle_cnt": int(res["cycle_cnt"]),
        "eng_state": res["eng_state"],
        "events": res["events"],
    }


def extract_connected_components(
    grid: Dict[Tuple[int, int], int],
) -> List[Dict[str, Any]]:
    """Find 4-connected regions of identical response class on the 2D grid."""
    visited: Set[Tuple[int, int]] = set()
    comps: List[Dict[str, Any]] = []
    cid = 0
    for x in X_VALS:
        for y in Y_VALS:
            if (x, y) in visited:
                continue
            label = grid[(x, y)]
            q = deque([(x, y)])
            visited.add((x, y))
            cells: List[Tuple[int, int]] = []
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (cx + dx, cy + dy)
                    if nb in grid and nb not in visited and grid[nb] == label:
                        visited.add(nb)
                        q.append(nb)
            comps.append(
                {
                    "id": cid,
                    "label": label,
                    "label_hex": f"0x{label:02x}",
                    "size": len(cells),
                    "cells": cells,
                    "is_small": len(cells) <= 12,
                }
            )
            cid += 1
    return comps


def boundary_cells(grid: Dict[Tuple[int, int], int]) -> Set[Tuple[int, int]]:
    """Return cells that have at least one 4-neighbor with a different response class."""
    bset: Set[Tuple[int, int]] = set()
    for (x, y), val in grid.items():
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in grid and grid[nb] != val:
                bset.add((x, y))
                break
    return bset


def collect_surface_truth_rtl(surface_id: int) -> Dict[Tuple[int, int], int]:
    """Capture the full 12x16 = 192 ground-truth response grid from live ASIC RTL."""
    truth: Dict[Tuple[int, int], int] = {}
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for x in X_VALS:
            words = build_spi_2d_program(0x06, 0x05, sclk_low_cycles=x)
            client.sync_program(words)
            for y in Y_VALS:
                transport.configure_target(10, delay_ns=surface_id)
                res = client.run_trial(y)
                truth[(x, y)] = int(res["sample_reg"])
    return truth


def _uniform_grid_points(budget: int) -> List[Tuple[int, int]]:
    if budget <= 36:
        xs = [2, 4, 6, 8, 10, 13]
        ys = [0, 3, 6, 9, 12, 15]
    elif budget <= 48:
        xs = [2, 4, 6, 9, 11, 13]
        ys = [0, 2, 4, 7, 9, 11, 13, 15]
    else:
        xs = [2, 3, 5, 6, 8, 10, 11, 13]
        ys = [0, 2, 4, 6, 8, 10, 12, 14]
    pts = [(x, y) for x in xs for y in ys]
    return pts[:budget]


def infer_voronoi_map(
    probed: Dict[Tuple[int, int], int],
) -> Dict[Tuple[int, int], int]:
    """Nearest-neighbor (normalized L2) 2D surface reconstruction from probed samples."""
    pred: Dict[Tuple[int, int], int] = {}
    items = list(probed.items())
    for x in X_VALS:
        for y in Y_VALS:
            if (x, y) in probed:
                pred[(x, y)] = probed[(x, y)]
                continue
            best_d = 1e9
            best_v = 0xAA
            # Prefer majority / deterministic lowest label on exact distance ties
            ties: List[int] = []
            for (px, py), pv in items:
                d = ((px - x) / 11.0) ** 2 + ((py - y) / 15.0) ** 2
                if d < best_d - 1e-9:
                    best_d = d
                    ties = [pv]
                elif abs(d - best_d) <= 1e-9:
                    ties.append(pv)
            if ties:
                best_v = max(set(ties), key=lambda v: (ties.count(v), -v))
            pred[(x, y)] = best_v
    return pred


def run_cartography_strategy(
    truth: Dict[Tuple[int, int], int],
    *,
    strategy: str,
    budget: int = 64,
    seed: int = 202601,
) -> Dict[str, Any]:
    """Execute one cartography sampling strategy under exact budget and score map quality."""
    rng = random.Random(seed)
    all_pts = [(x, y) for x in X_VALS for y in Y_VALS]
    probed: Dict[Tuple[int, int], int] = {}
    query_order: List[Tuple[int, int]] = []

    if strategy == "uniform_grid":
        for pt in _uniform_grid_points(budget):
            probed[pt] = truth[pt]
            query_order.append(pt)

    elif strategy == "blind_random":
        chosen = rng.sample(all_pts, budget)
        for pt in chosen:
            probed[pt] = truth[pt]
            query_order.append(pt)

    elif strategy == "adaptive_cartographer":
        # Phase 1: Coarse lattice covering corners + interior (~35-45% of budget)
        coarse_xs = (2, 6, 9, 13)
        coarse_ys = (0, 3, 7, 11, 15) if budget <= 48 else (0, 3, 6, 9, 12, 15)
        for x in coarse_xs:
            for y in coarse_ys:
                if len(probed) >= budget:
                    break
                probed[(x, y)] = truth[(x, y)]
                query_order.append((x, y))

        # Phase 2: Alternate midpoint boundary bisection (2/3) and max-min dispersion (1/3)
        step = 0
        while len(probed) < budget:
            untested = [p for p in all_pts if p not in probed]
            items = list(probed.items())

            if step % 3 == 2:
                best_pt = max(
                    untested,
                    key=lambda p: (
                        min((p[0] - px) ** 2 + (p[1] - py) ** 2 for px, py in probed),
                        rng.random(),
                    ),
                )
            else:
                scored: List[Tuple[float, Tuple[int, int]]] = []
                vals_list = list(probed.values())
                rare_score = {v: 1.0 / math.sqrt(vals_list.count(v)) for v in set(vals_list)}

                for ux, uy in untested:
                    dists = sorted(
                        (
                            math.hypot(ux - px, uy - py),
                            pv,
                        )
                        for (px, py), pv in items
                    )
                    k_near = dists[:3]
                    labels_near = [t[1] for t in k_near]
                    unique_lbls = set(labels_near)
                    min_d = k_near[0][0]
                    span_d = k_near[-1][0]

                    if len(unique_lbls) > 1 and span_d <= 5.5:
                        rarity = max(rare_score[l] for l in unique_lbls)
                        # Bisect wide disagreeing gaps first instead of packing adjacent pixels on walls
                        score = (10.0 * len(unique_lbls) + 4.0 * rarity) + 3.0 * min_d
                    else:
                        score = 0.5 * min_d
                    scored.append((score, (ux, uy)))

                max_sc = max(s for s, _ in scored)
                top_cands = [pt for s, pt in scored if s >= max_sc - 1e-6]
                best_pt = rng.choice(top_cands)

            probed[best_pt] = truth[best_pt]
            query_order.append(best_pt)
            step += 1

    elif strategy == "hybrid_mapper":
        # Stage A: Coverage-preserving stride-2 coarse grid skeleton (6x8 = 48 pts covers every 2x2 box)
        if budget >= 56:
            skel_xs = (2, 4, 6, 8, 10, 12)
            skel_ys = (0, 2, 4, 6, 8, 10, 12, 14)
        else:
            skel_xs = (2, 5, 8, 11)
            skel_ys = (0, 3, 6, 9, 12, 15)
        for x in skel_xs:
            for y in skel_ys:
                if len(probed) >= budget:
                    break
                probed[(x, y)] = truth[(x, y)]
                query_order.append((x, y))

        # Stage B: Spend remaining probes refining uncertain boundaries across skeleton gaps
        stage_b_pts: List[Tuple[int, int]] = []
        while len(probed) < budget:
            untested = [p for p in all_pts if p not in probed]
            vals_list = list(probed.values())
            rare_score = {v: 1.0 / math.sqrt(vals_list.count(v)) for v in set(vals_list)}
            scored_h: List[Tuple[float, Tuple[int, int]]] = []

            for ux, uy in untested:
                # Inspect local Chebyshev<=2 probed window around candidate
                local_vals = [
                    pv
                    for (px, py), pv in probed.items()
                    if abs(ux - px) <= 2 and abs(uy - py) <= 2
                ]
                uniq_local = set(local_vals)
                if len(uniq_local) > 1:
                    # Count immediate L1<=1 and diagonal L1==2 disagreeing brackets
                    imm_vals = [
                        probed[(ux + dx, uy + dy)]
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                        if (ux + dx, uy + dy) in probed
                    ]
                    diag_vals = [
                        probed[(ux + dx, uy + dy)]
                        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1))
                        if (ux + dx, uy + dy) in probed
                    ]
                    opposite_pairs = 0
                    for (dx1, dy1), (dx2, dy2) in (
                        ((-1, 0), (1, 0)),
                        ((0, -1), (0, 1)),
                        ((-1, -1), (1, 1)),
                        ((-1, 1), (1, -1)),
                    ):
                        p1, p2 = (ux + dx1, uy + dy1), (ux + dx2, uy + dy2)
                        if p1 in probed and p2 in probed and probed[p1] != probed[p2]:
                            opposite_pairs += 1
                    rarity = max(rare_score[l] for l in uniq_local)
                    spread_b = (
                        min(math.hypot(ux - bx, uy - by) for bx, by in stage_b_pts)
                        if stage_b_pts
                        else 2.0
                    )
                    sc = (
                        10.0 * (len(uniq_local) - 1)
                        + 5.0 * opposite_pairs
                        + 3.0 * len(set(imm_vals))
                        + 1.5 * len(set(diag_vals))
                        + 3.0 * rarity
                        + min(spread_b, 3.0)
                    )
                else:
                    sc = min(math.hypot(ux - px, uy - py) for px, py in probed)
                scored_h.append((sc, (ux, uy)))

            max_sc = max(s for s, _ in scored_h)
            top_cands = [pt for s, pt in scored_h if s >= max_sc - 1e-6]
            best_pt = rng.choice(top_cands)
            probed[best_pt] = truth[best_pt]
            stage_b_pts.append(best_pt)
            query_order.append(best_pt)
    else:
        raise ValueError(f"Unknown cartographer strategy: {strategy!r}")

    # Evaluate against ground truth on all 192 points AND strictly held-out unseen points
    comps = extract_connected_components(truth)
    b_cells = boundary_cells(truth)
    unseen_pts = [p for p in all_pts if p not in probed]
    pred_map = infer_voronoi_map(probed)

    hit_comps = [c for c in comps if any(cell in probed for cell in c["cells"])]
    non_pass_comps = [c for c in comps if c["label"] != 0xAA]
    hit_non_pass = [c for c in non_pass_comps if any(cell in probed for cell in c["cells"])]
    small_comps = [c for c in non_pass_comps if c["is_small"]]
    hit_small = [c for c in small_comps if any(cell in probed for cell in c["cells"])]

    unseen_acc = (
        sum(1 for p in unseen_pts if pred_map[p] == truth[p]) / len(unseen_pts)
        if unseen_pts
        else 1.0
    )
    full_acc = sum(1 for p in all_pts if pred_map[p] == truth[p]) / len(all_pts)
    unseen_b = [p for p in unseen_pts if p in b_cells]
    boundary_acc = (
        sum(1 for p in unseen_b if pred_map[p] == truth[p]) / len(unseen_b)
        if unseen_b
        else 1.0
    )

    first_comp_hit: Dict[str, Optional[int]] = {}
    for c in non_pass_comps:
        cset = set(c["cells"])
        hit_idx = next((i + 1 for i, pt in enumerate(query_order) if pt in cset), None)
        first_comp_hit[f"{c['label_hex']}_sz{c['size']}"] = hit_idx

    return {
        "strategy": strategy,
        "budget": len(probed),
        "components_hit": len(hit_comps),
        "components_total": len(comps),
        "failure_regions_hit": len(hit_non_pass),
        "failure_regions_total": len(non_pass_comps),
        "small_islands_hit": len(hit_small),
        "small_islands_total": len(small_comps),
        "unseen_accuracy": round(unseen_acc, 4),
        "full_accuracy": round(full_acc, 4),
        "boundary_accuracy": round(boundary_acc, 4),
        "first_region_queries": first_comp_hit,
        "probed": {f"{x},{y}": v for (x, y), v in probed.items()},
        "pred_map": pred_map,
    }


def _collect_truth_worker(sid: int) -> Tuple[int, Dict[Tuple[int, int], int]]:
    return sid, collect_surface_truth_rtl(sid)


def run_stage1_study(
    *,
    budget: int = 64,
    seeds: Sequence[int] = (20267001, 20267002, 20267003, 20267004, 20267005),
    workers: int = min(7, os.cpu_count() or 4),
    replay_repeats: int = 5,
) -> Dict[str, Any]:
    t0 = time.time()
    all_sids = tuple(sorted(SURFACE_SPECS.keys()))
    truths: Dict[int, Dict[Tuple[int, int], int]] = {}

    with ProcessPoolExecutor(max_workers=min(workers, len(all_sids))) as pool:
        futs = [pool.submit(_collect_truth_worker, sid) for sid in all_sids]
        for fut in as_completed(futs):
            sid, tr_map = fut.result()
            truths[sid] = tr_map
            print(f"[truth] surface {sid} ({SURFACE_SPECS[sid]['name']}) collected 192/192 RTL points", file=sys.stderr, flush=True)

    strategies = ("uniform_grid", "blind_random", "adaptive_cartographer")
    results: Dict[int, Dict[str, List[Dict[str, Any]]]] = {sid: {s: [] for s in strategies} for sid in all_sids}

    for sid in all_sids:
        tr_map = truths[sid]
        for sd in seeds:
            for strat in strategies:
                # Strip pred_map in serialized summary later, keep cleanly for plots on seed 0
                res = run_cartography_strategy(tr_map, strategy=strat, budget=budget, seed=sd)
                results[sid][strat].append(res)

    # Verify deterministic live RTL replay (5/5) for one failure cell per non-pass component across ALL 7 surfaces
    replay_records: List[Dict[str, Any]] = []
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for sid in all_sids:
            comps = [c for c in extract_connected_components(truths[sid]) if c["label"] != 0xAA]
            for comp in comps:
                cx, cy = comp["cells"][0]
                words = build_spi_2d_program(0x06, 0x05, sclk_low_cycles=cx)
                client.sync_program(words)
                matches = 0
                for _ in range(replay_repeats):
                    transport.configure_target(10, delay_ns=sid)
                    r = client.run_trial(cy)
                    if r["sample_reg"] == comp["label"]:
                        matches += 1
                replay_records.append(
                    {
                        "surface_id": sid,
                        "surface_name": SURFACE_SPECS[sid]["name"],
                        "component_label": comp["label_hex"],
                        "size": comp["size"],
                        "x_sclk_low": cx,
                        "y_gap_wait": cy,
                        "matches": matches,
                        "repeats": replay_repeats,
                    }
                )

    elapsed_s = round(time.time() - t0, 2)

    # Aggregate separately for dev (S0..S2) and held-out (S3..S6)
    def aggregate(sids_group: Sequence[int]) -> Dict[str, Any]:
        agg: Dict[str, Any] = {}
        for strat in strategies:
            runs = [r for sid in sids_group for r in results[sid][strat]]
            fail_recall = [
                r["failure_regions_hit"] / r["failure_regions_total"] if r["failure_regions_total"] else 1.0
                for r in runs
            ]
            small_recall = [
                r["small_islands_hit"] / r["small_islands_total"] if r["small_islands_total"] else 1.0
                for r in runs
            ]
            agg[strat] = {
                "runs": len(runs),
                "mean_unseen_accuracy": round(statistics.mean(r["unseen_accuracy"] for r in runs), 4),
                "mean_boundary_accuracy": round(statistics.mean(r["boundary_accuracy"] for r in runs), 4),
                "mean_failure_region_recall": round(statistics.mean(fail_recall), 4),
                "mean_small_island_recall": round(statistics.mean(small_recall), 4),
            }
        return agg

    per_surface_summary: List[Dict[str, Any]] = []
    for sid in all_sids:
        row: Dict[str, Any] = {
            "surface_id": sid,
            "name": SURFACE_SPECS[sid]["name"],
            "role": SURFACE_SPECS[sid]["role"],
            "failure_regions": results[sid]["uniform_grid"][0]["failure_regions_total"],
            "small_islands": results[sid]["uniform_grid"][0]["small_islands_total"],
        }
        for strat in strategies:
            rs = results[sid][strat]
            row[strat] = {
                "unseen_acc": round(statistics.mean(r["unseen_accuracy"] for r in rs), 4),
                "boundary_acc": round(statistics.mean(r["boundary_accuracy"] for r in rs), 4),
                "fail_hit": round(statistics.mean(r["failure_regions_hit"] for r in rs), 2),
                "small_hit": round(statistics.mean(r["small_islands_hit"] for r in rs), 2),
            }
        per_surface_summary.append(row)

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_2d_cartographer_stage1",
        "elapsed_seconds": elapsed_s,
        "budget": budget,
        "grid_size": len(X_VALS) * len(Y_VALS),
        "seeds": list(seeds),
        "dev_summary": aggregate(DEV_SURFACES),
        "heldout_summary": aggregate(HELD_SURFACES),
        "per_surface_summary": per_surface_summary,
        "replay_verification": replay_records,
        # Keep representative truth & seed-0 reconstructed maps for plotting
        "_plot_payload": {
            sid: {
                "truth": {f"{k[0]},{k[1]}": v for k, v in truths[sid].items()},
                "uniform": {f"{k[0]},{k[1]}": v for k, v in results[sid]["uniform_grid"][0]["pred_map"].items()},
                "adaptive": {f"{k[0]},{k[1]}": v for k, v in results[sid]["adaptive_cartographer"][0]["pred_map"].items()},
                "adaptive_probed": list(results[sid]["adaptive_cartographer"][0]["probed"].keys()),
            }
            for sid in (0, 3, 4, 6)
        },
    }


def format_stage1_markdown(rep: Dict[str, Any]) -> str:
    pct = (100.0 * rep["budget"]) / rep["grid_size"] if rep["grid_size"] else 0.0
    lines = [
        "# Stage 1: 2D Adaptive Timing-Surface Cartographer Evaluation",
        "",
        f"- **Timestamp (UTC):** {rep['timestamp_utc']}",
        f"- **Evidence class:** `{rep['evidence_class']}`",
        f"- **Axes:** `X = sclk_low_cycles (2..13)` (`20..130 ns`), `Y = gap_wait (0..15)` (`2..17` `CS_N` high cycles), `{rep['grid_size']}`-point space.",
        f"- **Matched query budget:** `{rep['budget']}` / `{rep['grid_size']}` (`{pct:.1f}%`), `{len(rep['seeds'])}` seeds (`{rep['seeds']}`), `0` RTL changes (`MODE_SPI_SURFACE=10`), finished in `{rep['elapsed_seconds']}s`.",
        "",
        "## Dev (`S0..S2`) vs Held-Out (`S3..S6`) Split Aggregate",
        "",
        "| Split | Strategy | Held-Out Grid Acc (%) | Boundary Acc (%) | Failure-Region Recall (%) | Narrow/Island Recall (%) |",
        "|---|---|---|---|---|---|",
    ]
    for split_name, grp in (("Dev (`S0..S2`)", rep["dev_summary"]), ("**Held-Out (`S3..S6`)**", rep["heldout_summary"])):
        for st in ("uniform_grid", "blind_random", "adaptive_cartographer"):
            v = grp[st]
            lines.append(
                f"| {split_name} | `{st}` | **{v['mean_unseen_accuracy']*100:.1f}%** | "
                f"**{v['mean_boundary_accuracy']*100:.1f}%** | {v['mean_failure_region_recall']*100:.1f}% | "
                f"{v['mean_small_island_recall']*100:.1f}% |"
            )

    lines += [
        "",
        f"## Per-Surface Breakdown (`{rep['budget']}` Queries Each)",
        "",
        "| Surface | Role | Regions (Small) | `uniform_grid` (Unseen / Bnd / Reg) | `blind_random` (Unseen / Bnd / Reg) | `adaptive_cartographer` (Unseen / Bnd / Reg) |",
        "|---|---|---|---|---|---|",
    ]
    for row in rep["per_surface_summary"]:
        ftot = row["failure_regions"]
        cells = []
        for st in ("uniform_grid", "blind_random", "adaptive_cartographer"):
            v = row[st]
            cells.append(f"{v['unseen_acc']*100:.1f}% / {v['boundary_acc']*100:.1f}% / `{v['fail_hit']:.1f}/{ftot}`")
        lines.append(
            f"| `{row['name']}` | `{row['role']}` | `{ftot}` (`{row['small_islands']}`) | " + " | ".join(cells) + " |"
        )

    rep_ok = sum(1 for r in rep["replay_verification"] if r["matches"] == r["repeats"])
    lines += [
        "",
        f"## Deterministic RTL Replay Verification (`{rep_ok}/{len(rep['replay_verification'])}` Regions Matched `5/5`)",
        "",
        "| Surface | Region Tag | Size | Sample `(X, Y)` | Replay |",
        "|---|---|---|---|---|",
    ]
    for rv in rep["replay_verification"]:
        lines.append(
            f"| `{rv['surface_name']}` | `{rv['component_label']}` | {rv['size']} | `({rv['x_sclk_low']}, {rv['y_gap_wait']})` | `{rv['matches']}/{rv['repeats']}` |"
        )
    return "\n".join(lines) + "\n"


def save_stage1_plot(rep: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    payload = rep["_plot_payload"]
    # Encode labels to discrete palette indices
    palette_vals = [0xAA, 0x00, 0x55, 0x11, 0x22, 0x33, 0x44, 0x66, 0x77, 0x88, 0xC4, 0xEE]
    cmap_cols = [
        "#e9f5ec",  # PASS 0xAA (soft green)
        "#495057",  # DROP 0x00
        "#f77f00",  # SETUP 0x55
        "#4cc9f0",  # 0x11
        "#4895ef",  # 0x22
        "#9b5de5",  # 0x33 pinhole A
        "#f15bb5",  # 0x44 pinhole B
        "#e63946",  # 0x66 comb
        "#ffbe0b",  # 0x77
        "#fb5607",  # 0x88
        "#8338ec",  # 0xC4 watchdog
        "#d00000",  # 0xEE notch / ridge
    ]
    cmap = ListedColormap(cmap_cols)
    val_to_idx = {v: i for i, v in enumerate(palette_vals)}

    def to_mat(d: Dict[str, int]) -> List[List[int]]:
        return [[val_to_idx.get(d[f"{x},{y}"], 1) for x in X_VALS] for y in Y_VALS]

    sids = [0, 3, 4, 6]
    fig, axes = plt.subplots(len(sids), 3, figsize=(10.5, 11.2), dpi=150)

    for row_i, sid in enumerate(sids):
        p = payload[sid]
        b = rep["budget"]
        g = rep["grid_size"]
        mats = [
            (f"Ground Truth ({g} pts)", to_mat(p["truth"]), []),
            (f"Uniform Grid ({b} pts -> {g})", to_mat(p["uniform"]), []),
            (f"Adaptive Cartographer ({b} pts -> {g})", to_mat(p["adaptive"]), p["adaptive_probed"]),
        ]
        for col_i, (title, mat, pts) in enumerate(mats):
            ax = axes[row_i][col_i]
            ax.imshow(mat, origin="lower", cmap=cmap, vmin=0, vmax=len(palette_vals) - 1, aspect="auto")
            if pts:
                pxs = [int(s.split(",")[0]) - X_VALS[0] for s in pts]
                pys = [int(s.split(",")[1]) - Y_VALS[0] for s in pts]
                ax.scatter(pxs, pys, s=8, c="black", marker="+", linewidths=0.7, alpha=0.65)
            if row_i == 0:
                ax.set_title(title, fontsize=9.5, fontweight="bold")
            if col_i == 0:
                ax.set_ylabel(f"{SURFACE_SPECS[sid]['name']}\nY (gap_wait)", fontsize=8.5)
            else:
                ax.set_yticks([])
            if row_i == len(sids) - 1:
                ax.set_xlabel("X (sclk_low_cycles)", fontsize=8.5)
                ax.set_xticks(range(0, len(X_VALS), 3))
                ax.set_xticklabels([str(X_VALS[k]) for k in range(0, len(X_VALS), 3)], fontsize=7.5)
            else:
                ax.set_xticks([])

    fig.suptitle(f"Stage 1: 2D Timing-Surface Reconstruction ({rep['budget']}/{rep['grid_size']} Probes)", fontsize=12, fontweight="bold", y=0.995)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def run_hybrid_eval_study(
    *,
    budget: int = 64,
    seeds: Sequence[int] = (20268001, 20268002, 20268003, 20268004, 20268005),
    workers: int = min(6, os.cpu_count() or 4),
    replay_repeats: int = 5,
) -> Dict[str, Any]:
    """Evaluate coverage-preserving hybrid_mapper on the fresh held-out S7..S12 suite."""
    t0 = time.time()
    sids = NEW_HELD_SURFACES
    truths: Dict[int, Dict[Tuple[int, int], int]] = {}
    with ProcessPoolExecutor(max_workers=min(workers, len(sids))) as pool:
        futs = [pool.submit(_collect_truth_worker, sid) for sid in sids]
        for fut in as_completed(futs):
            sid, tr_map = fut.result()
            truths[sid] = tr_map
            print(f"[truth] surface {sid} ({SURFACE_SPECS[sid]['name']}) collected 192/192 RTL points", file=sys.stderr, flush=True)

    strategies = ("uniform_grid", "blind_random", "adaptive_cartographer", "hybrid_mapper")
    results: Dict[int, Dict[str, List[Dict[str, Any]]]] = {sid: {s: [] for s in strategies} for sid in sids}
    for sid in sids:
        tr_map = truths[sid]
        for sd in seeds:
            for strat in strategies:
                results[sid][strat].append(
                    run_cartography_strategy(tr_map, strategy=strat, budget=budget, seed=sd)
                )

    replay_records: List[Dict[str, Any]] = []
    with IcarusSimTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        for sid in sids:
            comps = [c for c in extract_connected_components(truths[sid]) if c["label"] != 0xAA]
            for comp in comps:
                cx, cy = comp["cells"][0]
                client.sync_program(build_spi_2d_program(0x06, 0x05, sclk_low_cycles=cx))
                matches = 0
                for _ in range(replay_repeats):
                    transport.configure_target(10, delay_ns=sid)
                    r = client.run_trial(cy)
                    if r["sample_reg"] == comp["label"]:
                        matches += 1
                replay_records.append(
                    {
                        "surface_id": sid,
                        "surface_name": SURFACE_SPECS[sid]["name"],
                        "component_label": comp["label_hex"],
                        "size": comp["size"],
                        "x_sclk_low": cx,
                        "y_gap_wait": cy,
                        "matches": matches,
                        "repeats": replay_repeats,
                    }
                )

    elapsed_s = round(time.time() - t0, 2)
    summary: Dict[str, Any] = {}
    for strat in strategies:
        runs = [r for sid in sids for r in results[sid][strat]]
        fail_rec = [r["failure_regions_hit"] / r["failure_regions_total"] for r in runs]
        small_rec = [
            r["small_islands_hit"] / r["small_islands_total"] if r["small_islands_total"] else 1.0
            for r in runs
        ]
        summary[strat] = {
            "runs": len(runs),
            "mean_unseen_accuracy": round(statistics.mean(r["unseen_accuracy"] for r in runs), 4),
            "mean_boundary_accuracy": round(statistics.mean(r["boundary_accuracy"] for r in runs), 4),
            "mean_failure_region_recall": round(statistics.mean(fail_rec), 4),
            "mean_small_island_recall": round(statistics.mean(small_rec), 4),
        }

    per_surface: List[Dict[str, Any]] = []
    for sid in sids:
        row: Dict[str, Any] = {
            "surface_id": sid,
            "name": SURFACE_SPECS[sid]["name"],
            "failure_regions": results[sid]["uniform_grid"][0]["failure_regions_total"],
            "small_islands": results[sid]["uniform_grid"][0]["small_islands_total"],
        }
        for st in strategies:
            rs = results[sid][st]
            row[st] = {
                "unseen_acc": round(statistics.mean(r["unseen_accuracy"] for r in rs), 4),
                "boundary_acc": round(statistics.mean(r["boundary_accuracy"] for r in rs), 4),
                "fail_hit": round(statistics.mean(r["failure_regions_hit"] for r in rs), 2),
                "small_hit": round(statistics.mean(r["small_islands_hit"] for r in rs), 2),
            }
        per_surface.append(row)

    # Pre-declared PoC Gate Check:
    # 1) Retain uniform_grid failure-region & narrow-island recall (hybrid >= uniform_grid)
    # 2) Improve boundary accuracy over uniform_grid by >= +5.0 pp (+0.05) AND beat adaptive_cartographer
    u = summary["uniform_grid"]
    a = summary["adaptive_cartographer"]
    h = summary["hybrid_mapper"]
    gate_retains_regions = (
        h["mean_failure_region_recall"] >= u["mean_failure_region_recall"]
        and h["mean_small_island_recall"] >= u["mean_small_island_recall"]
    )
    bnd_gain_pp = round((h["mean_boundary_accuracy"] - u["mean_boundary_accuracy"]) * 100.0, 2)
    gate_bnd_gain = (bnd_gain_pp >= 5.0) and (h["mean_boundary_accuracy"] > a["mean_boundary_accuracy"])
    gate_replay_ok = all(rv["matches"] == rv["repeats"] for rv in replay_records)

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "rtl_simulation_2d_hybrid_cartographer_heldout",
        "elapsed_seconds": elapsed_s,
        "budget": budget,
        "grid_size": len(X_VALS) * len(Y_VALS),
        "seeds": list(seeds),
        "strategies": list(strategies),
        "summary": summary,
        "per_surface": per_surface,
        "replay_verification": replay_records,
        "poc_gate": {
            "passed": bool(gate_retains_regions and gate_bnd_gain and gate_replay_ok),
            "retains_region_discovery": gate_retains_regions,
            "boundary_gain_vs_grid_pp": bnd_gain_pp,
            "meets_5pp_boundary_gain_and_beats_adaptive": gate_bnd_gain,
            "all_regions_replay_ok": gate_replay_ok,
        },
        "_plot_payload": {
            sid: {
                "truth": {f"{k[0]},{k[1]}": v for k, v in truths[sid].items()},
                "uniform": {f"{k[0]},{k[1]}": v for k, v in results[sid]["uniform_grid"][0]["pred_map"].items()},
                "adaptive": {f"{k[0]},{k[1]}": v for k, v in results[sid]["adaptive_cartographer"][0]["pred_map"].items()},
                "hybrid": {f"{k[0]},{k[1]}": v for k, v in results[sid]["hybrid_mapper"][0]["pred_map"].items()},
                "hybrid_probed": list(results[sid]["hybrid_mapper"][0]["probed"].keys()),
            }
            for sid in (7, 8, 9, 10)
        },
    }


def format_hybrid_markdown(rep: Dict[str, Any]) -> str:
    pct = (100.0 * rep["budget"]) / rep["grid_size"] if rep["grid_size"] else 0.0
    g = rep["poc_gate"]
    lines = [
        "# Coverage-Preserving Hybrid Cartographer Evaluation (`S7..S12` New Held-Out Set)",
        "",
        f"- **Timestamp (UTC):** {rep['timestamp_utc']}",
        f"- **Evidence class:** `{rep['evidence_class']}`",
        f"- **Matched budget:** `{rep['budget']}` / `{rep['grid_size']}` (`{pct:.1f}%`), `5` seeds (`{rep['seeds']}`), finished in `{rep['elapsed_seconds']}s`.",
        f"- **Pre-declared PoC Gate:** **{'PASS' if g['passed'] else 'FAIL'}** "
        f"(retains region/island recall: `{g['retains_region_discovery']}`, "
        f"boundary gain vs `uniform_grid`: `{g['boundary_gain_vs_grid_pp']:+.2f} pp` [threshold `>= +5.0 pp` & beats `adaptive`], "
        f"5/5 RTL replay: `{g['all_regions_replay_ok']}`)",
        "",
        "## Aggregate Comparison on `S7..S12` (New Held-Out Surfaces)",
        "",
        "| Strategy | Held-Out Grid Acc | Boundary Acc | Failure-Region Recall | Narrow-Island Recall |",
        "|---|---|---|---|---|",
    ]
    for st in rep["strategies"]:
        v = rep["summary"][st]
        lines.append(
            f"| `{st}` | **{v['mean_unseen_accuracy']*100:.1f}%** | **{v['mean_boundary_accuracy']*100:.1f}%** | "
            f"{v['mean_failure_region_recall']*100:.1f}% | {v['mean_small_island_recall']*100:.1f}% |"
        )
    lines += [
        "",
        f"## Per-Surface Results (`{rep['budget']}` Probes Each, Unseen / Boundary / Regions)",
        "",
        "| Surface | Regions (Small) | `uniform_grid` | `blind_random` | `adaptive_cartographer` | `hybrid_mapper` |",
        "|---|---|---|---|---|---|",
    ]
    for row in rep["per_surface"]:
        ft = row["failure_regions"]
        cells = []
        for st in rep["strategies"]:
            v = row[st]
            cells.append(f"{v['unseen_acc']*100:.1f}% / {v['boundary_acc']*100:.1f}% / `{v['fail_hit']:.1f}/{ft}`")
        lines.append(f"| `{row['name']}` | `{ft}` (`{row['small_islands']}`) | " + " | ".join(cells) + " |")
    rep_ok = sum(1 for rv in rep["replay_verification"] if rv["matches"] == rv["repeats"])
    lines += [
        "",
        f"## Live RTL Replay Verification (`{rep_ok}/{len(rep['replay_verification'])}` Regions Matched `5/5`)",
        "",
        "| Surface | Region Tag | Size | Sample `(X, Y)` | Replay |",
        "|---|---|---|---|---|",
    ]
    for rv in rep["replay_verification"]:
        lines.append(
            f"| `{rv['surface_name']}` | `{rv['component_label']}` | {rv['size']} | `({rv['x_sclk_low']}, {rv['y_gap_wait']})` | `{rv['matches']}/{rv['repeats']}` |"
        )
    return "\n".join(lines) + "\n"


def save_hybrid_plot(rep: Dict[str, Any], out_png: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    payload = rep["_plot_payload"]
    palette_vals = [0xAA, 0x00, 0x55, 0x11, 0x22, 0x33, 0x44, 0x66, 0x77, 0x88, 0xC4, 0xEE]
    cmap_cols = [
        "#e9f5ec", "#495057", "#f77f00", "#4cc9f0", "#4895ef", "#9b5de5",
        "#f15bb5", "#e63946", "#ffbe0b", "#fb5607", "#8338ec", "#d00000",
    ]
    cmap = ListedColormap(cmap_cols)
    val_to_idx = {v: i for i, v in enumerate(palette_vals)}

    def to_mat(d: Dict[str, int]) -> List[List[int]]:
        return [[val_to_idx.get(d[f"{x},{y}"], 1) for x in X_VALS] for y in Y_VALS]

    sids = [7, 8, 9, 10]
    fig, axes = plt.subplots(len(sids), 4, figsize=(13.2, 11.2), dpi=150)
    b = rep["budget"]
    g = rep["grid_size"]

    for row_i, sid in enumerate(sids):
        p = payload[sid]
        mats = [
            (f"Ground Truth ({g})", to_mat(p["truth"]), []),
            (f"Uniform Grid ({b})", to_mat(p["uniform"]), []),
            (f"Adaptive ({b})", to_mat(p["adaptive"]), []),
            (f"Hybrid Mapper ({b})", to_mat(p["hybrid"]), p["hybrid_probed"]),
        ]
        for col_i, (title, mat, pts) in enumerate(mats):
            ax = axes[row_i][col_i]
            ax.imshow(mat, origin="lower", cmap=cmap, vmin=0, vmax=len(palette_vals) - 1, aspect="auto")
            if pts:
                pxs = [int(s.split(",")[0]) - X_VALS[0] for s in pts]
                pys = [int(s.split(",")[1]) - Y_VALS[0] for s in pts]
                ax.scatter(pxs, pys, s=8, c="black", marker="+", linewidths=0.7, alpha=0.65)
            if row_i == 0:
                ax.set_title(title, fontsize=9.5, fontweight="bold")
            if col_i == 0:
                ax.set_ylabel(f"{SURFACE_SPECS[sid]['name']}\nY (gap_wait)", fontsize=8.5)
            else:
                ax.set_yticks([])
            if row_i == len(sids) - 1:
                ax.set_xlabel("X (sclk_low)", fontsize=8.5)
                ax.set_xticks(range(0, len(X_VALS), 3))
                ax.set_xticklabels([str(X_VALS[k]) for k in range(0, len(X_VALS), 3)], fontsize=7.5)
            else:
                ax.set_xticks([])

    fig.suptitle(f"Hybrid Cartographer vs Baselines on New Held-Out Set ({b}/{g} Probes)", fontsize=12, fontweight="bold", y=0.995)
    fig.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1 Adaptive 2D Protocol Cartographer evaluation")
    parser.add_argument("--suite", choices=("stage1", "hybrid"), default="stage1")
    parser.add_argument("--budget", type=int, default=64)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=min(7, os.cpu_count() or 4))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--png-out", type=Path)
    args = parser.parse_args()

    if args.suite == "hybrid":
        seed_list = [20268001 + i for i in range(args.seeds)]
        rep = run_hybrid_eval_study(budget=args.budget, seeds=seed_list, workers=args.workers)
        md = format_hybrid_markdown(rep)
        if args.png_out:
            save_hybrid_plot(rep, args.png_out)
    else:
        seed_list = [20267001 + i for i in range(args.seeds)]
        rep = run_stage1_study(budget=args.budget, seeds=seed_list, workers=args.workers)
        md = format_stage1_markdown(rep)
        if args.png_out:
            save_stage1_plot(rep, args.png_out)
    rep_clean = {k: v for k, v in rep.items() if k != "_plot_payload"}
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep_clean, indent=2, sort_keys=True), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
