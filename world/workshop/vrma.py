"""Write a VRM Animation (.vrma, glTF binary) from per-bone local rotations on a T-pose skeleton.

The skeleton is the source's T-pose with identity rest rotations, Y up, +Z forward, left = +X
(the VRM 1.0 / glTF convention), so three-vrm-animation passes the rotations through unchanged.
"""
import json
import struct
from pathlib import Path

import numpy as np


def read_tracks(path: Path | bytes) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Read the local rotations and hips of a self-contained Nirai VRMA.

    Used for private pose constraints, not to deserialize untrusted animations.
    Reject incomplete tracks instead of guessing a pose.
    """
    raw = path if isinstance(path, bytes) else Path(path).read_bytes()
    if len(raw) < 28 or raw[:4] != b"glTF" or struct.unpack_from("<II", raw, 4) != (2, len(raw)):
        raise ValueError("Invalid VRMA GLB")
    size, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A or 20 + size + 8 > len(raw):
        raise ValueError("Invalid VRMA JSON chunk")
    document = json.loads(raw[20:20 + size])
    binary_at = 20 + size
    binary_size, binary_kind = struct.unpack_from("<II", raw, binary_at)
    binary_at += 8
    if binary_kind != 0x004E4942 or binary_at + binary_size != len(raw):
        raise ValueError("Invalid VRMA binary chunk")

    def values(accessor_id):
        accessor = document["accessors"][accessor_id]
        view = document["bufferViews"][accessor["bufferView"]]
        width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
        if accessor["componentType"] != 5126 or "byteStride" in view or "sparse" in accessor:
            raise ValueError("Unsupported VRMA accessor")
        inner_offset = accessor.get("byteOffset", 0)
        offset = view.get("byteOffset", 0) + inner_offset
        length = accessor["count"] * width * 4
        if (inner_offset < 0 or offset < 0 or offset + length > binary_size
                or inner_offset + length > view["byteLength"]):
            raise ValueError("Invalid VRMA accessor bounds")
        return np.frombuffer(raw, dtype="<f4", count=accessor["count"] * width,
                             offset=binary_at + offset).reshape(accessor["count"], width).astype(np.float64)

    animations = document["animations"]
    if len(animations) != 1:
        raise ValueError("Expected a single VRMA animation")
    animation = animations[0]
    rotations, hips, times = {}, None, None
    for channel in animation["channels"]:
        sampler = animation["samplers"][channel["sampler"]]
        track_times = values(sampler["input"]).reshape(-1)
        if times is None:
            times = track_times
        elif not np.array_equal(times, track_times):
            raise ValueError("VRMA tracks use different times")
        track = values(sampler["output"])
        bone = document["nodes"][channel["target"]["node"]]["name"]
        action = channel["target"]["path"]
        if action == "rotation":
            if bone in rotations or track.shape[1] != 4:
                raise ValueError("Invalid rotation track")
            rotations[bone] = track
        elif action == "translation" and bone == "hips" and hips is None and track.shape[1] == 3:
            hips = track
        else:
            raise ValueError("Unexpected VRMA animation track")
    if hips is None or not rotations or times is None or len(times) != len(hips):
        raise ValueError("Incomplete VRMA animation")
    if not np.isfinite(hips).all() or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError("Invalid VRMA timing or hips")
    for track in rotations.values():
        if (track.shape != (len(hips), 4) or not np.isfinite(track).all()
                or np.any(np.abs(np.linalg.norm(track, axis=1) - 1) > 1e-3)):
            raise ValueError("Invalid VRMA rotation values")
    return rotations, hips


def replace_tracks(raw: bytes, rotations: dict[str, np.ndarray], hips: np.ndarray) -> bytes:
    """Replace local pose arrays in a VRMA without changing skeleton, tracks, or timing.

    All output arrays must match the original; this intentionally cannot
    introduce an animation channel or modify the original clip's duration.
    """
    existing, original_hips = read_tracks(raw)
    if set(rotations) != set(existing) or np.asarray(hips).shape != original_hips.shape:
        raise ValueError("Replacement must preserve all existing tracks")
    if any(np.asarray(rotations[k]).shape != v.shape for k, v in existing.items()):
        raise ValueError("Replacement rotation shape mismatch")
    size = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20 + size])
    binary_at = 20 + size + 8
    result = bytearray(raw)
    for channel in document["animations"][0]["channels"]:
        sampler = document["animations"][0]["samplers"][channel["sampler"]]
        accessor = document["accessors"][sampler["output"]]
        view = document["bufferViews"][accessor["bufferView"]]
        name = document["nodes"][channel["target"]["node"]]["name"]
        kind = channel["target"]["path"]
        value = np.asarray(hips if kind == "translation" else rotations[name], dtype=np.float64)
        if (accessor["componentType"] != 5126 or "byteStride" in view
                or value.shape != (accessor["count"], {"VEC3": 3, "VEC4": 4}[accessor["type"]])
                or not np.isfinite(value).all()):
            raise ValueError("Invalid replacement accessor")
        if kind == "rotation" and np.any(np.abs(np.linalg.norm(value, axis=1) - 1) > 1e-3):
            raise ValueError("Rotation is not unit length")
        offset = binary_at + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        data = np.asarray(value, dtype="<f4").tobytes()
        if offset < binary_at or offset + len(data) > binary_at + struct.unpack_from("<I", raw, 20 + size)[0]:
            raise ValueError("Invalid replacement bounds")
        result[offset:offset + len(data)] = data
    return bytes(result)


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


def glb(name: str, order: list[str], parent: dict, rest: dict, local: dict, hips: np.ndarray, times: np.ndarray,
        animated_bones: set[str] | None = None) -> bytes:
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
        if animated_bones is not None and bone not in animated_bones:
            continue
        samplers.append({"input": time_accessor, "output": add(quat_xyzw(local[bone]), "VEC4"), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node_of[bone], "path": "rotation"}})
    if animated_bones is None:
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
