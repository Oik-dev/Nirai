"""SwimXYZ SMPL-H swim (AMASS-style npz) -> an in-place, seamlessly looping VRM Animation (.vrma).

SwimXYZ (Fiche et al. 2023, CC BY 4.0, https://zenodo.org/record/8399376) stores the strokes upright: the root
is near identity (head +Y, chest +Z) and `trans` is a straight synthetic pool track. We tip the body forward
90 degrees so it swims face down head-first along +Z, drop the track (the window moves her along her own path),
cut the stretch of whole strokes whose ends match best, spread the leftover seam error over the loop, smooth it
around the loop (the synthetic strokes are noisy, the wrists flip by up to 150 degrees a frame), hold the wrists
and toes at rest, straighten elbows and knees that bend backwards, and hold the hips at a height that keeps every joint clear of the ground (the gate checks that).

SMPL's T-pose is Y up, +Z forward, left = +X like VRM, and every SMPL body joint maps to one VRM bone with the
same parent, so the local rotations pass through unchanged.

usage: python smpl_to_vrma.py <poses.npz> <out.vrma> [--speed 1.0] [--clear 0.25] [--pitch 90] [--smooth 2]
"""
import argparse
import json
from pathlib import Path

import numpy as np

from vrma import glb, quat_xyzw
from loops import close_rotations

# SMPL-H body joints in SMPL order (parents first) -> VRM bones.
BONES = ["hips", "leftUpperLeg", "rightUpperLeg", "spine", "leftLowerLeg", "rightLowerLeg", "chest", "leftFoot",
         "rightFoot", "upperChest", "leftToes", "rightToes", "neck", "leftShoulder", "rightShoulder", "head",
         "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand"]
PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]
# SMPL neutral template joints (metres). Only the bone directions and the hips height matter downstream.
TEMPLATE = np.array([
    [-0.0018, -0.2233, 0.0282], [0.0695, -0.3142, 0.0239], [-0.0677, -0.3145, 0.0214], [-0.0025, -0.1089, 0.0013],
    [0.1040, -0.6756, 0.0150], [-0.1055, -0.6828, 0.0181], [0.0054, 0.0247, 0.0302], [0.0911, -1.0880, -0.0255],
    [-0.0920, -1.0950, -0.0216], [0.0022, 0.0732, 0.0326], [0.1157, -1.1430, 0.0975], [-0.1152, -1.1438, 0.0998],
    [-0.0002, 0.2876, -0.0115], [0.0811, 0.1947, -0.0063], [-0.0791, 0.1928, -0.0107], [0.0050, 0.3523, 0.0401],
    [0.1726, 0.2259, -0.0174], [-0.1730, 0.2250, -0.0197], [0.4320, 0.2131, -0.0424], [-0.4303, 0.2085, -0.0359],
    [0.6820, 0.2221, -0.0418], [-0.6762, 0.2152, -0.0389]])
STILL = [BONES.index(b) for b in ("leftHand", "rightHand", "leftToes", "rightToes")]
HINGES = {"leftLowerLeg": (0, 1), "rightLowerLeg": (0, 1), "leftLowerArm": (1, -1), "rightLowerArm": (1, 1)}  # axis, flex


def rotmat(v: np.ndarray) -> np.ndarray:
    """Axis-angle (..., 3) -> rotation matrices (..., 3, 3)."""
    angle = np.linalg.norm(v, axis=-1)[..., None, None]
    k = v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)
    K = np.zeros(v.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2], K[..., 1, 2] = -k[..., 2], k[..., 1], -k[..., 0]
    K = K - np.swapaxes(K, -1, -2)
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * K @ K


def axis_angle(R: np.ndarray) -> np.ndarray:
    """Rotation matrices (..., 3, 3) -> axis-angle (..., 3)."""
    angle = np.arccos(np.clip((np.trace(R, axis1=-2, axis2=-1) - 1) / 2, -1, 1))
    w = np.stack([R[..., 2, 1] - R[..., 1, 2], R[..., 0, 2] - R[..., 2, 0], R[..., 1, 0] - R[..., 0, 1]], -1)
    return w * (angle / np.maximum(2 * np.sin(angle), 1e-12))[..., None]


