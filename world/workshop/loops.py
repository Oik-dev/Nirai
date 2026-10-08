"""Bring the end of a motion cycle to its beginning without a hard snap.

The same quaternion correction is used for SMPL and Kimodo. The caller chooses
the source interval; this routine knows nothing about the activity or body.
"""
import numpy as np
from scipy.spatial.transform import Rotation


def close_rotations(rotations: np.ndarray) -> np.ndarray:
    """(frames, joints, 3, 3) local rotations, with an exact first/last seam."""
    frames = len(rotations)
    difference = rotations[0] @ rotations[-1].transpose(0, 2, 1)
    correction = Rotation.from_matrix(difference).as_rotvec()
    fraction = np.linspace(0, 1, frames)[:, None, None]
    steps = Rotation.from_rotvec((fraction * correction[None]).reshape(-1, 3))
    matrices = steps.as_matrix().reshape(rotations.shape)
    result = matrices @ rotations
    result[-1] = result[0]
    return result
