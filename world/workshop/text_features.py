"""Offline, reusable text conditioning for D0 motion experiments.

Only the approved extraction run may supply an encoder. CPU trials load exact
cached sentences and fail if a sentence is absent; they never load an 8B model.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile

import numpy as np


FEATURES_DIR = Path("D:/Products/AI-Models/Motion/D0/text-features")
FEATURE_DIM = 4096


class TextFeatures:
    llm_dim = FEATURE_DIM

    def __init__(self, folder: Path = FEATURES_DIR, encoder=None):
        self.folder = Path(folder)
        self.encoder = encoder

    def _path(self, sentence: str) -> Path:
        return self.folder / (hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:16] + ".npz")

    def _read(self, sentence: str) -> np.ndarray | None:
        path = self._path(sentence)
        try:
            with np.load(path, allow_pickle=False) as stored:
                if set(stored.files) != {"text", "feat"} or stored["text"].shape != ():
                    raise ValueError("Invalid cached feature")
                if str(stored["text"].item()) != sentence:
                    raise ValueError("Cached text does not match its fingerprint")
                vector = np.asarray(stored["feat"])
                if vector.shape != (1, FEATURE_DIM) or vector.dtype != np.float32 or not np.isfinite(vector).all():
                    raise ValueError("Invalid cached feature")
                return vector.copy()
        except FileNotFoundError:
            return None

    def _extract(self, sentence: str) -> np.ndarray:
        if self.encoder is None:
            raise RuntimeError("Text feature is not cached; extraction requires prior approval")
        encoded, lengths = self.encoder([sentence])
        if hasattr(encoded, "detach"):
            encoded = encoded.detach().float().cpu().numpy()
        feature = np.asarray(encoded, dtype=np.float32)
        if feature.shape != (1, 1, FEATURE_DIM) or lengths != [1] or not np.isfinite(feature).all():
            raise ValueError("Text encoder returned invalid features")
        vector = feature[0]
        self.folder.mkdir(parents=True, exist_ok=True)
        # Atomic replacement: interrupted extraction must not leave half a feature.
        with tempfile.NamedTemporaryFile(dir=self.folder, suffix=".npz", delete=False) as tmp:
            pending = Path(tmp.name)
            try:
                np.savez_compressed(tmp, text=sentence, feat=vector)
            except BaseException:
                pending.unlink(missing_ok=True)
                raise
        pending.replace(self._path(sentence))
        return vector

    def __call__(self, texts):
        import torch

        single = isinstance(texts, str)
        sentences = [texts] if single else list(texts)
        if not sentences or not all(isinstance(s, str) and s for s in sentences):
            raise ValueError("Invalid text input")
        features = []
        for sentence in sentences:
            vector = self._read(sentence)
            features.append(vector if vector is not None else self._extract(sentence))
        result = torch.from_numpy(np.stack(features))  # [batch, 1, 4096] on CPU
        return (result[0], 1) if single else (result, [1] * len(sentences))
