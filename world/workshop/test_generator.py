"""Fake-only tests. These never import Kimodo or access local model weights."""
from __future__ import annotations

import http.client
import json
import os
import socket
import struct
import sys
import threading
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from generator import MotionHTTPServer, motion_request, valid_glb
from npz_to_vrma import BODY, convert, convert_arrays
from kimodo_backend import KimodoBackend
from constraints import prepare_constraints, restore_origin
from preflight import (
    MODEL_NAME, TEXT_REVISIONS, MIN_AVAILABLE_RAM_MIB, MIN_FREE_VRAM_MIB,
    local_models, require_capacity, offline_only,
)

GLB = b"glTF" + struct.pack("<II", 2, 12)


class FakeGenerator:
    def __init__(self, wait=None, entered=None, broken=False):
        self.calls = []
        self.wait = wait
        self.entered = entered
        self.broken = broken

    def generate(self, text, seconds, seed):
        self.calls.append((text, seconds, seed))
        if self.entered:
            self.entered.set()
        if self.wait:
            if not self.wait.wait(5):
                raise RuntimeError("Timeout in fake")
        if self.broken:
            raise RuntimeError("Private prompt: " + text)
        return GLB


class TestMotionHTTP(unittest.TestCase):
    def setUp(self):
        self.server = MotionHTTPServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, data=None, content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=6)
        try:
            headers = {"Content-Type": content_type} if data is not None else {}
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def post(self, obj):
        return self.request("POST", "/motion", json.dumps(obj).encode("utf-8"))

    def test_local_only_and_readiness(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.request("GET", "/health")[0], 503)
        self.assertEqual(self.post({"text": "wave", "seconds": 3})[0], 503)
        self.server.install(FakeGenerator())
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_glb_result_and_seed(self):
        fake = FakeGenerator()
        self.server.install(fake)
        status, headers, data = self.post({"text": "a quiet stretch", "seconds": 2.5, "seed": 42})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "model/gltf-binary")
        self.assertEqual(headers["X-Motion-Seed"], "42")
        self.assertEqual(data, GLB)
        self.assertEqual(fake.calls, [("a quiet stretch", 2.5, 42)])
        status, headers, data = self.post({"text": "rest", "seconds": 1})
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["X-Motion-Seed"]), fake.calls[-1][2])
        self.assertTrue(0 <= fake.calls[-1][2] <= 2**31 - 1)

    def test_invalid_inputs(self):
        self.server.install(FakeGenerator())
        for wrong in (
            {}, {"text": "", "seconds": 1}, {"text": "x" * 201, "seconds": 1},
            {"text": "ok", "seconds": 0}, {"text": "ok", "seconds": 11},
            {"text": "ok", "seconds": True}, {"text": "ok", "seconds": float("inf")},
            {"text": "ok", "seconds": 1, "seed": True},
            {"text": "ok", "seconds": 1, "extra": "no"},
        ):
            self.assertEqual(self.post(wrong)[0], 400, wrong)
        self.assertEqual(self.request("POST", "/motion", b"{not json")[0], 400)
        self.assertEqual(self.request("POST", "/motion", b"a" * 2049)[0], 400)
        self.assertEqual(self.request("POST", "/motion", b"{}", "text/plain")[0], 400)
        self.assertEqual(self.request("GET", "/not-found")[0], 404)

    def test_failed_generator_hides_submitted_text(self):
        self.server.install(FakeGenerator(broken=True))
        status, headers, data = self.post({"text": "PRIVATE-STRING-DO-NOT-LOG", "seconds": 1})
        self.assertEqual(status, 500)
        self.assertNotIn(b"PRIVATE-STRING", data)

    def test_concurrent_generation_is_rejected(self):
        gate, entered = threading.Event(), threading.Event()
        fake = FakeGenerator(wait=gate, entered=entered)
        self.server.install(fake)
        first = []
        thread = threading.Thread(target=lambda: first.append(self.post({"text": "one", "seconds": 1})[0]))
        thread.start()
        self.assertTrue(entered.wait(3))
        self.assertEqual(self.post({"text": "two", "seconds": 1})[0], 503)
        gate.set()
        thread.join(timeout=3)
        self.assertEqual(first, [200])
        self.assertEqual(len(fake.calls), 1)


class TestMemoryConversion(unittest.TestCase):
    def test_in_memory_and_cli_conversion_share_identical_result(self):
        names = list(BODY.values())
        count = len(names)
        skeleton = {
            "joint_names": names,
            "parents": [-1] + [i - 1 for i in range(1, count)],
            "neutral_joints_m": [[0, i * .01, 0] for i in range(count)],
            "fps": 30,
        }
        matrices = np.broadcast_to(np.eye(3), (3, count, 3, 3)).copy()
        positions = np.array([[0, .8, 0], [0, .79, 0], [0, .8, 0]])
        arrays = {"global_rot_mats": matrices, "root_positions": positions}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "clip.npz"
            data = Path(directory) / "clip.json"
            np.savez(source, **arrays)
            data.write_text(json.dumps({"skeleton": skeleton}), encoding="utf-8")
            original, info = convert(source, data)
            same, memory_info = convert_arrays(arrays, skeleton, label="clip")
        self.assertEqual(original, same)
        self.assertEqual(info, memory_info)
        self.assertTrue(valid_glb(same))


