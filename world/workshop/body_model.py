"""Disposable Y-up MuJoCo proxy from a look.mjs sample (never commit derived MJCF)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import mujoco
import numpy as np
from scipy.spatial.transform import Rotation

RANGES = json.loads((Path(__file__).parents[1] / "sea" / "joint-ranges.json").read_text(encoding="utf-8"))
PARENTS = {
    "spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest", "head": "neck",
    **{s + k: (v if v in ("hips", "upperChest") else s + v) for s in ("left", "right") for k, v in {
        "UpperLeg": "hips", "LowerLeg": "UpperLeg", "Foot": "LowerLeg", "Toes": "Foot",
        "Shoulder": "upperChest", "UpperArm": "Shoulder", "LowerArm": "UpperArm", "Hand": "LowerArm"
    }.items()},
}

def parent_of(name, names):
    parent = PARENTS.get(name)
    while parent and parent not in names:
        parent = PARENTS.get(parent)
    return parent

def segment_distance(a, b, c, d):
    """Shortest line-segment distance; includes endpoints and parallel cases."""
    u, v, w = b - a, d - c, a - c
    aa, bb, cc, dd, ee = np.dot(u, u), np.dot(u, v), np.dot(v, v), np.dot(u, w), np.dot(v, w)
    if aa < 1e-12:
        if cc < 1e-12:
            return float(np.linalg.norm(a - c))
        return float(np.linalg.norm(a - (c + np.clip(ee / cc, 0, 1) * v)))
    if cc < 1e-12:
        return float(np.linalg.norm(a + np.clip(-dd / aa, 0, 1) * u - c))
    denom = aa * cc - bb * bb
    s = np.clip((bb * ee - cc * dd) / denom, 0, 1) if denom > 1e-12 else 0
    t = np.clip((bb * s + ee) / cc, 0, 1)
    s = np.clip((bb * t - dd) / aa, 0, 1)
    return float(np.linalg.norm(w + s * u - t * v))

def _vec(v):
    return " ".join(f"{float(x):.9g}" for x in v)

@dataclass
class BodyProxy:
    xml: str
    model: mujoco.MjModel
    data: mujoco.MjData
    names: list[str]
    parents: dict[str, str | None]
    children: dict[str, str]
    radius: dict[str, float]
    fallback: list[str]
    rest: dict
    surface: dict

    def pose(self, samples, frame):
        """Apply a frame to the T-pose proxy via VRM normalized joint rotations.

        Normalized T-pose is mandatory: positions alone cannot recover
        twist around a bone axis, which would corrupt body-surface contacts.
        """
        dst = {n: np.asarray(samples["bones"][n]["position"][frame], dtype=float) for n in self.names}
        self.data.qpos[:3] = dst["hips"]
        # The reference model is the VRM normalized T-pose; normalized local
        # rotations are already in its basis. Never infer axial twist from bone
        # positions (a forearm can twist without changing its endpoints).
        if "tpose" not in samples:
            raise ValueError("missing normalized tpose: refusing approximate bone rotation")
        for name in self.names:
            local = Rotation.from_quat(samples["bones"][name]["rotation"][frame])
            if name == "hips":
                x, y, z, w = local.as_quat()
                self.data.qpos[3:7] = [w, x, y, z]
            else:
                definitions = RANGES["joints"][name]
                kinds = ("bend", "offAxis", "twist")
                axes = [definitions[k]["axis"] for k in kinds]
                angles = local.as_euler("".join(a[-1].upper() for a in axes))
                for kind, signed_axis, angle in zip(kinds, axes, angles):
                    jid = self.model.joint(f"{name}_{kind}").id
                    self.data.qpos[self.model.jnt_qposadr[jid]] = (
                        angle if len(signed_axis) == 1 else -angle)
        self.data.qvel[:] = 0
        mujoco.mj_forward(self.model, self.data)

    def segments(self, samples, frame):
        points = {n: np.asarray(samples["bones"][n]["position"][frame], dtype=float) for n in self.names}
        out = {}
        for name in self.names:
            if name == "hips" or name not in self.children:
                continue
            end = self.children[name]
            out[name] = (points[name], points[end])
        return out

    def alignment_error(self, samples, frame):
        self.pose(samples, frame)
        return max(
            (float(np.linalg.norm(self.data.xpos[self.model.body(n).id] -
                       samples["bones"][n]["position"][frame])), n)
            for n in self.names
        )

    def collisions(self, samples, frame):
        segments = self.segments(samples, frame)
        worst = {"depth_m": 0.0, "pair": None, "frame": frame}
        for idx, (n, (a, b)) in enumerate(segments.items()):
            if n not in self.radius:
                continue
            for m, (c, d) in list(segments.items())[idx+1:]:
                if m not in self.radius or self._near(n, m):
                    continue
                depth = self.radius[n] + self.radius[m] - segment_distance(a, b, c, d)
                if depth > worst["depth_m"]:
                    worst = {"depth_m": round(float(depth), 6), "pair": [n, m], "frame": frame}
        return worst

    def _near(self, a, b):
        def path(n):
            result = [n]
            while self.parents.get(n):
                n = self.parents[n]
                result.append(n)
            return result
        pa, pb = path(a), path(b)
        distance = min((i + j for i, x in enumerate(pa) for j, y in enumerate(pb) if x == y), default=99)
        return distance <= 2

def build_model(samples: dict) -> BodyProxy:
    reference = samples.get("tpose", samples)
    rest = reference["rest"]
    candidates = {n for n in rest if n in PARENTS and n in samples["bones"]}
    names = ["hips"]
    while candidates:
        newly = sorted(n for n in candidates if parent_of(n, list(candidates) + names) in names)
        if not newly:
            raise ValueError(f"invalid bone hierarchy: {sorted(candidates)}")
        names.extend(newly)
        candidates.difference_update(newly)
    if "hips" not in rest:
        raise ValueError("hips not found")
    # Preserve the VRM hierarchy, including the nearest existing ancestor of optional bones.
    parents = {n: parent_of(n, names) for n in names}
    children = {}
    for name in reversed(names):
        p = parents[name]
        if p and p not in children:
            children[p] = name
    for name in names:
        candidates = [n for n in names if parents[n] == name]
        if candidates:
            children[name] = max(candidates, key=lambda c: np.linalg.norm(np.asarray(rest[c]) - rest[name]))
    # Skin is reserved for the existing sole gate. bodySkin adds surface samples
    # without changing what the gate considers ground penetration.
    surface = reference.get("bodySkin", {})
    radius = {}
    fallback = []
    for name, child in children.items():
        if name == "hips":
            continue
        a, b = np.asarray(rest[name]), np.asarray(rest[child])
        axis = b - a
        norm = np.linalg.norm(axis)
        if norm < 0.005:
            continue
        points = surface.get(name, {}).get("rest", [])
        if points:
            offsets = [np.linalg.norm((np.asarray(p) - a) -
                       np.clip(np.dot(np.asarray(p) - a, axis) / (norm * norm), 0, 1) * axis)
                       for p in points]
            rad = float(np.median(offsets))
        else:
            rad = 0.13 * norm
            fallback.append(name)
        radius[name] = max(0.006, min(0.12, rad))
    root = ET.Element("mujoco", model="nirai_yup_proxy")
    ET.SubElement(root, "compiler", angle="radian")
    ET.SubElement(root, "option", gravity="0 -9.81 0", timestep="0.001", integrator="implicitfast", solver="Newton", iterations="100")
    default = ET.SubElement(root, "default")
    ET.SubElement(default, "joint", damping="0.5", armature="0.02")
    ET.SubElement(default, "geom", friction="1.2 0.05 0.03", condim="3", contype="1", conaffinity="1")
    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "geom", name="sand", type="plane", size="20 20 0.1",
                  quat="0.707106781 -0.707106781 0 0", density="1000", contype="1", conaffinity="1")
    bodies = {}
    def build(name):
        par = parents[name]
        where = bodies[par] if par else world
        pos = np.asarray(rest[name]) - (np.asarray(rest[par]) if par else 0)
        body = ET.SubElement(where, "body", name=name, pos=_vec(pos))
        bodies[name] = body
        if name == "hips":
            ET.SubElement(body, "freejoint", name="root")
            pelvis_points = np.asarray(surface.get("hips", {}).get("rest", []), dtype=float)
            pelvis_radius = float(np.median(np.linalg.norm(pelvis_points - rest["hips"], axis=1))) if len(pelvis_points) else 0.07
            ET.SubElement(body, "geom", name="pelvis", type="sphere",
                          size=f"{np.clip(pelvis_radius, 0.006, 0.12):.7g}", density="1000")
        else:
            ranges = RANGES["joints"].get(name, {})
            for kind in ("bend", "offAxis", "twist"):
                config = ranges.get(kind, {})
                axis = config.get("axis")
                if not axis:
                    raise ValueError(f"joint missing {name}.{kind}")
                direction = [int(a == axis[-1]) * (-1 if axis.startswith("-") else 1)
                             for a in "xyz"]
                params = {"name": f"{name}_{kind}", "type": "hinge",
                          "axis": _vec(direction)}
                if "min" in config and "max" in config:
                    params["range"] = _vec(np.deg2rad([config["min"], config["max"]]))
                    params["limited"] = "true"
                else:
                    params["limited"] = "false"
                ET.SubElement(body, "joint", **params)
        if name in radius:
            vector = np.asarray(rest[children[name]]) - np.asarray(rest[name])
            ET.SubElement(body, "geom", name=f"{name}_capsule", type="capsule",
                          fromto=_vec([0, 0, 0]) + " " + _vec(vector),
                          size=f"{radius[name]:.7g}", density="1000")
        # terminal bone: small end effector that has mass
        elif name != "hips":
            tip_points = np.asarray(surface.get(name, {}).get("rest", []), dtype=float)
            tip_radius = float(np.median(np.linalg.norm(tip_points - rest[name], axis=1))) if len(tip_points) else 0.012
            ET.SubElement(body, "geom", name=f"{name}_tip", type="sphere",
                          size=f"{np.clip(tip_radius, 0.006, 0.12):.7g}", density="1000")
        for child in names:
            if parents[child] == name:
                build(child)
    build("hips")
    contacts = ET.SubElement(root, "contact")
    for i, a in enumerate(names):
        for b in names[i+1:]:
            # Explicit exclusions make collision behaviour match geometric checks.
            def chain(n, count):
                result=[n]
                for _ in range(count):
                    n=parents.get(n)
                    if not n: break
                    result.append(n)
                return result
            if a in chain(b, 2) or b in chain(a, 2) or parents[a] is not None and parents[a] == parents[b]:
                ET.SubElement(contacts, "exclude", body1=a, body2=b)
    xml = ET.tostring(root, encoding="unicode")
    model = mujoco.MjModel.from_xml_string(xml)
    return BodyProxy(xml, model, mujoco.MjData(model), names, parents, children, radius, fallback, rest, surface)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("samples")
    parser.add_argument("--out", help="local, untracked MJCF destination")
    args = parser.parse_args()
    sample = json.loads(Path(args.samples).read_text(encoding="utf-8"))
    model = build_model(sample)
    if args.out:
        Path(args.out).write_text(model.xml, encoding="utf-8")
    print(json.dumps({"bodies": len(model.names), "radii": model.radius,
                      "fallback": model.fallback,
                      "alignment": model.alignment_error(sample, 0)}, ensure_ascii=False))
