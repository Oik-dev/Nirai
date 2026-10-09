"""Fake-only tests. These never import Kimodo or access local model weights."""
from __future__ import annotations

import http.client
import json
import struct
import threading
import tempfile
import unittest
from pathlib import Path

import numpy as np

from generator import MotionHTTPServer, motion_request, valid_glb
from npz_to_vrma import BODY, convert, convert_arrays

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


if __name__ == "__main__":
    unittest.main()
