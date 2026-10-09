"""Measure a generated SOMA77 pelvis trajectory before expensive VRM rendering.

Pure numerical ranking, not a substitute for the shared VRM gate or a visual
decision. No thresholds, motion corrections, or model loading live here.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def pelvis_metrics(rotations, *, fps: float = 30.0, target=None) -> dict[str, float]:
    """Use the first SOMA77 local joint (hips) and optional final pose target.

    Returns path/net degrees and peak angular speed. A nearly full-circle
    motion has a high ratio even when it ends at its starting orientation.
    """
    rotations = np.asarray(rotations)
    if (rotations.ndim != 4 or rotations.shape[1:] != (77, 3, 3)
            or len(rotations) < 2 or not np.isfinite(rotations).all()
            or not np.isfinite(fps) or fps <= 0):
        raise ValueError("Invalid SOMA77 rotation sequence")
    hips = Rotation.from_matrix(rotations[:, 0])
    steps = (hips[1:] * hips[:-1].inv()).magnitude()
    total = float(np.degrees(steps.sum()))
    net = float(np.degrees((hips[-1] * hips[0].inv()).magnitude()))
    ratio = total / net if net > 1e-6 else (1.0 if total < 1e-6 else float("inf"))
    output = {"path_deg": total, "net_deg": net, "path_to_net": ratio,
              "peak_deg_per_s": float(np.degrees(steps.max()) * fps)}
    if target is not None:
        target = np.asarray(target)
        if target.shape != (3, 3) or not np.isfinite(target).all():
            raise ValueError("Invalid final pelvis target")
        output["final_error_deg"] = float(np.degrees((hips[-1] * Rotation.from_matrix(target).inv()).magnitude()))
    return output


def review_order(results: list[dict]) -> list[dict]:
    """Order numbered trials for human inspection, without approving any.

    Prefer the correct sleeping endpoint, then less wasted turning and fewer
    abrupt rotations. The shared gate and VRM image inspection still decide.
    """
    return sorted(results, key=lambda row: (
        row["final_error_deg"],
        row["path_deg"] - row["net_deg"],
        row["peak_deg_per_s"],
        row["number"],
    ))
