"""Build *private* Kimodo pose constraints from existing reference NPZ files.

No model, GPU, network, or resident data is loaded. The returned dictionaries
are passed only to KimodoBackend.generate_arrays(..., constraints=...), never
to the public /motion HTTP route. Frames are target timestamps, while source
frames select existing SOMA77 motion poses.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


JOINT_COUNT = 77


def load_reference(npz_path: Path, json_path: Path) -> dict[str, np.ndarray]:
    """Validate a local SOMA77 sample without modifying any source files."""
    with np.load(npz_path, allow_pickle=False) as source:
        keys = ("local_rot_mats", "root_positions", "smooth_root_pos")
        if any(key not in source for key in keys):
            raise ValueError("Reference motion has missing arrays")
        arrays = {key: np.asarray(source[key], dtype=np.float64).copy() for key in keys}
    metadata = json.loads(json_path.read_text(encoding="utf-8"))
    skeleton = metadata.get("skeleton", {})
    count = len(skeleton.get("joint_names", ()))
    length = len(arrays["local_rot_mats"])
    if (count != JOINT_COUNT or not length or
            arrays["local_rot_mats"].shape != (length, JOINT_COUNT, 3, 3) or
            arrays["root_positions"].shape != (length, 3) or
            arrays["smooth_root_pos"].shape != (length, 3) or
            not all(np.isfinite(array).all() for array in arrays.values())):
        raise ValueError("Reference motion is not a finite SOMA77 sequence")
    matrices = arrays["local_rot_mats"]
    identity = np.eye(3)
    if (not np.allclose(matrices @ np.swapaxes(matrices, -2, -1), identity, atol=2e-3)
            or not np.allclose(np.linalg.det(matrices), 1, atol=2e-3)):
        raise ValueError("Reference rotations are not valid rotation matrices")
    return arrays


def reference_pose(source: dict[str, np.ndarray], source_frame: int,
                   target_frame: int, *, kind: str = "fullbody") -> dict:
    """Constrain a target frame to the orientation and root of a source pose.

    As with Kimodo's own benchmark builder, rotation matrices are converted
    to axis-angle vectors before passing to load_constraints_lst.
    """
    count = len(source["local_rot_mats"])
    if (type(source_frame) is not int or source_frame < 0 or source_frame >= count or
            type(target_frame) is not int or target_frame < 0):
        raise ValueError("Source and target frames must be in range")
    rotations = source["local_rot_mats"][source_frame]
    return {
        "type": kind,
        "frame_indices": np.array([target_frame], dtype=np.int64),
        "local_joints_rot": Rotation.from_matrix(rotations).as_rotvec().astype(np.float32)[None],
        "root_positions": source["root_positions"][source_frame].astype(np.float32)[None].copy(),
        "smooth_root_2d": source["smooth_root_pos"][source_frame, [0, 2]].astype(np.float32)[None].copy(),
    }


def standing_anchors(source: dict[str, np.ndarray], frames: int) -> list[dict]:
    """Start and finish at the same standing frame from sit_ground.

    Frame zero is the standing entry used by the already checked sit-to-ground
    clip. Two independent full-body anchors pin all joints and root XZ, rather
    than constraining only the upper body or asking the model to guess a stop.
    """
    if type(frames) is not int or frames < 2:
        raise ValueError("Standing motion requires at least two frames")
    return [reference_pose(source, 0, 0), reference_pose(source, 0, frames - 1)]


def reclining_anchors(folder: Path, frames: int, *, end_as_sleep: bool = True) -> list[dict]:
    """Anchor reclining to the original seated pose (source frame 60).

    Optional final anchor matches the old sleeping loop's starting pose. This
    is the fallback route when a newly generated sleep loop cannot pass gate.
    Both anchors are source poses; no generated movement is invented here.
    """
    if type(frames) is not int or frames < 2:
        raise ValueError("Reclining duration must contain at least two frames")
    seat = load_reference(folder / "sit_ground.npz", folder / "sit_ground.json")
    rows = [reference_pose(seat, 60, 0)]
    if end_as_sleep:
        sleep = load_reference(folder / "lie_side_sleep.npz", folder / "lie_side_sleep.json")
        rows.append(reference_pose(sleep, 0, frames - 1))
    return rows


def sleep_vrma_end_anchors(sleep_vrma: Path, skeleton: dict,
                           frames: int, *, count: int = 5) -> list[dict]:
    """End a recline exactly at the window's already-quiet sleep loop.

    Use the loop's final frames (including the frame matching its beginning).
    The canonical VRMA already contains its yaw and XZ position. No private prompt,
    model, avatar, or resident data is touched.
    """
    from npz_to_vrma import animation_to_soma
    from vrma import read_tracks

    rotations, hips = read_tracks(sleep_vrma)
    if (type(frames) is not int or type(count) is not int
            or count < 1 or frames < count or count > len(hips)):
        raise ValueError("Invalid final anchor frames")
    soma = animation_to_soma(rotations, hips, skeleton)
    indices = np.arange(len(hips) - count, len(hips))
    targets = np.arange(frames - count, frames, dtype=np.int64)
    return [{"type": "fullbody", "frame_indices": targets,
             "local_joints_rot": Rotation.from_matrix(soma["local_rot_mats"][indices].reshape(-1, 3, 3))
                 .as_rotvec().reshape(count, JOINT_COUNT, 3).astype(np.float32),
             "root_positions": soma["root_positions"][indices].astype(np.float32)}]
