"""Elbow limiting must not introduce a discontinuity as the angle wraps."""
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from npz_to_vrma import clamp_elbow_rotation


class ElbowRotationTests(unittest.TestCase):
    def test_wrap_through_positive_180_degrees_on_both_elbows(self):
        for left, direction in ((False, 1), (True, -1)):
            with self.subTest(left=left):
                source = Rotation.from_euler('y', (np.array([179., 181.]) * direction)[:, None], degrees=True).as_matrix()
                changed = clamp_elbow_rotation(source, left=left)
                relative = changed[0].T @ changed[1]
                jump = np.rad2deg(Rotation.from_matrix(relative).magnitude())
                self.assertLess(jump, 2., 'the clamp must not disappear at the atan2 boundary')
                target = Rotation.from_euler('y', 153. * direction, degrees=True).as_matrix()
                for frame in changed:
                    np.testing.assert_allclose(frame, target, atol=1e-5)

    def test_normal_bend_is_unchanged(self):
        for left, direction in ((False, 1), (True, -1)):
            source = Rotation.from_euler('y', (np.array([45., 90., 120.]) * direction)[:, None], degrees=True).as_matrix()
            np.testing.assert_allclose(clamp_elbow_rotation(source, left=left), source, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
