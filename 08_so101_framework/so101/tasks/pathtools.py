"""Stroke clean-up for pen drawing - the standard pen-plotter toolbox (as in vpype / AxiDraw tools):

- simplify:  Ramer-Douglas-Peucker - drops points that lie within `eps` of the line through their
             neighbours (removes pixel staircase and hand jitter, keeps corners sharp).
- merge:     joins strokes whose ends meet (within `tol`), so the pen lifts less often.
- order:     greedy nearest-neighbour ordering, reversing strokes when the far end is closer, so
             travel moves between strokes are short.
- drop_short: removes specks (strokes shorter than `min_len`).

Strokes are lists of (x, y) in any unit; every function returns new lists.
"""
from __future__ import annotations

import math


def length(st):
    return sum(math.dist(st[i], st[i + 1]) for i in range(len(st) - 1))


def simplify(st, eps):
    """Ramer-Douglas-Peucker (iterative, so long strokes can't hit the recursion limit)."""
    if len(st) < 3:
        return list(st)
    keep = [False] * len(st)
    keep[0] = keep[-1] = True
    stack = [(0, len(st) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = st[a], st[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = st[i]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n if n > 1e-12 else math.hypot(x - x1, y - y1)
            if d > best:
                best, idx = d, i
        if idx > 0 and best > eps:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(st, keep) if k]


def drop_short(strokes, min_len):
    return [st for st in strokes if len(st) >= 2 and length(st) >= min_len]


def merge(strokes, tol):
    """Join strokes end-to-end when an end of one is within tol of an end of another."""
    out = [list(s) for s in strokes]
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if math.dist(a[-1], b[0]) <= tol:
                    out[i] = a + b[1:]
                elif math.dist(a[-1], b[-1]) <= tol:
                    out[i] = a + b[::-1][1:]
                elif math.dist(a[0], b[-1]) <= tol:
                    out[i] = b + a[1:]
                elif math.dist(a[0], b[0]) <= tol:
                    out[i] = b[::-1] + a[1:]
                else:
                    continue
                del out[j]
                changed = True
                break
            if changed:
                break
    return out


def order(strokes, start=None):
    """Greedy nearest-neighbour: next stroke = the one with an end closest to the pen."""
    left = [list(s) for s in strokes]
    if not left:
        return []
    pen = start if start is not None else left[0][0]
    out = []
    while left:
        best, bi, rev = None, 0, False
        for i, st in enumerate(left):
            for r, end in ((False, st[0]), (True, st[-1])):
                d = math.dist(pen, end)
                if best is None or d < best:
                    best, bi, rev = d, i, r
        st = left.pop(bi)
        st = st[::-1] if rev else st
        out.append(st)
        pen = st[-1]
    return out


def travel(strokes):
    """Total pen-up travel between consecutive strokes."""
    return sum(math.dist(strokes[i][-1], strokes[i + 1][0]) for i in range(len(strokes) - 1))


def clean(strokes, eps, min_len, join_tol):
    """The usual chain: simplify -> merge touching ends -> drop specks -> order. (Merging first:
    a traced letter comes in pieces split at its junctions, each of which alone looks like a speck.)"""
    s = [simplify(st, eps) for st in strokes]
    s = merge(s, join_tol)
    s = drop_short(s, min_len)
    return order(s)
