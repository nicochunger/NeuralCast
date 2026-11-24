"""Deterministic 2D constellation layout for station playlist graphs."""

from __future__ import annotations

import hashlib
import math
import random
from typing import Mapping, Sequence

COORD_MIN = 0.08
COORD_MAX = 0.92


def compute_constellation_layout(
    station_slug: str,
    node_slugs: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    node_weights: Mapping[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """Compute stable, well-spaced 2D coordinates in [0.08, 0.92] for each node.

    Uses a deterministic seed derived from ``station_slug``, places nodes on an
    initial seeded phyllotaxis / circle arrangement ordered by connectivity,
    runs force-directed refinement with bridge attraction and pairwise
    repulsion, enforces minimum pairwise separation so no nodes stack, and
    normalizes into ``[0.08, 0.92]``.
    """
    ordered_slugs = sorted(set(node_slugs))
    count = len(ordered_slugs)
    if count == 0:
        return {}
    if count == 1:
        return {ordered_slugs[0]: (0.5, 0.5)}
    if count == 2:
        return {ordered_slugs[0]: (0.3, 0.5), ordered_slugs[1]: (0.7, 0.5)}

    index_by_slug = {slug: idx for idx, slug in enumerate(ordered_slugs)}
    weighted_edges: list[tuple[int, int, float]] = []
    degree_weight = [0.0] * count
    for src, dst, raw_weight in edges:
        if src == dst or src not in index_by_slug or dst not in index_by_slug:
            continue
        weight = max(0.05, float(raw_weight))
        u = index_by_slug[src]
        v = index_by_slug[dst]
        weighted_edges.append((u, v, weight))
        degree_weight[u] += weight
        degree_weight[v] += weight

    seed_digest = hashlib.sha256(f"sonic-atlas-layout:{station_slug}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(seed_digest[:8], "big"))

    # Order nodes by connectivity so central bridged hubs start nearer the middle
    # while isolated playlists form a balanced outer constellation ring.
    rank_order = sorted(
        range(count),
        key=lambda idx: (
            -degree_weight[idx],
            -(float(node_weights.get(ordered_slugs[idx], 1.0)) if node_weights else 1.0),
            ordered_slugs[idx],
        ),
    )

    pos_x = [0.0] * count
    pos_y = [0.0] * count
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    phase = rng.uniform(0.0, 2.0 * math.pi)
    for rank, idx in enumerate(rank_order):
        radius = 0.22 + 0.78 * math.sqrt((rank + 0.5) / count)
        angle = phase + rank * golden_angle + rng.uniform(-0.08, 0.08)
        # Mild horizontal stretch (1.35:1) to match the landscape 1000x620 SVG canvas.
        pos_x[idx] = math.cos(angle) * radius * 1.35
        pos_y[idx] = math.sin(angle) * radius

    area = 4.5 * 3.0
    ideal_k = math.sqrt(area / count)
    iterations = 180
    max_edge_weight = max((w for _, _, w in weighted_edges), default=1.0)

    for step in range(iterations):
        disp_x = [0.0] * count
        disp_y = [0.0] * count
        temperature = 0.45 * (1.0 - step / iterations) ** 1.3 + 0.01

        # Pairwise repulsion across all nodes (prevents overlap and stacking).
        for i in range(count):
            xi, yi = pos_x[i], pos_y[i]
            for j in range(i + 1, count):
                dx = xi - pos_x[j]
                dy = yi - pos_y[j]
                dist_sq = dx * dx + dy * dy
                if dist_sq < 1e-8:
                    angle = rng.uniform(0.0, 2.0 * math.pi)
                    dx = math.cos(angle) * 1e-3
                    dy = math.sin(angle) * 1e-3
                    dist = 1e-3
                else:
                    dist = math.sqrt(dist_sq)
                force = (ideal_k * ideal_k) / dist
                fx = (dx / dist) * force
                fy = (dy / dist) * force
                disp_x[i] += fx
                disp_y[i] += fy
                disp_x[j] -= fx
                disp_y[j] -= fy

        # Attractive spring forces along shared-track bridges.
        for u, v, weight in weighted_edges:
            dx = pos_x[u] - pos_x[v]
            dy = pos_y[u] - pos_y[v]
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                continue
            strength = 0.35 + 1.25 * (weight / max_edge_weight)
            force = (dist * dist / ideal_k) * strength * 0.45
            fx = (dx / dist) * force
            fy = (dy / dist) * force
            disp_x[u] -= fx
            disp_y[u] -= fy
            disp_x[v] += fx
            disp_y[v] += fy

        # Gentle centering gravity so disconnected components do not drift to infinity.
        for i in range(count):
            gravity = 0.09 if degree_weight[i] > 0 else 0.14
            disp_x[i] -= pos_x[i] * gravity * 0.85
            disp_y[i] -= pos_y[i] * gravity * 1.15

            disp_len = math.hypot(disp_x[i], disp_y[i])
            if disp_len > 1e-9:
                scale = min(disp_len, temperature) / disp_len
                pos_x[i] += disp_x[i] * scale
                pos_y[i] += disp_y[i] * scale

    norm_x, norm_y = _normalize_coordinates(pos_x, pos_y)

    # Final de-overlap pass in normalized space so labels & orbs never stack.
    min_dist = min(0.11, 0.72 / math.sqrt(count))
    for _ in range(40):
        moved = False
        for i in range(count):
            for j in range(i + 1, count):
                dx = norm_x[i] - norm_x[j]
                dy = norm_y[i] - norm_y[j]
                dist = math.hypot(dx, dy)
                if dist < min_dist:
                    if dist < 1e-6:
                        angle = (i * 2.39996 + j * 0.7) % (2.0 * math.pi)
                        ux, uy = math.cos(angle), math.sin(angle)
                    else:
                        ux, uy = dx / dist, dy / dist
                    push = (min_dist - dist) * 0.52
                    norm_x[i] = _clamp(norm_x[i] + ux * push)
                    norm_y[i] = _clamp(norm_y[i] + uy * push)
                    norm_x[j] = _clamp(norm_x[j] - ux * push)
                    norm_y[j] = _clamp(norm_y[j] - uy * push)
                    moved = True
        if not moved:
            break

    norm_x, norm_y = _normalize_coordinates(norm_x, norm_y)
    return {
        slug: (round(_clamp(norm_x[idx]), 4), round(_clamp(norm_y[idx]), 4))
        for idx, slug in enumerate(ordered_slugs)
    }


def _normalize_coordinates(
    xs: Sequence[float], ys: Sequence[float]
) -> tuple[list[float], list[float]]:
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x
    span_y = max_y - min_y
    target_span = COORD_MAX - COORD_MIN

    if span_x < 1e-9:
        out_x = [0.5 for _ in xs]
    else:
        out_x = [COORD_MIN + ((x - min_x) / span_x) * target_span for x in xs]

    if span_y < 1e-9:
        out_y = [0.5 for _ in ys]
    else:
        out_y = [COORD_MIN + ((y - min_y) / span_y) * target_span for y in ys]

    return out_x, out_y


def _clamp(value: float) -> float:
    return max(COORD_MIN, min(COORD_MAX, value))
