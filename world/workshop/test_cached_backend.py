"""Verify CPU-only Kimodo wiring with fakes; neither Kimodo nor 8B weights load."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

from kimodo_backend import KimodoBackend
from preflight import local_motion_checkpoint, MODEL_NAME
from text_features import TextFeatures


class _Array:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


class CachedBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkpoints = self.root / "motion"
        self.motion = self.checkpoints / MODEL_NAME
        self.motion.mkdir(parents=True)
        (self.motion / "config.yaml").write_text("test: 1", encoding="utf-8")
        (self.motion / "weights.safetensors").write_bytes(b"fake")
        self.features = TextFeatures(self.root / "features")

    def test_cpu_only_does_not_instantiate_text_encoder_or_cuda(self):
        self.assertEqual(local_motion_checkpoint(self.checkpoints), self.motion)
        calls = []

        class Model:
            def __init__(self, features):
                self.text_encoder = features
                self.output_skeleton = types.SimpleNamespace(
                    name="somaskel77", bone_order_names=[f"joint{i}" for i in range(77)],
                    joint_parents=_Array([-1] + [0] * 76),
                    neutral_joints=_Array([[0, 0, 0]] * 77),
                )
                self.fps = 30

            def float(self):
                return self

            def eval(self):
                return self

            def parameters(self):
                return iter(())

        def load_model(name, *, device, text_encoder):
            calls.append((name, device, text_encoder))
            return Model(text_encoder)

        kimodo = types.ModuleType("kimodo")
        kimodo.__path__ = []  # importing kimodo.model.llm2vec must fail
        kimodo.load_model = load_model
        torch = types.ModuleType("torch")
        torch.float32 = "fp32"
        torch.set_num_threads = lambda n: calls.append(("threads", n))
        with (patch.dict(sys.modules, {"kimodo": kimodo, "torch": torch}),
              patch("preflight.available_ram_mib", return_value=20000),
              patch("preflight.offline_only") as offline):
            backend = KimodoBackend.load_cached(
                self.checkpoints, features=self.features, poc=self.root / "poc",
                hf_home=self.root / "hf")
        self.assertEqual(backend.fps, 30)
        self.assertEqual(backend.skeleton["joint_names"], [f"joint{i}" for i in range(77)])
        self.assertEqual(calls[1], ("kimodo-soma-rp-v1.1", "cpu", self.features))
        offline.assert_called_once_with(self.root / "poc", self.root / "hf", self.checkpoints)

    def test_reject_missing_checkpoint_low_ram_and_live_encoder_before_load(self):
        with patch("preflight.available_ram_mib", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "RAM"):
                KimodoBackend.load_cached(self.checkpoints, features=self.features,
                                          poc=self.root, hf_home=self.root)
        live = TextFeatures(self.root / "features", encoder=lambda text: text)
        with self.assertRaisesRegex(RuntimeError, "cached features"):
            KimodoBackend.load_cached(self.checkpoints, features=live,
                                      poc=self.root, hf_home=self.root)
        (self.motion / "weights.safetensors").unlink()
        with self.assertRaisesRegex(RuntimeError, "checkpoint"):
            KimodoBackend.load_cached(self.checkpoints, features=self.features,
                                      poc=self.root, hf_home=self.root)


if __name__ == "__main__":
    unittest.main()
