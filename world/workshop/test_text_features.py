"""Disposable-cache tests; never load text weights or access a resident."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

from text_features import TextFeatures, FEATURE_DIM


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return np.full((len(texts), 1, FEATURE_DIM), 0.25, dtype=np.float32), [1] * len(texts)


class TextFeaturesTest(unittest.TestCase):
    def test_extract_once_and_reuse_without_encoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            encoder = FakeEncoder()
            warm = TextFeatures(Path(tmp), encoder)
            texts = ["A person slowly lies down.", "A person slowly sits up."]
            values, lengths = warm(texts)
            self.assertEqual(tuple(values.shape), (2, 1, FEATURE_DIM))
            self.assertEqual(lengths, [1, 1])
            self.assertEqual(encoder.calls, [[texts[0]], [texts[1]]])
            self.assertEqual(warm(texts)[1], [1, 1])
            self.assertEqual(len(encoder.calls), 2)
            cold = TextFeatures(Path(tmp))
            feature, count = cold(texts[0])
            self.assertEqual(tuple(feature.shape), (1, FEATURE_DIM))
            self.assertEqual(count, 1)
            self.assertTrue(np.allclose(feature.numpy(), values[0].numpy()))
            path = Path(tmp) / (hashlib.sha256(texts[0].encode()).hexdigest()[:16] + ".npz")
            with np.load(path, allow_pickle=False) as saved:
                self.assertEqual(str(saved["text"].item()), texts[0])
                self.assertEqual(saved["feat"].shape, (1, FEATURE_DIM))

    def test_uncached_cannot_start_text_model_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "prior approval"):
                TextFeatures(Path(tmp))(["new text"])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_wrong_sentence_or_corrupt_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TextFeatures(Path(tmp))
            sentence = "A person slowly lies down."
            path = store._path(sentence)
            np.savez(path, text="Different text", feat=np.zeros((1, FEATURE_DIM), dtype=np.float32))
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                store([sentence])
            np.savez(path, text=sentence, feat=np.full((1, FEATURE_DIM), np.nan, dtype=np.float32))
            with self.assertRaisesRegex(ValueError, "Invalid cached feature"):
                store([sentence])


if __name__ == "__main__":
    unittest.main()
