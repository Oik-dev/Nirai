"""Prepare D0 reclining experiments from cached text features, without loading models.

The approved motion run uses four locally authored descriptions, two durations
and three seeds. This module prepares private SOMA77 constraints and keeps the
descriptions out of candidate metadata, logs and filenames.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from reference_constraints import load_reference, reference_pose, sleep_vrma_end_anchors
from text_features import TextFeatures


@dataclass(frozen=True)
class ReclineCandidate:
    number: int
    text_index: int
    seconds: int
    seed: int
    frames: int
    constraints: tuple[dict, ...]


def prepare_recline_trials(texts, seeds, *, features: TextFeatures,
                           seat_npz: Path, seat_json: Path, sleep_vrma: Path,
                           skeleton: dict, fps: float = 30.0) -> list[ReclineCandidate]:
    """Fail before generation when the text cache or reference poses are invalid.

    Each result references a text only by its position in the caller's memory.
    Real inference, output files and model lifecycle belong to the run script.
    """
    if (not isinstance(texts, (list, tuple)) or len(texts) != 4
            or not all(isinstance(t, str) and 1 <= len(t) <= 200 for t in texts)
            or len(set(texts)) != 4):
        raise ValueError("Expected four distinct nonempty descriptions")
    if (not isinstance(seeds, (list, tuple)) or len(seeds) != 3
            or any(type(seed) is not int for seed in seeds)
            or len(set(seeds)) != 3 or any(seed < 0 or seed >= 2**32 for seed in seeds)):
        raise ValueError("Expected three distinct unsigned 32-bit seeds")
    if not np.isfinite(fps) or fps <= 0 or any(int(seconds * fps) != seconds * fps for seconds in (4, 6)):
        raise ValueError("Frame rate must give integral frame counts")
    if not isinstance(features, TextFeatures):
        raise ValueError("The approved offline cache is required")
    for sentence in texts:
        if features._read(sentence) is None:
            raise ValueError("A text feature is not cached; generation is prohibited")

    reference_names = json.loads(Path(seat_json).read_text(encoding="utf-8"))["skeleton"]["joint_names"]
    if reference_names != skeleton.get("joint_names"):
        raise ValueError("Seated and output skeleton orders differ")
    seat = load_reference(Path(seat_npz), Path(seat_json))
    start = reference_pose(seat, 60, 0)
    root_xz = start["root_positions"][0, [0, 2]]
    endpoints = {
        seconds: sleep_vrma_end_anchors(Path(sleep_vrma), skeleton, root_xz, int(seconds * fps))
        for seconds in (4, 6)
    }
    output = []
    for text_index in range(len(texts)):
        for seconds in (4, 6):
            frames = int(seconds * fps)
            for seed in seeds:
                output.append(ReclineCandidate(len(output) + 1, text_index, seconds,
                                               seed, frames, (start, *endpoints[seconds])))
    return output
