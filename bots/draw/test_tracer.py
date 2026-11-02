#!/usr/bin/env python3
"""The tracer on fixed synthetic pictures; run directly: python3 test_tracer.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tracer  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def disc(size=48, radius=14, cx=24, cy=24):
    return [[0 if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2 else 255 for x in range(size)] for y in range(size)]


def noise(size=64):
    state = 12345
    rows = []
    for _ in range(size):
        row = []
        for _ in range(size):
            state = (state * 1103515245 + 12345) % 2**31
            row.append(state % 256)
        rows.append(row)
    return rows


def bounds(pieces):
    xs = [p[0] for piece in pieces for p in piece]
    ys = [p[1] for piece in pieces for p in piece]
    return min(xs), max(xs), min(ys), max(ys)


def test_a_disc_becomes_closed_outlines_around_where_it_is():
    pieces = tracer.trace(disc(), 100)
    assert pieces and all(piece[0] == piece[-1] for piece in pieces)
    low_x, high_x, low_y, high_y = bounds(pieces)
    assert 8 <= low_x <= 11 and 37 <= high_x <= 40 and 8 <= low_y <= 11 and 37 <= high_y <= 40


def test_the_outline_stays_near_the_true_circle():
    for piece in tracer.trace(disc(), 100):
        for x, y in piece:
            assert abs(((x - 23.5) ** 2 + (y - 23.5) ** 2) ** 0.5 - 14) < 3


def test_simplifying_cuts_points_but_keeps_the_ends():
    line = [(float(i), 0.0 if i % 2 else 0.2) for i in range(50)]
    simple = tracer.simplify(line, 0.5)
    assert simple == [line[0], line[-1]]


def test_two_shapes_give_two_separate_outlines_and_a_flat_picture_gives_none():
    two = [[255] * 60 for _ in range(30)]
    for y in range(8, 20):
        for x in list(range(6, 18)) + list(range(36, 50)):
            two[y][x] = 0
    pieces = tracer.trace(two, 100)
    lefts = {round(min(p[0] for p in piece)) // 20 for piece in pieces}
    assert lefts == {0, 1}
    assert tracer.trace([[200] * 30 for _ in range(30)], 100) == []


def test_busy_noise_never_exceeds_the_budget_and_points_stay_under_the_chunk_size():
    for budget in (10, 60, 150):
        pieces = tracer.trace(noise(), budget)
        assert 0 < len(pieces) <= budget
        assert all(2 <= len(piece) <= tracer.MAX_POINTS for piece in pieces)


def test_a_long_outline_is_split_into_pieces_that_share_a_joint():
    points = [(float(i), float(i % 7)) for i in range(300)]
    pieces = tracer.chunks(points)
    assert len(pieces) == 3 and all(len(p) <= tracer.MAX_POINTS for p in pieces)
    assert all(pieces[i][-1] == pieces[i + 1][0] for i in range(len(pieces) - 1))
    assert sum(len(p) for p in pieces) - (len(pieces) - 1) == 300


def test_the_same_picture_always_traces_the_same_way():
    assert tracer.trace(noise(), 40) == tracer.trace(noise(), 40)


if __name__ == "__main__":
    run_module_tests(globals())
