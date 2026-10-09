"""CPU-only tests for the private, reference-derived Kimodo anchor dictionaries."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from constraints import _checked
from reference_constraints import load_reference, reference_pose, reclining_anchors


class ReferenceConstraintsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        frames = 62
        matrices = np.broadcast_to(np.eye(3), (frames, 77, 3, 3)).copy()
        matrices[60, 12] = Rotation.from_euler("z", 12, degrees=True).as_matrix()
        root = np.zeros((frames, 3))
        root[:, 1] = .8
        root[:, 0] = .15
        root[60] = [2, .6, 3]
        smooth = root.copy()
        for name in ("sit_ground", "lie_side_sleep"):
            np.savez(self.folder / (name + ".npz"), local_rot_mats=matrices,
                     root_positions=root, smooth_root_pos=smooth)
            (self.folder / (name + ".json")).write_text(
                json.dumps({"skeleton": {"joint_names": [f"S{i}" for i in range(77)]}}),
                encoding="utf-8")

    def test_seat_anchor_is_exactly_frame_60_without_loading_model(self):
        source = load_reference(self.folder / "sit_ground.npz", self.folder / "sit_ground.json")
        row = reference_pose(source, 60, 0)
        self.assertEqual(row["type"], "fullbody")
        np.testing.assert_array_equal(row["frame_indices"], [0])
        np.testing.assert_allclose(row["root_positions"], [[2, .6, 3]])
        np.testing.assert_allclose(row["smooth_root_2d"], [[2, 3]])
        np.testing.assert_allclose(row["local_joints_rot"][0, 12], [0, 0, np.deg2rad(12)], atol=1e-6)
        self.assertEqual(row["local_joints_rot"].shape, (1, 77, 3))
        self.assertEqual(len(_checked([row])), 1)

    def test_sleep_fallback_anchor_is_last_frame_and_source_remains_unchanged(self):
        rows = reclining_anchors(self.folder, 270)
        self.assertEqual([int(row["frame_indices"][0]) for row in rows], [0, 269])
        self.assertEqual(len(_checked(rows)), 2)
        rows[0]["root_positions"][0, 0] = -999
        source = load_reference(self.folder / "sit_ground.npz", self.folder / "sit_ground.json")
        self.assertEqual(float(source["root_positions"][60, 0]), 2)
        self.assertEqual(len(reclining_anchors(self.folder, 270, end_as_sleep=False)), 1)

    def test_reject_invalid_indices_skeleton_and_rotations(self):
        source = load_reference(self.folder / "sit_ground.npz", self.folder / "sit_ground.json")
        for src, dst in ((-1, 0), (62, 1), (0, -1), (True, 0), (1, 1.5)):
            with self.assertRaises(ValueError):
                reference_pose(source, src, dst)
        with self.assertRaises(ValueError):
            reclining_anchors(self.folder, 1)
        p = self.folder / "sit_ground.npz"
        source["local_rot_mats"][1, 5, 0, 0] = 4
        np.savez(p, **source)
        with self.assertRaisesRegex(ValueError, "rotation"):
            load_reference(p, self.folder / "sit_ground.json")


if __name__ == "__main__":
    unittest.main()