def smooth_loop(rot: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian-smooth one period of rotations (T, J, 3, 3) around the loop, averaging quaternions aligned to each centre."""
    if sigma <= 0:
        return rot
    q = quat_xyzw(rot.reshape(-1, 3, 3)).reshape(rot.shape[0], rot.shape[1], 4)
    reach = int(np.ceil(3 * sigma))
    total = np.zeros_like(q)
    for k in range(-reach, reach + 1):
        other = np.roll(q, -k, axis=0)
        sign = np.sign(np.sum(other * q, axis=-1, keepdims=True)) + (np.sum(other * q, axis=-1, keepdims=True) == 0)
        total += np.exp(-0.5 * (k / sigma) ** 2) * sign * other
    return matrix(total / np.linalg.norm(total, axis=-1, keepdims=True))


def matrix(q: np.ndarray) -> np.ndarray:
    """Unit quaternions (..., 4) as x, y, z, w -> rotation matrices (..., 3, 3)."""
    x, y, z, w = np.moveaxis(q, -1, 0)
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                     np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                     np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], -2)


def unbend(rot: np.ndarray, axis: int, flex: float) -> np.ndarray:
    """Remove the reverse part of a hinge (knee about +X, elbow about Y; flex = the sign that bends it the right way).
    The rotation splits into swing @ twist about the hinge axis; a twist past straight is set back to straight."""
    q = quat_xyzw(rot)
    q *= np.where(q[:, 3:] < 0, -1, 1)
    angle = 2 * np.arctan2(q[:, axis], q[:, 3])
    reverse = flex * angle < 0
    twist = np.zeros_like(q)
    twist[:, axis], twist[:, 3] = np.sin(angle / 2), np.cos(angle / 2)
    x1, y1, z1, w1 = np.moveaxis(q, -1, 0)  # swing = q @ conj(twist)
    x2, y2, z2, w2 = -twist[:, 0], -twist[:, 1], -twist[:, 2], twist[:, 3]
    swing = np.stack([w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2, w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                      w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2, w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2], -1)
    return np.where(reverse[:, None, None], matrix(swing), rot)


def best_loop(local: np.ndarray, fps: float, shortest: float = 1.5, longest: float = 3.5) -> tuple[int, int, float]:
    """The stretch [start, start+length] whose end pose is closest to its start pose (summed joint angles, rad)."""
    frames = local.shape[0]
    best = (0, 0, np.inf)
    for length in range(int(shortest * fps), min(int(longest * fps), frames - 1) + 1):
        a, b = local[:frames - length], local[length:]
        gap = np.linalg.norm(axis_angle(np.einsum("tjik,tjil->tjkl", a, b)), axis=-1).sum(-1)  # angle of a^T b
        start = int(gap.argmin())
        if gap[start] < best[2]:
            best = (start, length, float(gap[start]))
    return best


def convert(npz: Path, speed: float, clear: float, pitch: float, smooth: float) -> tuple[bytes, dict]:
    data = np.load(npz, allow_pickle=False)
    fps = float(data["mocap_framerate"])
    local = rotmat(data["poses"][:, :66].reshape(-1, 22, 3))  # (T, 22, 3, 3), body only; fingers stay at rest
    start, length, gap = best_loop(local, fps)
    loop = local[start:start + length + 1].copy()
    loop = close_rotations(loop)
    loop[:length] = smooth_loop(loop[:length], smooth)
    loop[length] = loop[0]
    loop[:, STILL] = np.eye(3)
    for bone, (axis, flex) in HINGES.items():
        loop[:, BONES.index(bone)] = unbend(loop[:, BONES.index(bone)], axis, flex)
    tip = rotmat(np.array([np.radians(pitch), 0.0, 0.0]))
    loop[:, 0] = tip @ loop[:, 0]

    rest_offsets = TEMPLATE - np.where(np.array(PARENTS)[:, None] >= 0, TEMPLATE[np.maximum(PARENTS, 0)], 0)
    world_rot = np.zeros_like(loop)
    position = np.zeros(loop.shape[:2] + (3,))
    for j, p in enumerate(PARENTS):
        if p < 0:
            world_rot[:, j] = loop[:, j]
        else:
            world_rot[:, j] = world_rot[:, p] @ loop[:, j]
            position[:, j] = position[:, p] + np.einsum("tab,b->ta", world_rot[:, p], rest_offsets[j])
    height = -position[..., 1].min() + clear
    hips = np.tile([0.0, height, 0.0], (length + 1, 1))

    lift = -TEMPLATE[:, 1].min()  # rest: the T-pose standing on the ground
    rest = {bone: TEMPLATE[j] + np.array([0, lift, 0]) for j, bone in enumerate(BONES)}
    parent = {bone: BONES[p] if p >= 0 else None for bone, p in zip(BONES, PARENTS)}
    times = np.arange(length + 1) / fps / speed
    info = {"source": npz.name, "start": start, "frames": length + 1, "seconds": float(times[-1]),
            "seam_gap_rad": round(gap, 3), "hips_height": round(float(height), 3), "rest_hips_y": float(rest["hips"][1])}
    return glb(npz.stem, BONES, parent, rest, {b: loop[:, j] for j, b in enumerate(BONES)}, hips, times), info


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("npz", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--clear", type=float, default=0.25, help="metres between the lowest joint and the ground")
    parser.add_argument("--pitch", type=float, default=90.0, help="degrees to tip the upright stroke forward")
    parser.add_argument("--smooth", type=float, default=2.0, help="Gaussian sigma in frames around the loop")
    args = parser.parse_args()
    data, info = convert(args.npz, args.speed, args.clear, args.pitch, args.smooth)
    args.out.write_bytes(data)
    print(json.dumps(info))