class TestPreflight(unittest.TestCase):
    def test_local_model_files_and_missing_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            poc, hf, checkpoint = root / "poc", root / "huggingface", root / "motion"
            source = poc / "vendor" / "kimodo" / "kimodo" / "__init__.py"
            source.parent.mkdir(parents=True)
            source.touch()
            for key, (repo, rev) in TEXT_REVISIONS.items():
                model = hf / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots" / rev
                model.mkdir(parents=True)
                if key == "base":
                    (model / "config.json").write_text("{}", encoding="utf-8")
                    (model / "model.safetensors").touch()
                else:
                    (model / "adapter_config.json").write_text("{}", encoding="utf-8")
                    (model / "adapter_model.safetensors").touch()
            motion = checkpoint / MODEL_NAME
            motion.mkdir(parents=True)
            (motion / "config.yaml").write_text("test: 1", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Kimodo checkpoint"):
                local_models(poc, hf, checkpoint)
            (motion / "model.safetensors").touch()
            paths = local_models(poc, hf, checkpoint)
            self.assertEqual(paths["motion"], motion)
            self.assertEqual(set(paths), {"base", "mntp", "supervised", "motion"})
            (motion / "model.safetensors").unlink()
            self.assertFalse(motion.joinpath("model.safetensors").exists())
            with self.assertRaisesRegex(RuntimeError, "Kimodo checkpoint"):
                local_models(poc, hf, checkpoint)

    def test_resource_capacity_fails_closed(self):
        require_capacity(MIN_AVAILABLE_RAM_MIB, MIN_FREE_VRAM_MIB)
        with self.assertRaisesRegex(RuntimeError, "RAM"):
            require_capacity(MIN_AVAILABLE_RAM_MIB - 1, MIN_FREE_VRAM_MIB)
        with self.assertRaisesRegex(RuntimeError, "GPU"):
            require_capacity(MIN_AVAILABLE_RAM_MIB, MIN_FREE_VRAM_MIB - 1)

    def test_offline_mode_blocks_connections_and_sets_local_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ):
            base = Path(tmp)
            original_connect = socket.socket.connect
            original_connect_ex = socket.socket.connect_ex
            original_create = socket.create_connection
            try:
                offline_only(base / "poc", base / "hf", base / "motion")
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")
                self.assertEqual(os.environ["CHECKPOINT_DIR"], str(base / "motion"))
                with socket.socket() as client:
                    with self.assertRaisesRegex(RuntimeError, "Outbound"):
                        client.connect(("127.0.0.1", 1))
                    with self.assertRaisesRegex(RuntimeError, "Outbound"):
                        client.connect_ex(("127.0.0.1", 1))
                with self.assertRaisesRegex(RuntimeError, "Outbound"):
                    socket.create_connection(("127.0.0.1", 1))
            finally:
                socket.socket.connect = original_connect
                socket.socket.connect_ex = original_connect_ex
                socket.create_connection = original_create


class TestKimodoAdapter(unittest.TestCase):
    def test_fake_model_receives_plain_text_and_converts_without_files(self):
        frames = 30
        count = 77
        matrices = np.broadcast_to(np.eye(3), (1, frames, count, 3, 3)).copy()
        root = np.zeros((1, frames, 3))
        root[..., 1] = .8
        names = list(BODY.values()) + [f"Extra{i}" for i in range(count - len(BODY))]
        skeleton = {
            "joint_names": names,
            "parents": [-1] + [0] * (count - 1),
            "neutral_joints_m": [[0, i * .01, 0] for i in range(count)],
            "fps": 30,
        }
        calls, seeds = [], []

        class FakeTorch:
            @staticmethod
            def inference_mode():
                from contextlib import nullcontext
                return nullcontext()

        class Model:
            def __call__(self, text, count_frames, **kwargs):
                calls.append((text, count_frames, kwargs))
                return {
                    "posed_joints": np.zeros((1, frames, count, 3)),
                    "global_rot_mats": matrices,
                    "root_positions": root,
                }

        tools = types.ModuleType("kimodo.tools")
        tools.seed_everything = seeds.append
        pkg = types.ModuleType("kimodo")
        pkg.__path__ = []
        with patch.dict(sys.modules, {"kimodo": pkg, "kimodo.tools": tools}):
            backend = KimodoBackend(Model(), FakeTorch(), skeleton, 30, 100)
            clip = backend.generate("Stretch both arms", 1, 42)

        self.assertTrue(valid_glb(clip))
        self.assertEqual(seeds, [42])
        self.assertEqual(calls[0][0], "Stretch both arms")
        self.assertEqual(calls[0][1], 30)
        self.assertEqual(calls[0][2]["constraint_lst"], [])
        self.assertFalse(calls[0][2]["post_processing"])


