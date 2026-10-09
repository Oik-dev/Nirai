"""CPU-only continuity measurements for D0 source and generated poses.

Report alignment at the beginning and the end separately. This is not a
replacement for the VRM gate or for a visual decision: passing a numeric
comparison does not mean an animation is natural.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from reference_constraints import load_reference


def pose_gap(reference: dict[str, np.ndarray], reference_frame: int,
             candidate: dict[str, np.ndarray], candidate_frame: int,
             names: list[str]) -> dict:
    """Compare positions and SO(3) joint orientations at an existing frame."""
    if len(names) != 77:
        raise ValueError("Expected exactly 77 named joints")
    for source, frame in ((reference, reference_frame), (candidate, candidate_frame)):
        if type(frame) is not int or frame < 0 or frame >= len(source["root_positions"]):
            raise ValueError("Reference frame out of range")
    a = reference["local_rot_mats"][reference_frame]
    b = candidate["local_rot_mats"][candidate_frame]
    angles = np.rad2deg((Rotation.from_matrix(a).inv() * Rotation.from_matrix(b)).magnitude())
    delta = candidate["root_positions"][candidate_frame] - reference["root_positions"][reference_frame]
    worst = np.argsort(-angles, kind="stable")[:5]
    return {
        "root_xyz_m": [round(float(x), 4) for x in delta],
        "root_distance_m": round(float(np.linalg.norm(delta)), 4),
        "joint_median_deg": round(float(np.median(angles)), 3),
        "joint_p90_deg": round(float(np.percentile(angles, 90)), 3),
        "joint_max_deg": round(float(angles.max()), 3),
        "most_different": [{"joint": names[i], "degrees": round(float(angles[i]), 2)}
                           for i in worst],
    }


def compare(candidate_path: Path, reference_folder: Path,
            sleep_name: str = "lie_side_sleep") -> dict:
    if sleep_name not in ("lie_side_sleep", "lie_back_sleep"):
        raise ValueError("Unsupported sleep reference")
    seat_json = reference_folder / "sit_ground.json"
    seat = load_reference(reference_folder / "sit_ground.npz", seat_json)
    names = json.loads(seat_json.read_text(encoding="utf-8"))["skeleton"]["joint_names"]
    sleep = load_reference(reference_folder / (sleep_name + ".npz"),
                           reference_folder / (sleep_name + ".json"))
    candidate = load_reference(candidate_path, candidate_path.with_suffix(".json"))
    return {
        "candidate": candidate_path.stem,
        "sleep_reference": sleep_name,
        "start_vs_seat": pose_gap(seat, 60, candidate, 0, names),
        "end_vs_sleep": pose_gap(sleep, 0, candidate, len(candidate["root_positions"]) - 1, names),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("reference_folder", type=Path)
    parser.add_argument("candidate_paths", type=Path, nargs="+")
    parser.add_argument("--sleep", choices=("lie_side_sleep", "lie_back_sleep"),
                        default="lie_side_sleep")
    args = parser.parse_args()
    for path in args.candidate_paths:
        print(json.dumps(compare(path, args.reference_folder, args.sleep), ensure_ascii=False))
