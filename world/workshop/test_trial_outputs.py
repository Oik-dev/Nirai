"""One-trial, fake-only tests; no CPU/GPU model or resident is started."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from npz_to_vrma import BODY
from recline_trials import ReclineCandidate
from trial_outputs import generate_trial


class FakeBackend:
    fps = 30

    def __init__(self, broken=False):
        names = list(BODY.values()) + [f"Other{i}" for i in range(77 - len(BODY))]
        self.skeleton = {
            "joint_names": names,
            "parents": [-1] + [0] * 76,
            "neutral_joints_m": [[0, i * .01, 0] for i in range(77)],
            "fps": 30,
        }
        self.calls = []
        self.broken = broken

    def generate_arrays(self, text, seconds, seed, *, constraints):
        self.calls.append((text, seconds, seed, constraints))
        frames = seconds * self.fps
        rotations = np.broadcast_to(np.eye(3), (1, frames, 77, 3, 3)).copy()
        if self.broken:
            rotations[0, 0, 0, 0, 0] = np.nan
        root = np.zeros((1, frames, 3))
        root[..., 1] = .8
        return {"local_rot_mats": rotations, "global_rot_mats": rotations.copy(),
                "root_positions": root}


class TrialOutputTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = Path(self.dir.name) / "trial"
        self.secret = "PRIVATE-DESCRIPTION-DO-NOT-STORE"
        zeros = np.zeros((1, 77, 3), dtype=np.float32)
        self.candidate = ReclineCandidate(
            number=1, text_index=0, seconds=4, seed=17, frames=120,
            constraints=(
                {"type": "fullbody", "frame_indices": np.array([0]),
                 "local_joints_rot": zeros, "root_positions": np.zeros((1, 3))},
                {"type": "fullbody", "frame_indices": np.arange(115, 120),
                 "local_joints_rot": np.zeros((5, 77, 3), dtype=np.float32),
                 "root_positions": np.zeros((5, 3))},
            ),
        )

    def test_fake_motion_is_numbered_and_never_approved(self):
        fake = FakeBackend()
        result = generate_trial(fake, [self.secret], self.candidate, self.out)
        self.assertEqual((result["number"], result["seconds"], result["seed"]), (1, 4, 17))
        self.assertEqual(result["path_deg"], 0)
        self.assertEqual(fake.calls[0][:3], (self.secret, 4, 17))
        self.assertEqual(len(fake.calls[0][3]), 2)
        self.assertEqual({p.name for p in self.out.iterdir()},
                         {"candidate-001.npz", "candidate-001.vrma"})
        for path in self.out.iterdir():
            self.assertNotIn(self.secret.encode("utf-8"), path.read_bytes())
        with np.load(self.out / "candidate-001.npz", allow_pickle=False) as arrays:
            self.assertEqual(set(arrays.files),
                             {"local_rot_mats", "global_rot_mats", "root_positions"})
        with self.assertRaises(FileExistsError):
            generate_trial(fake, [self.secret], self.candidate, self.out)
        self.assertEqual(len(fake.calls), 1)

    def test_bad_generation_never_persists_partial_data(self):
        with self.assertRaisesRegex(ValueError, "numeric"):
            generate_trial(FakeBackend(broken=True), [self.secret], self.candidate, self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
