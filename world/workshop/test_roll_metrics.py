"""Synthetic-only geometric checks; no model or resident is loaded."""
import math
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from roll_metrics import pelvis_metrics, review_order


def samples(angles):
    poses = np.broadcast_to(np.eye(3), (len(angles), 77, 3, 3)).copy()
    poses[:, 0] = Rotation.from_euler("y", np.asarray(angles)[:, None], degrees=True).as_matrix()
    return poses


class PelvisMetricsTest(unittest.TestCase):
    def test_review_order_prioritizes_endpoint_then_unnecessary_turning(self):
        rows = [
            {"number": 3, "final_error_deg": 10, "path_deg": 90,
             "net_deg": 90, "peak_deg_per_s": 50},
            {"number": 2, "final_error_deg": 0, "path_deg": 450,
             "net_deg": 90, "peak_deg_per_s": 100},
            {"number": 4, "final_error_deg": 0, "path_deg": 90,
             "net_deg": 90, "peak_deg_per_s": 200},
            {"number": 1, "final_error_deg": 0, "path_deg": 90,
             "net_deg": 90, "peak_deg_per_s": 50},
        ]
        ordered = review_order(rows)
        self.assertEqual([row["number"] for row in ordered], [1, 4, 2, 3])
        self.assertEqual([row["number"] for row in rows], [3, 2, 4, 1])
        self.assertTrue(all("approved" not in row for row in ordered))

    def test_smooth_quarter_turn(self):
        measured = pelvis_metrics(samples([0, 30, 60, 90]), fps=30,
                                  target=Rotation.from_euler("y", 90, degrees=True).as_matrix())
        self.assertAlmostEqual(measured["path_deg"], 90)
        self.assertAlmostEqual(measured["net_deg"], 90)
        self.assertAlmostEqual(measured["path_to_net"], 1)
        self.assertAlmostEqual(measured["peak_deg_per_s"], 900)
        self.assertAlmostEqual(measured["final_error_deg"], 0)

    def test_backtracking_and_full_circle_are_not_silently_good(self):
        out = pelvis_metrics(samples([0, 90, 0]))
        self.assertAlmostEqual(out["path_deg"], 180)
        self.assertEqual(out["net_deg"], 0)
        self.assertTrue(math.isinf(out["path_to_net"]))
        continuous = pelvis_metrics(samples([0, 90, 180, 270, 360]))
        self.assertAlmostEqual(continuous["path_deg"], 360)
        self.assertTrue(math.isinf(continuous["path_to_net"]))

    def test_stationary_and_invalid_inputs(self):
        result = pelvis_metrics(samples([0, 0]))
        self.assertEqual(result["path_to_net"], 1)
        self.assertEqual(result["peak_deg_per_s"], 0)
        with self.assertRaisesRegex(ValueError, "Invalid"):
            pelvis_metrics(samples([0]))
        broken = samples([0, 1]); broken[0, 0, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "Invalid"):
            pelvis_metrics(broken)


if __name__ == "__main__":
    unittest.main()
