"""Reusable motion-clip endpoint fitting and quaternion regression tests."""
import unittest
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from motion_seams import fit_endpoints, shortest_slerp
from vrma import read_tracks


class MotionSeamsTests(unittest.TestCase):
    def test_actual_seat_recline_sleep_seams(self):
        motions = Path(__file__).resolve().parent.parent / "window" / "assets" / "motions"
        seat, seat_root = read_tracks(motions / "座る.vrma")
        recline, recline_root = read_tracks(motions / "寝転ぶ.vrma")
        sleep, sleep_root = read_tracks(motions / "眠る.vrma")
        for a, a_root, a_index, b, b_root, b_index in (
                (seat, seat_root, 0, recline, recline_root, 0),
                (recline, recline_root, -1, sleep, sleep_root, 0)):
            self.assertLess(np.linalg.norm(a_root[a_index] - b_root[b_index]), .001)
            for bone in recline:
                difference = (Rotation.from_quat(a[bone][a_index]).inv()
                              * Rotation.from_quat(b[bone][b_index])).magnitude()
                self.assertLess(np.rad2deg(difference), .5, bone)

    def test_endpoints_equal_targets_and_middle_unchanged(self):
        frames = 61
        original = {"hips": Rotation.from_euler("z", np.linspace(15, 35, frames)[:, None], degrees=True).as_quat(),
                    "head": Rotation.from_euler("x", np.linspace(5, -10, frames)[:, None], degrees=True).as_quat()}
        hips = np.stack([np.linspace(0, 1, frames), np.ones(frames), np.zeros(frames)], axis=1)
        start = {k: Rotation.from_euler("x", 20, degrees=True).as_quat()[None] for k in original}
        end = {k: Rotation.from_euler("y", -45, degrees=True).as_quat()[None] for k in original}
        start_root, end_root = np.array([[.5, 0, .5]]), np.array([[1.5, .2, -.5]])
        output, root = fit_endpoints(original, hips, start, start_root, end, end_root, frames=12)
        for bone in original:
            self.assertLess(float((Rotation.from_quat(output[bone][0]).inv()
                * Rotation.from_quat(start[bone][0])).magnitude()), 1e-9)
            self.assertLess(float((Rotation.from_quat(output[bone][-1]).inv()
                * Rotation.from_quat(end[bone][0])).magnitude()), 1e-9)
            np.testing.assert_array_equal(output[bone][13:48], original[bone][13:48])
        np.testing.assert_array_equal(root[0], start_root[0])
        np.testing.assert_array_equal(root[-1], end_root[0])
        np.testing.assert_array_equal(root[13:48], hips[13:48])

    def test_opposite_quaternion_sign_uses_shortest_rotation(self):
        start = Rotation.from_euler("y", 0, degrees=True).as_quat()
        target = -Rotation.from_euler("y", 20, degrees=True).as_quat()
        mid = shortest_slerp(start, target, .5)
        angle = (Rotation.from_quat(mid) * Rotation.from_quat(start).inv()).magnitude()
        self.assertAlmostEqual(np.rad2deg(angle), 10, places=6)
        self.assertAlmostEqual(np.linalg.norm(mid), 1)

    def test_reject_overlap_and_missing_bone(self):
        r = {"hips": np.tile([0, 0, 0, 1], (31, 1))}
        hips = np.zeros((31, 3))
        with self.assertRaisesRegex(ValueError, "overlap"):
            fit_endpoints(r, hips, r, hips, r, hips, frames=16)
        with self.assertRaisesRegex(ValueError, "Missing"):
            fit_endpoints(r, hips, {}, hips, r, hips)


if __name__ == "__main__":
    unittest.main()
