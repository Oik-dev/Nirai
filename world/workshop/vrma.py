"""Write a VRM Animation (.vrma, glTF binary) from per-bone local rotations on a T-pose skeleton.

The skeleton is the source's T-pose with identity rest rotations, Y up, +Z forward, left = +X
(the VRM 1.0 / glTF convention), so three-vrm-animation passes the rotations through unchanged.
"""
import json
import struct

import numpy as np


def quat_xyzw(m: np.ndarray) -> np.ndarray:
    """Rotation matrices (T, 3, 3) -> unit quaternions (T, 4) as x, y, z, w, kept on one hemisphere frame to frame."""
    m = np.asarray(m, dtype=np.float64)
    w = np.sqrt(np.clip(1 + m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2], 0, None)) / 2
    x = np.sqrt(np.clip(1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2], 0, None)) / 2
    y = np.sqrt(np.clip(1 - m[..., 0, 0] + m[..., 1, 1] - m[..., 2, 2], 0, None)) / 2
    z = np.sqrt(np.clip(1 - m[..., 0, 0] - m[..., 1, 1] + m[..., 2, 2], 0, None)) / 2
    x = np.copysign(x, m[..., 2, 1] - m[..., 1, 2])
    y = np.copysign(y, m[..., 0, 2] - m[..., 2, 0])
    z = np.copysign(z, m[..., 1, 0] - m[..., 0, 1])
    q = np.stack([x, y, z, w], axis=-1)
    q /= np.linalg.norm(q, axis=-1, keepdims=True)
    for i in range(1, q.shape[0]):  # keep consecutive keys on the same hemisphere so slerp takes the short way
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] *= -1
    return q


def glb(name: str, order: list[str], parent: dict, rest: dict, local: dict, hips: np.ndarray, times: np.ndarray) -> bytes:
    """order: VRM bones, parents first. parent: bone -> parent bone or None. rest: bone -> T-pose world position
    (ground at y=0). local: bone -> (T, 3, 3) rotation relative to the parent. hips: (T, 3) hips position."""
    blobs, accessors, views = [], [], []

    def add(data: np.ndarray, kind: str, with_bounds: bool = False) -> int:
        data = np.ascontiguousarray(data, dtype=np.float32)
        raw = data.tobytes()
        views.append({"buffer": 0, "byteOffset": sum(len(b) for b in blobs), "byteLength": len(raw)})
        blobs.append(raw + b"\0" * (-len(raw) % 4))
        accessor = {"bufferView": len(views) - 1, "componentType": 5126, "count": int(data.shape[0]), "type": kind}
        if with_bounds:
            accessor["min"], accessor["max"] = [float(data.min())], [float(data.max())]
        accessors.append(accessor)
        return len(accessors) - 1

    time_accessor = add(times, "SCALAR", with_bounds=True)
    node_of = {bone: i for i, bone in enumerate(order)}
    nodes = [{"name": bone, "translation": [float(v) for v in rest[bone] - (rest[parent[bone]] if parent[bone] else 0)]}
             for bone in order]
    samplers, channels = [], []
    for bone in order:
        children = [node_of[b] for b in order if parent[b] == bone]
        if children:
            nodes[node_of[bone]]["children"] = children
        samplers.append({"input": time_accessor, "output": add(quat_xyzw(local[bone]), "VEC4"), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node_of[bone], "path": "rotation"}})
    samplers.append({"input": time_accessor, "output": add(hips, "VEC3"), "interpolation": "LINEAR"})
    channels.append({"sampler": len(samplers) - 1, "target": {"node": node_of["hips"], "path": "translation"}})

    binary = b"".join(blobs)
    document = {
        "asset": {"version": "2.0", "generator": "Nirai D0"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {"VRMC_vrm_animation": {"specVersion": "1.0",
                                              "humanoid": {"humanBones": {b: {"node": node_of[b]} for b in order}}}},
        "scene": 0, "scenes": [{"nodes": [node_of["hips"]]}], "nodes": nodes,
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
        "animations": [{"name": name, "samplers": samplers, "channels": channels}],
    }
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    head = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary))
    return head + struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
