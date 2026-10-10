"""Fit generated VRMA pose endpoints to the clips used by the window.

Only pose tracks are changed; duration, root coordinates and humanoid tracks
retain their meanings. This is also reusable by the D1 motion workshop.
"""
import numpy as np


def shortest_slerp(source, target, weight):
    """Unit quaternions [x,y,z,w], including the q and -q equivalence."""
    a = np.asarray(source, dtype=np.float64)
    b = np.asarray(target, dtype=np.float64)
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    dot = float(np.dot(a, b))
    if dot < 0:
        b = -b
        dot = -dot
    if dot > .9995:
        result = a * (1 - weight) + b * weight
    else:
        angle = np.arccos(np.clip(dot, -1, 1))
        result = (np.sin((1 - weight) * angle) * a + np.sin(weight * angle) * b) / np.sin(angle)
    return result / np.linalg.norm(result)


def fit_endpoints(rotations, hips, start_rotations, start_hips,
                  end_rotations, end_hips, *, frames=12):
    """Blend each end smoothly while leaving the middle keys unchanged."""
    if set(rotations) != set(start_rotations) or set(rotations) != set(end_rotations):
        raise ValueError("Missing VRMA bone")
    count = len(hips)
    if frames < 2 or 2 * frames >= count:
        raise ValueError("Seam windows would overlap")
    out = {bone: np.asarray(track, dtype=np.float64).copy() for bone, track in rotations.items()}
    root = np.asarray(hips, dtype=np.float64).copy()
    for i in range(frames + 1):
        t = i / frames
        smooth = t * t * (3 - 2 * t)
        opposite = count - 1 - i
        for bone, track in out.items():
            track[i] = shortest_slerp(start_rotations[bone][0], rotations[bone][i], smooth)
            track[opposite] = shortest_slerp(end_rotations[bone][0], rotations[bone][opposite], smooth)
        root[i] = np.asarray(start_hips[0]) * (1 - smooth) + hips[i] * smooth
        root[opposite] = np.asarray(end_hips[0]) * (1 - smooth) + hips[opposite] * smooth
    return out, root
