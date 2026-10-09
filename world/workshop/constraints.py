"""Private Kimodo constraints. No model or GPU is loaded by importing this file.

Kimodo receives one shared list for a generated batch. The origin is relative
to the first constraint; generated positions are returned to the source origin.
The HTTP endpoint never accepts these constraint dictionaries.
"""
from __future__ import annotations

import numpy as np


KINDS = frozenset({"fullbody", "end-effector", "left-hand", "right-hand"})


def _checked(rows):
    if not rows or not isinstance(rows, (list, tuple)):
        raise ValueError("At least one private motion constraint is required")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in KINDS:
            raise ValueError("Unknown motion constraint type")
        frames = np.asarray(row.get("frame_indices"))
        rotations = np.asarray(row.get("local_joints_rot"), dtype=np.float32)
        positions = np.asarray(row.get("root_positions"), dtype=np.float32)
        if (frames.ndim != 1 or not np.issubdtype(frames.dtype, np.integer)
                or frames.size == 0 or np.any(frames < 0)
                or rotations.shape != (len(frames), 77, 3)
                or positions.shape != (len(frames), 3)):
            raise ValueError("Motion constraint arrays have incompatible shapes")
        if not (np.isfinite(rotations).all() and np.isfinite(positions).all()):
            raise ValueError("Motion constraint contains nonfinite values")
        smooth = np.asarray(row.get("smooth_root_2d", positions[:, [0, 2]]), dtype=np.float32)
        if smooth.shape != (len(frames), 2) or not np.isfinite(smooth).all():
            raise ValueError("Motion constraint root path is invalid")
        entry = {**row, "frame_indices": frames.astype(np.int64, copy=True),
                 "local_joints_rot": rotations.copy(), "root_positions": positions.copy(),
                 "smooth_root_2d": smooth.copy()}
        if row["type"] == "end-effector":
            names = row.get("joint_names")
            if not isinstance(names, (tuple, list)) or not names or not all(isinstance(n, str) for n in names):
                raise ValueError("End-effector joints are missing")
        normalized.append(entry)
    return normalized


def prepare_constraints(rows, model):
    """Return (constraints, first_heading_angle, origin_xz) for private generation.

    Use Kimodo's own from_dict conversion for the SOMA77 to SOMA30 path.
    Do not coerce frame indices with the floating-point tensor dtype.
    """
    from kimodo.constraints import load_constraints_lst
    from kimodo.motion_rep.feature_utils import compute_heading_angle

    checked = _checked(rows)
    if not any(row["type"] == "fullbody" for row in checked):
        raise ValueError("A fullbody origin is required")
    reference = next(row for row in checked if row["type"] == "fullbody")
    origin = reference["smooth_root_2d"][0].copy()
    for row in checked:
        row["root_positions"][:, [0, 2]] -= origin
        row["smooth_root_2d"] -= origin
    converted = load_constraints_lst(checked, model.skeleton, device=model.device)
    fullbody = next((item for item, row in zip(converted, checked)
                     if row["type"] == "fullbody"), None)
    if fullbody is None:
        raise ValueError("Kimodo did not accept the fullbody constraint")
    heading = compute_heading_angle(fullbody.global_joints_positions[None], model.skeleton)[:, 0]
    return converted, heading, origin


def restore_origin(result, origin):
    """Restore translated motion arrays without changing angles or local bones."""
    for key in ("root_positions", "posed_joints"):
        if key in result:
            result[key][..., 0] += origin[0]
            result[key][..., 2] += origin[1]
    if "smooth_root_pos" in result:
        smooth = result["smooth_root_pos"]
        if smooth.shape[-1] == 2:
            smooth += origin
        elif smooth.shape[-1] == 3:
            smooth[..., 0] += origin[0]
            smooth[..., 2] += origin[1]
        else:
            raise ValueError("Unexpected smooth root dimension")
    return result
