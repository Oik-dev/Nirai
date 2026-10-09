"""A world gesture must not animate the base pose's hips or legs."""
import json
import struct
import unittest

import numpy as np

from npz_to_vrma import UPPER_BODY
from vrma import glb


class UpperBodyTests(unittest.TestCase):
    def test_upper_body_tracks_do_not_contain_hips_or_legs(self):
        order = ["hips", "spine", "leftUpperArm", "rightUpperArm", "leftUpperLeg"]
        parent = {"hips": None, "spine": "hips", "leftUpperArm": "spine",
                  "rightUpperArm": "spine", "leftUpperLeg": "hips"}
        rest = {name: np.zeros(3) for name in order}
        identity = np.broadcast_to(np.eye(3), (3, 3, 3)).copy()
        local = {name: identity for name in order}
        hips = np.array([[0, 0, 0], [10, 0, 0], [0, 0, 0]], dtype=float)
        times = np.array([0, .5, 1], dtype=float)
        result = glb("stretch", order, parent, rest, local, hips, times, set(UPPER_BODY))
        self.assertEqual(result[:4], b"glTF")
        self.assertEqual(struct.unpack_from("<I", result, 8)[0], len(result))
        doc = json.loads(result[20:20 + struct.unpack_from("<I", result, 12)[0]])
        names = {doc["nodes"][channel["target"]["node"]]["name"]
                 for channel in doc["animations"][0]["channels"]}
        self.assertEqual(names, {"spine", "leftUpperArm", "rightUpperArm"})
        self.assertTrue(all(channel["target"]["path"] == "rotation"
                            for channel in doc["animations"][0]["channels"]))
        self.assertEqual(len(doc["animations"][0]["samplers"]), 3)


if __name__ == "__main__":
    unittest.main()
