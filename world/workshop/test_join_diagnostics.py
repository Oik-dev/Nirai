"""Continuity comparisons require no generation model, avatar, or GPU."""
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from join_diagnostics import compare, pose_gap


class JoinDiagnosticTests(unittest.TestCase):
    def test_identical_pose_is_zero(self):
        identity = np.broadcast_to(np.eye(3), (2, 77, 3, 3)).copy()
        source = {"local_rot_mats": identity, "root_positions": np.zeros((2, 3))}
        gap = pose_gap(source, 0, source, 1, [str(i) for i in range(77)])
        self.assertEqual(gap["root_distance_m"], 0)
        self.assertEqual(gap["joint_max_deg"], 0)

    def test_root_and_joint_gaps_are_independent(self):
        identity = np.broadcast_to(np.eye(3), (2, 77, 3, 3)).copy()
        rotated = identity.copy()
        rotated[1, 10] = Rotation.from_euler("z", 30, degrees=True).as_matrix()
        original = {"local_rot_mats": identity, "root_positions": np.zeros((2, 3))}
        candidate = {"local_rot_mats": rotated,
                     "root_positions": np.array([[0., 0., 0.], [0.3, 0.4, 0.]])}
        gap = pose_gap(original, 0, candidate, 1, [str(i) for i in range(77)])
        self.assertEqual(gap["root_distance_m"], 0.5)
        self.assertAlmostEqual(gap["joint_max_deg"], 30)
        self.assertEqual(gap["most_different"][0]["joint"], "10")

    def test_bad_indices_are_rejected(self):
        source = {"local_rot_mats": np.broadcast_to(np.eye(3), (1, 77, 3, 3)).copy(),
                  "root_positions": np.zeros((1, 3))}
        for frame in (-1, 1, True):
            with self.assertRaises(ValueError):
                pose_gap(source, frame, source, 0, [str(i) for i in range(77)])

    def test_unknown_sleep_reference_is_rejected_before_loading_files(self):
        from pathlib import Path
        with self.assertRaises(ValueError):
            compare(Path("unused.npz"), Path("unused"), "unknown_sleep")


if __name__ == "__main__":
    unittest.main()