class TestPrivateConstraints(unittest.TestCase):
    def test_origin_heading_and_frame_types_without_loading_the_model(self):
        rows = []

        def load_constraints(items, skeleton, *, device):
            rows.extend(items)
            self.assertEqual(skeleton, "soma30")
            self.assertEqual(device, "cuda:0")
            return [types.SimpleNamespace(global_joints_positions=np.zeros((len(r["frame_indices"]), 30, 3)))
                    for r in items]

        feature = types.ModuleType("kimodo.motion_rep.feature_utils")
        feature.compute_heading_angle = lambda points, skeleton: np.full((1, len(points[0])), .25)
        constraint_module = types.ModuleType("kimodo.constraints")
        constraint_module.load_constraints_lst = load_constraints
        with patch.dict(sys.modules, {
            "kimodo": types.ModuleType("kimodo"), "kimodo.constraints": constraint_module,
            "kimodo.motion_rep": types.ModuleType("kimodo.motion_rep"),
            "kimodo.motion_rep.feature_utils": feature,
        }):
            source = {
                "type": "fullbody", "frame_indices": np.array([0, 4], dtype=np.int64),
                "local_joints_rot": np.zeros((2, 77, 3)),
                "root_positions": np.array([[2, 1, -3], [2.1, 1, -2.9]]),
            }
            model = types.SimpleNamespace(skeleton="soma30", device="cuda:0")
            made, angle, offset = prepare_constraints([source], model)
            self.assertEqual(len(made), 1)
            self.assertEqual(angle.shape, (1,))
            np.testing.assert_array_equal(offset, [2, -3])
            self.assertEqual(rows[0]["frame_indices"].dtype, np.int64)
            np.testing.assert_allclose(rows[0]["root_positions"][0], [0, 1, 0])
            np.testing.assert_allclose(rows[0]["smooth_root_2d"][1], [.1, .1], atol=1e-6)
            np.testing.assert_array_equal(source["root_positions"][0], [2, 1, -3])

            generated = {
                "root_positions": np.zeros((1, 5, 3)),
                "posed_joints": np.zeros((1, 5, 77, 3)),
                "smooth_root_pos": np.zeros((1, 5, 2)),
            }
            restore_origin(generated, offset)
            np.testing.assert_array_equal(generated["root_positions"][0, 0], [2, 0, -3])
            np.testing.assert_array_equal(generated["posed_joints"][0, 0, 0], [2, 0, -3])
            np.testing.assert_array_equal(generated["smooth_root_pos"][0, 0], [2, -3])

            for invalid in (
                {**source, "frame_indices": np.array([0.0, 4.0])},
                {**source, "local_joints_rot": np.zeros((2, 30, 3))},
                {**source, "root_positions": np.array([[np.nan, 0, 0], [1, 0, 0]])},
                {**source, "type": "left-hand"},
            ):
                if invalid["type"] == "left-hand":
                    # A hand alone cannot establish a body-wide reference pose.
                    with self.assertRaisesRegex(ValueError, "fullbody origin"):
                        prepare_constraints([invalid], model)
                else:
                    with self.assertRaises(ValueError):
                        prepare_constraints([invalid], model)

    def test_private_constraints_reach_the_model_and_restore_generated_origin(self):
        calls = []

        class FakeTorch:
            @staticmethod
            def inference_mode():
                from contextlib import nullcontext
                return nullcontext()

        class Model:
            def __call__(self, text, frames, **kwargs):
                calls.append((frames, kwargs))
                return {
                    "posed_joints": np.zeros((1, frames, 77, 3)),
                    "global_rot_mats": np.broadcast_to(np.eye(3), (1, frames, 77, 3, 3)).copy(),
                    "root_positions": np.zeros((1, frames, 3)),
                    "smooth_root_pos": np.zeros((1, frames, 2)),
                }

        tools = types.ModuleType("kimodo.tools")
        tools.seed_everything = lambda seed: None
        sample = {"frame_indices": [0, 2]}
        with patch.dict(sys.modules, {"kimodo": types.ModuleType("kimodo"), "kimodo.tools": tools}):
            with patch("constraints.prepare_constraints", return_value=(["prepared"], np.array([.25]), np.array([2., -3.]))):
                backend = KimodoBackend(Model(), FakeTorch(), {}, 30, 10)
                result = backend.generate_arrays("a private sample", 1, 5, constraints=[sample])
                np.testing.assert_array_equal(result["root_positions"][0, 0], [2, 0, -3])
                np.testing.assert_array_equal(result["smooth_root_pos"][0, 0], [2, -3])
                self.assertEqual(calls[0][1]["constraint_lst"], ["prepared"])
                self.assertEqual(calls[0][1]["first_heading_angle"], np.array([.25]))
                self.assertFalse(calls[0][1]["post_processing"])
                with self.assertRaisesRegex(ValueError, "outside"):
                    backend.generate_arrays("invalid", 1, 6, constraints=[{"frame_indices": [30]}])
                self.assertEqual(len(calls), 1, "Invalid frame must fail before model inference")


if __name__ == "__main__":
    unittest.main()
