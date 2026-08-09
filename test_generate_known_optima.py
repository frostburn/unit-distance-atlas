#!/usr/bin/env python3
"""Regression tests for the exact small unit-distance constructions."""

from __future__ import annotations

import math
import unittest

import generate


class KnownExactMoserTests(unittest.TestCase):
    def make_host(self, n: int) -> generate.Host:
        points = generate.KNOWN_EXACT_MOSER_POINTS[n]
        spec = generate.HostSpec(
            key=f"test-known-exact-moser-{n}",
            label=f"test known optimum n={n}",
            kind="explicit",
            m=0,
            basis="moser",
            explicit_points=points,
        )
        return generate.build_host(spec)

    def test_point_and_edge_counts(self) -> None:
        self.assertEqual(
            set(generate.KNOWN_EXACT_MOSER_POINTS),
            set(generate.KNOWN_EXACT_EDGE_COUNTS),
        )

        for n, expected_edges in generate.KNOWN_EXACT_EDGE_COUNTS.items():
            with self.subTest(n=n):
                host = self.make_host(n)
                self.assertEqual(len(host.points), n)
                self.assertEqual(sum(map(len, host.neighbors)) // 2, expected_edges)

    def test_exact_adjacency_agrees_with_planar_unit_distances(self) -> None:
        """The exact step test must find every and only unit-length pair."""
        for n in generate.KNOWN_EXACT_EDGE_COUNTS:
            with self.subTest(n=n):
                host = self.make_host(n)
                coords = generate.coordinates_for_host(host)
                adjacency = [set(row) for row in host.neighbors]

                for i in range(n):
                    for j in range(i + 1, n):
                        numerically_unit = math.isclose(
                            abs(coords[i] - coords[j]),
                            1.0,
                            rel_tol=0.0,
                            abs_tol=2e-12,
                        )
                        exactly_adjacent = j in adjacency[i]
                        self.assertEqual(
                            exactly_adjacent,
                            numerically_unit,
                            msg=f"n={n}, pair=({i}, {j})",
                        )

    def test_known_records_obey_strict_improvement_policy(self) -> None:
        best_edges = [-1] * 22
        winning: dict[int, generate.PeelRun] = {}

        installed = generate.seed_known_exact_constructions(
            min_n=1,
            max_n=21,
            best_edges=best_edges,
            winning=winning,
        )
        self.assertEqual(installed, 21)
        self.assertEqual(
            best_edges[1:],
            [generate.KNOWN_EXACT_EDGE_COUNTS[n] for n in range(1, 22)],
        )

        # Equal records are not rewritten.
        installed_again = generate.seed_known_exact_constructions(
            min_n=1,
            max_n=21,
            best_edges=best_edges,
            winning=winning,
        )
        self.assertEqual(installed_again, 0)

        # Nor is a hypothetical stronger record replaced by a known optimum.
        stronger = [-1] * 22
        stronger[19] = generate.KNOWN_EXACT_EDGE_COUNTS[19] + 1
        stronger_winning: dict[int, generate.PeelRun] = {}
        installed_over_stronger = generate.seed_known_exact_constructions(
            min_n=19,
            max_n=19,
            best_edges=stronger,
            winning=stronger_winning,
        )
        self.assertEqual(installed_over_stronger, 0)
        self.assertNotIn(19, stronger_winning)


if __name__ == "__main__":
    unittest.main()
