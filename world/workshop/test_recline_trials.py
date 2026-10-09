"""CPU-only trial planning tests. No Kimodo, Serina or 8B encoder is loaded."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from recline_trials import prepare_recline_trials
from text_features import TextFeatures, FEATURE_DIM


WORLD = Path(__file__).resolve().parent.parent
SLEEP = WORLD / "window" / "assets" / "motions" / "眠る.vrma"
REFERENCE = Path("D:/Products/AI-Models/Motion/D0/kimodo")


class ReclineTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.texts = [f"local trial description {index}" for index in range(4)]
        self.features = TextFeatures(self.folder)
        for sentence in self.texts:
            np.savez(self.features._path(sentence), text=sentence,
                     feat=np.zeros((1, FEATURE_DIM), dtype=np.float32))
        self.skeleton = json.loads((REFERENCE / "sit_ground.json").read_text(encoding="utf-8"))["skeleton"]

    def plan(self, texts=None, seeds=(101, 201, 301), skeleton=None):
        return prepare_recline_trials(self.texts if texts is None else texts, seeds,
                                      features=self.features, seat_npz=REFERENCE / "sit_ground.npz",
                                      seat_json=REFERENCE / "sit_ground.json", sleep_vrma=SLEEP,
                                      skeleton=self.skeleton if skeleton is None else skeleton)

    def test_twenty_four_cached_cpu_jobs_have_continuous_join_anchors(self):
        jobs = self.plan()
        self.assertEqual(len(jobs), 24)
        self.assertEqual([j.number for j in jobs], list(range(1, 25)))
        self.assertEqual({(j.text_index, j.seconds, j.seed) for j in jobs},
                         {(i, s, seed) for i in range(4) for s in (4, 6) for seed in (101, 201, 301)})
        for job in jobs:
            self.assertEqual(job.frames, 30 * job.seconds)
            np.testing.assert_array_equal(job.constraints[0]["frame_indices"], [0])
            np.testing.assert_array_equal(job.constraints[1]["frame_indices"],
                                          np.arange(job.frames - 5, job.frames))
            self.assertEqual(job.constraints[1]["local_joints_rot"].shape, (5, 77, 3))
            self.assertEqual(job.constraints[1]["root_positions"].shape, (5, 3))
            self.assertFalse(hasattr(job, "text"))

    def test_missing_features_stop_all_jobs_before_model_start(self):
        self.features._path(self.texts[0]).unlink()
        with self.assertRaisesRegex(ValueError, "not cached"):
            self.plan()
        self.assertEqual(set(self.folder.glob("*.npz")),
                         {self.features._path(s) for s in self.texts[1:]})

    def test_bad_text_seeds_and_mismatched_skeleton_are_rejected(self):
        for texts in (self.texts[:3], self.texts[:3] + [self.texts[0]],
                      self.texts[:3] + [""], self.texts[:3] + ["X" * 201]):
            with self.assertRaises(ValueError):
                self.plan(texts=texts)
        for seeds in ((1, 1, 2), (1, 2), (1, 2, True), (-1, 2, 3)):
            with self.assertRaises(ValueError):
                self.plan(seeds=seeds)
        wrong = dict(self.skeleton)
        wrong["joint_names"] = list(reversed(wrong["joint_names"]))
        with self.assertRaisesRegex(ValueError, "orders differ"):
            self.plan(skeleton=wrong)


if __name__ == "__main__":
    unittest.main()
