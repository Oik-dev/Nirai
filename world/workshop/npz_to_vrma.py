"""Kimodo SOMA77 motion (NPZ + skeleton JSON) -> VRM Animation (.vrma).

Kimodo's rotations are relative to its standard T-pose (the neutral joints), Y up, +Z forward, left = +X,
which is the VRM 1.0 / glTF convention. Each VRM bone's local rotation is inv(world of nearest mapped
ancestor) @ world of the bone; unmapped SOMA joints (Spine3? Neck2, ends, fingers, face) fold into the next
mapped descendant.

usage: python npz_to_vrma.py <motion.npz> <motion.json> <out.vrma>
"""
import json
import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from vrma import glb
from loops import close_rotations

BODY = {  # VRM humanoid bone -> SOMA77 joint (PoC viewer soma.js, from kimodo/skeleton/definitions.py)
    "hips": "Hips", "spine": "Spine1", "chest": "Spine2", "upperChest": "Chest", "neck": "Neck1", "head": "Head",
    "leftShoulder": "LeftShoulder", "rightShoulder": "RightShoulder",
    "leftUpperArm": "LeftArm", "rightUpperArm": "RightArm", "leftLowerArm": "LeftForeArm", "rightLowerArm": "RightForeArm",
    "leftHand": "LeftHand", "rightHand": "RightHand", "leftUpperLeg": "LeftLeg", "rightUpperLeg": "RightLeg",
    "leftLowerLeg": "LeftShin", "rightLowerLeg": "RightShin", "leftFoot": "LeftFoot", "rightFoot": "RightFoot",
    "leftToes": "LeftToeBase", "rightToes": "RightToeBase",
}

# 身振りは活動の土台へ重ねる。腰・脚の回転と腰の移動を持たせない。
UPPER_BODY = frozenset({
    "spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder",
    "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand",
})


def animation_to_soma(rotations: dict[str, np.ndarray], hips: np.ndarray,
                      skeleton: dict) -> dict[str, np.ndarray]:
    """Invert our VRMA retargeting, keeping unknown SOMA77 joints neutral.

    Mapped joints retain the VRM local orientation; unmapped intermediate
    joints have identity locals so descendants inherit their ancestor's world.
    """
    names, parents = skeleton["joint_names"], skeleton["parents"]
    if (len(names) != 77 or len(parents) != 77 or len(set(names)) != 77
            or any(name not in names for name in BODY.values())
            or set(rotations) != set(BODY) or hips.ndim != 2 or hips.shape[1] != 3
            or not np.isfinite(hips).all()):
        raise ValueError("Incomplete SOMA77/VRMA skeleton")
    frames = len(hips)
    from scipy.spatial.transform import Rotation

    local = np.broadcast_to(np.eye(3), (frames, 77, 3, 3)).copy()
    bone_at = {names.index(joint): bone for bone, joint in BODY.items()}
    for joint, bone in bone_at.items():
        track = np.asarray(rotations[bone])
        if track.shape != (frames, 4) or not np.isfinite(track).all():
            raise ValueError("Invalid VRMA track")
        local[:, joint] = Rotation.from_quat(track).as_matrix()
    global_rot = local.copy()
    for j, parent in enumerate(parents):
        if parent >= j or parent < -1:
            raise ValueError("SOMA77 parents must precede children")
        if parent >= 0:
            global_rot[:, j] = global_rot[:, parent] @ local[:, j]
    return {"local_rot_mats": local, "global_rot_mats": global_rot,
            "root_positions": np.asarray(hips, dtype=np.float64).copy()}


def position_animation(rotations: dict[str, np.ndarray], hips: np.ndarray,
                       yaw_degrees: float, seat_xz) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Bake the window's sleep yaw and seat-aligned XZ into a copied animation."""
    from scipy.spatial.transform import Rotation

    target = np.asarray(seat_xz, dtype=np.float64)
    if target.shape != (2,) or not np.isfinite(target).all() or not np.isfinite(yaw_degrees):
        raise ValueError("Invalid pose alignment")
    result = {name: np.asarray(value, dtype=np.float64).copy() for name, value in rotations.items()}
    root = np.asarray(hips, dtype=np.float64).copy()
    if "hips" not in result or root.ndim != 2 or root.shape[1] != 3:
        raise ValueError("Missing root track")
    turn = Rotation.from_euler("y", yaw_degrees, degrees=True)
    result["hips"] = (turn * Rotation.from_quat(result["hips"])).as_quat()
    root = turn.apply(root)
    root[:, [0, 2]] += target - root[0, [0, 2]]
    return result, root


