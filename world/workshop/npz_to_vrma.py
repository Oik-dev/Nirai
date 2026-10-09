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
        q = Rotation.from_matrix(local[bone]).as_quat()
        x, y, z, w = q.T
        bend = np.arctan2((1 if left else -1) * 2 * (x * z - y * w), 1 - 2 * (y * y + z * z))
        excess = np.maximum(0, bend - np.deg2rad(153))
        delta = Rotation.from_rotvec(np.column_stack((np.zeros(len(q)), excess * (1 if left else -1), np.zeros(len(q)))))
        local[bone] = (Rotation.from_quat(q) * delta).as_matrix()
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
