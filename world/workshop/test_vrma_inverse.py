"""CPU-only roundtrip against the approved quiet sleep clip and fake frames."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from constraints import _checked
from npz_to_vrma import BODY, animation_to_soma, convert_arrays, position_animation
from reference_constraints import sleep_vrma_end_anchors
from vrma import read_tracks


WORLD = Path(__file__).resolve().parent.parent
SLEEP = WORLD / "window" / "assets" / "motions" / "眠る.vrma"
SKELETON = Path("D:/Products/AI-Models/Motion/D0/kimodo/lie_side_sleep.json")


class InverseVrmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skeleton = json.loads(SKELETON.read_text(encoding="utf-8"))["skeleton"]

    def test_real_quiet_sleep_roundtrip_keeps_all_22_bones(self):
        rotations, hips = read_tracks(SLEEP)
        self.assertEqual(set(rotations), set(BODY))
        soma = animation_to_soma(rotations, hips, self.skeleton)
        converted, _ = convert_arrays(soma, self.skeleton)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roundtrip.vrma"
            path.write_bytes(converted)
            got_rotations, got_hips = read_tracks(path)
        difference = max(float(np.degrees((Rotation.from_quat(got_rotations[bone]).inv()
                     * Rotation.from_quat(rotations[bone])).magnitude()).max()) for bone in BODY)
        self.assertLess(difference, 1e-3)
        np.testing.assert_allclose(got_hips, hips, atol=1e-6)

    def test_alignment_and_last_five_constraints(self):
        rotations, hips = read_tracks(SLEEP)
        seat = np.array([2.5, -1.25])
        rotated, moved = position_animation(rotations, hips, 135.0, seat)
        np.testing.assert_allclose(moved[0, [0, 2]], seat, atol=1e-6)
        expected = Rotation.from_euler("y", 135, degrees=True) * Rotation.from_quat(rotations["hips"][0])
        self.assertLess(np.degrees((Rotation.from_quat(rotated["hips"][0]).inv() * expected).magnitude()), 1e-4)
        np.testing.assert_allclose(hips, read_tracks(SLEEP)[1], atol=0)  # never edit the source
        anchors = sleep_vrma_end_anchors(SLEEP, self.skeleton, 120)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["type"], "fullbody")
        np.testing.assert_array_equal(anchors[0]["frame_indices"], np.arange(115, 120))
        self.assertEqual(anchors[0]["local_joints_rot"].shape, (5, 77, 3))
        self.assertNotIn("smooth_root_2d", anchors[0])
        self.assertEqual(len(_checked(anchors)), 1)
        np.testing.assert_allclose(anchors[0]["root_positions"][-1, [0, 2]], hips[-1, [0, 2]], atol=1e-5)
        # quiet_sleep closes the loop: the last constraint must meet the first frame.
        np.testing.assert_allclose(anchors[0]["root_positions"][-1], hips[0], atol=1e-5)

    def test_missing_bones_and_invalid_frame_count_are_rejected(self):
        rotations, hips = read_tracks(SLEEP)
        missing = rotations.copy()
        missing.pop("head")
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            animation_to_soma(missing, hips, self.skeleton)
        with self.assertRaisesRegex(ValueError, "Invalid final"):
            sleep_vrma_end_anchors(SLEEP, self.skeleton, 4)


if __name__ == "__main__":
    unittest.main()