def clamp_elbow_rotation(matrices: np.ndarray, *, left: bool) -> np.ndarray:
    """Limit positive elbow bends continuously across the +/-180-degree branch cut."""
    q = Rotation.from_matrix(matrices).as_quat()
    x, y, z, w = q.T
    sign = 1 if left else -1
    bend = np.unwrap(np.arctan2(sign * 2 * (x * z - y * w), 1 - 2 * (y * y + z * z)))
    excess = np.maximum(0, bend - np.deg2rad(153))
    delta = Rotation.from_rotvec(np.column_stack((np.zeros(len(q)), excess * sign, np.zeros(len(q)))))
    return (Rotation.from_quat(q) * delta).as_matrix()


def convert(npz_path: Path, json_path: Path, start: int = 0, end: int | None = None,
            loop: bool = False, upper_body: bool = False) -> tuple[bytes, dict]:
    """The existing CLI uses the same in-memory converter as the HTTP generator."""
    skeleton = json.loads(json_path.read_text(encoding="utf-8"))["skeleton"]
    with np.load(npz_path, allow_pickle=False) as arrays:
        return convert_arrays(arrays, skeleton, start=start, end=end, loop=loop,
                              label=npz_path.stem, upper_body=upper_body)


def convert_arrays(arrays, skeleton: dict, start: int = 0, end: int | None = None,
                   loop: bool = False, label: str = "motion", upper_body: bool = False) -> tuple[bytes, dict]:
    """Convert model output without storing a submitted text or embedding on disk."""
    names, parents = skeleton["joint_names"], skeleton["parents"]
    neutral = np.asarray(skeleton["neutral_joints_m"], dtype=np.float64)
    fps = float(skeleton["fps"])
    world = arrays["global_rot_mats"].astype(np.float64)  # (T, 77, 3, 3), relative to the T-pose
    root = arrays["root_positions"].astype(np.float64)  # (T, 3), hips in metres, ground at y=0
    world, root = world[start:end], root[start:end]

    index = {bone: names.index(joint) for bone, joint in BODY.items()}
    bone_of = {joint: bone for bone, joint in index.items()}

    def mapped_parent(bone: str) -> str | None:
        joint = parents[index[bone]]
        while joint >= 0 and joint not in bone_of:
            joint = parents[joint]
        return bone_of.get(joint)

    order = list(BODY)  # parents come before children in BODY
    parent = {bone: mapped_parent(bone) for bone in order}
    ground = -neutral.min(axis=0)[1]  # rest: the T-pose standing on the ground (the lowest joint at y=0)
    rest = {bone: neutral[index[bone]] + np.array([0, ground, 0]) for bone in order}
    local = {bone: world[:, index[bone]] if parent[bone] is None
             else np.einsum("tji,tjk->tik", world[:, index[parent[bone]]], world[:, index[bone]])  # inv(R_parent) @ R
             for bone in order}
    for bone, left in [('leftLowerArm', True), ('rightLowerArm', False)]:
        local[bone] = clamp_elbow_rotation(local[bone], left=left)
    if loop:
        fraction = np.linspace(0, 1, len(root))
        closed = close_rotations(np.stack([local[bone] for bone in order], axis=1))
        local = {bone: closed[:, i] for i, bone in enumerate(order)}
        root -= fraction[:, None] * (root[-1] - root[0])
    times = np.arange(world.shape[0]) / fps
    info = {"frames": world.shape[0], "start": start, "end": end, "loop": loop, "fps": fps, "rest_hips_y": float(rest["hips"][1]),
            "root_y_range": [float(root[:, 1].min()), float(root[:, 1].max())]}
    return glb(label, order, parent, rest, local, root, times,
               animated_bones=set(UPPER_BODY) if upper_body else None), info


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('npz', type=Path)
    parser.add_argument('skeleton', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--end', type=int)
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--upper-body', action='store_true', help='Only upper-body rotations; no hip translation or leg tracks')
    args = parser.parse_args()
    data, info = convert(args.npz, args.skeleton, args.start, args.end, args.loop, args.upper_body)
    args.out.write_bytes(data)
    print(json.dumps(info))
