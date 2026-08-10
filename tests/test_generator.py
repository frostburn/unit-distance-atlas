from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from known_optima import KNOWN_EXACT_EDGE_COUNTS  # noqa: E402


class JsonMotionAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="unit-distance-motion-test-")
        cls.output = Path(cls.temporary.name)
        cls._run_generator(seed=5, reset=True)
        cls.catalog = json.loads((cls.output / "data" / "catalog.local.json").read_text())
        cls.records = [
            json.loads((cls.output / summary["record"]).read_text())
            for summary in cls.catalog["records"]
        ]
        cls.initial_counts = [record["edges"] for record in cls.records]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @classmethod
    def _run_generator(cls, *, seed: int, reset: bool = False) -> None:
        command = [
            sys.executable,
            str(PROJECT / "generate.py"),
            "--max-n", "25",
            "--restarts", "1",
            "--hosts", "hex,moser,z12,z18",
            "--ties-per-n", "8",
            "--seed", str(seed),
            "--output", str(cls.output),
            "--publish",
        ]
        if reset:
            command.append("--reset-search-state")
        subprocess.run(command, cwd=PROJECT, check=True, capture_output=True, text=True, timeout=90)

    def test_catalog_and_json_only_output(self) -> None:
        self.assertEqual(self.catalog["schemaVersion"], 2)
        self.assertEqual(self.catalog["rendering"], "dynamic canvas from JSON; no SVG assets")
        self.assertEqual(self.catalog["maxN"], 25)
        self.assertEqual(self.catalog["recordCount"], 25)
        self.assertFalse(list(self.output.rglob("*.svg")))
        self.assertFalse((self.output / "data" / "graphs").exists())
        for path in (self.output / "data").rglob("*"):
            if path.is_file():
                self.assertEqual(path.suffix, ".json", path)

    def test_known_exact_counts_through_21(self) -> None:
        for n, expected in KNOWN_EXACT_EDGE_COUNTS.items():
            if n > 21:
                continue
            record = self.records[n - 1]
            self.assertEqual(record["n"], n)
            self.assertEqual(record["edges"], expected)
            self.assertEqual(record["optimality"]["status"], "proven optimal")

    def test_geometry_and_unit_distances(self) -> None:
        for record in self.records:
            n = record["n"]
            coords = record["geometry"]["coordinates"]
            edges = record["geometry"]["edges"]
            self.assertEqual(len(coords), n)
            self.assertEqual(len(edges), 2 * record["edges"])
            for offset in range(0, len(edges), 2):
                a, b = edges[offset], edges[offset + 1]
                self.assertTrue(0 <= a < n and 0 <= b < n and a != b)
                dx = coords[a][0] - coords[b][0]
                dy = coords[a][1] - coords[b][1]
                self.assertAlmostEqual(math.hypot(dx, dy), 1.0, places=7)

    def test_transition_invariants(self) -> None:
        for index in range(1, len(self.records)):
            previous = self.records[index - 1]
            current = self.records[index]
            transition = current["transition"]
            self.assertEqual(transition["from"], previous["n"])
            self.assertIn(transition["kind"], {"growth", "transmutation"})
            mapping = transition["oldToNew"]
            added = transition["addedVertex"]
            self.assertEqual(len(mapping), previous["n"])
            self.assertEqual(len(set(mapping)), len(mapping))
            self.assertTrue(all(0 <= value < current["n"] for value in mapping))
            self.assertNotIn(added, mapping)

            current_edges = current["geometry"]["edges"]
            current_edge_set = {
                tuple(sorted((current_edges[offset], current_edges[offset + 1])))
                for offset in range(0, len(current_edges), 2)
            }
            if transition["kind"] == "growth":
                self.assertEqual(transition["retainedEdges"], previous["edges"])
                old_edges = previous["geometry"]["edges"]
                for offset in range(0, len(old_edges), 2):
                    mapped = tuple(sorted((mapping[old_edges[offset]], mapping[old_edges[offset + 1]])))
                    self.assertIn(mapped, current_edge_set)
                old_coords = previous["geometry"]["coordinates"]
                new_coords = current["geometry"]["coordinates"]
                for old_index, new_index in enumerate(mapping):
                    self.assertAlmostEqual(old_coords[old_index][0], new_coords[new_index][0], places=8)
                    self.assertAlmostEqual(old_coords[old_index][1], new_coords[new_index][1], places=8)
            else:
                self.assertEqual(transition["retainedEdges"], 0)

            neighbours = transition["spawnNeighbors"]
            if neighbours:
                coords = current["geometry"]["coordinates"]
                expected_x = sum(coords[i][0] for i in neighbours) / len(neighbours)
                expected_y = sum(coords[i][1] for i in neighbours) / len(neighbours)
                self.assertAlmostEqual(transition["spawn"][0], expected_x, places=8)
                self.assertAlmostEqual(transition["spawn"][1], expected_y, places=8)

    def test_z_incremental_search_never_regresses_edge_counts(self) -> None:
        self._run_generator(seed=20260810, reset=False)
        catalog = json.loads((self.output / "data" / "catalog.local.json").read_text())
        later = [
            json.loads((self.output / summary["record"]).read_text())["edges"]
            for summary in catalog["records"]
        ]
        self.assertEqual(len(later), len(self.initial_counts))
        self.assertTrue(all(new >= old for old, new in zip(self.initial_counts, later)))
        self.assertFalse(list(self.output.rglob("*.svg")))


if __name__ == "__main__":
    unittest.main()
