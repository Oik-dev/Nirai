"""Motion-independent surface-contact and static-equilibrium checks for look samples.

Only CPU computation, zero time integration, no reference to actor's wishes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import mujoco
import numpy as np
from scipy.optimize import lsq_linear
from body_model import build_model

LIMITS = {
    "penetration": {
        "max": 0.087374, "unit": "m",
        "reason": "Midpoint: 0.056319m max accepted 伸び vs 0.118430m failing 泳ぐ (true T-pose proxy, Yumeka 2026-10-15)",
    },
    "contactBand": {
        "max": 0.03, "unit": "m",
        "reason": "Surface sampling coarseness and soft sand allowance (plan §2.2)",
    },
    "contactFriction": {"value": 1.0, "unit": "coefficient", "reason": "Plan §2.2"},
    "supportRootResidual": {
        "max": 0.01, "unit": "fraction of body weight (and body-weight times standing hip height)",
        "reason": "1% numerical tolerance; a free pelvis cannot create force or torque",
    },
    "supportJointEffort": {
        "max": None, "unit": "m of body-weight equivalent",
        "reason": "Calculated from left/right upper arm holding torque in T-pose, per body model (plan §2.2)",
    },
    "quietSpeed": {"max": 0.08, "unit": "m/s", "reason": "Body near-static within 0.5s"},
    "seatedHipsRatio": {"max": 1/3, "unit": "rest hips height", "reason": "Existing gate seatedHipsRatio"},
}

def quiet_frames(samples, quiet_speed=0.08):
    bones, rest = samples["bones"], samples["rest"]
    fps = samples["fps"]
    count = len(bones["hips"]["position"])
    stride = max(1, round(0.5 * fps))
    observed = []
    for i in range(0, count, stride):
        if bones["hips"]["position"][i][1] >= rest["hips"][1] / 3:
            continue
        nearby = min(count-1, i+stride)
        if nearby <= i:
            nearby = max(0, i-stride)
        interval = abs(nearby-i) / fps
        if interval == 0:
            continue
        tracking = ("hips", "head", "leftHand", "rightHand", "leftFoot", "rightFoot")
        speeds = [
            np.linalg.norm(np.asarray(bones[n]["position"][i]) -
                           np.asarray(bones[n]["position"][nearby])) / interval
            for n in tracking if n in bones
        ]
        if speeds and max(speeds) <= quiet_speed:
            observed.append(i)
    return observed

def surface_contacts(proxy, samples, frame, band=0.03):
    """Convert bodySkin.rest into posed locations (rigid bone-local proxy).

    The skin samples are in world coordinates at rest. The measured relative
    bone transform is applied without changing world vertical direction.
    """
    proxy.pose(samples, frame)
    model, data = proxy.model, proxy.data
    collected = []
    for name, surface in proxy.surface.items():
        if name not in proxy.names:
            continue
        rest = np.asarray(surface.get("rest", []), dtype=float)
        if not len(rest):
            continue
        bid = model.body(name).id
        origin = np.asarray(proxy.rest[name], dtype=float)
        points = data.xpos[bid] + (rest - origin) @ data.xmat[bid].reshape((3, 3)).T
        for p in points:
            if -band <= p[1] <= band:
                collected.append((name, np.array([p[0], 0, p[2]])))
    # Downsample within each body and near-identical contact footprint.
    # Keep points far apart; otherwise lsq_linear has too many redundant columns.
    kept = {}
    for name, point in collected:
        key = (name, round(point[0] / 0.025), round(point[2] / 0.025))
        kept.setdefault(key, (name, point))
    return list(kept.values())

def joint_effort(proxy, torques, frame):
    """Combined torque along the three actual world-space hinge axes, in body-weight metres."""
    model, data = proxy.model, proxy.data
    mg = float(model.body_subtreemass[model.body("hips").id] * 9.81)
    worst = {"effort_m": 0.0, "joint": None, "frame": frame}
    for name in proxy.names:
        if name == "hips":
            continue
        jids = [model.joint(f"{name}_{kind}").id for kind in ("bend", "offAxis", "twist")]
        dofs = [int(model.jnt_dofadr[j]) for j in jids]
        axes = np.asarray([data.xaxis[j] for j in jids]).T
        # qfrc entries are generalized torques; solve the physical 3D torque.
        torque = np.linalg.lstsq(axes.T, torques[dofs], rcond=None)[0]
        normalized = float(np.linalg.norm(torque) / mg)
        if normalized > worst["effort_m"]:
            worst = {"effort_m": round(normalized, 6), "joint": name, "frame": frame}
    return worst

def resting_arm_limit(proxy, samples):
    """Body-specific effort limit from a separately sampled actual VRM T-pose."""
    # Body's presentation 'rest' lowers its arms: it is not a T-pose.
    # Preserve the exact reference with tpose.rest and its own surface model.
    reference = samples.get("tpose", samples)
    if reference is not samples:
        proxy = build_model({**reference, "tpose": reference})
    frame = 0
    proxy.pose({**reference, "tpose": reference}, frame)
    model, data = proxy.model, proxy.data
    mg = float(model.body_subtreemass[model.body("hips").id] * 9.81)
    arm_efforts = []
    # Each arm's torque from the mass of upper arm and all descendants;
    # T-pose rest geometry is used (not the animation frame rotation).
    rest = reference["rest"]
    for side in ("left", "right"):
        bname = f"{side}Shoulder" if f"{side}Shoulder" in proxy.names else f"{side}UpperArm"
        if bname not in proxy.names:
            continue
        anchor = np.asarray(rest[bname], dtype=float)
        mass = 0.0
        torque = np.zeros(3)
        for bid in range(1, model.nbody):
            n = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
            ancestry = n
            while ancestry and ancestry != bname:
                ancestry = proxy.parents.get(ancestry)
            if ancestry != bname:
                continue
            # Body geom masses represent the same rest segment shapes.
            center = np.asarray(rest[n], dtype=float)
            if n in proxy.children:
                center = (center + np.asarray(rest[proxy.children[n]], dtype=float)) / 2
            weight = model.body_mass[bid] * 9.81
            mass += model.body_mass[bid]
            torque += np.cross(center - anchor, [0, -weight, 0])
        arm_efforts.append(float(np.linalg.norm(torque) / mg))
    return max(arm_efforts, default=0.0)

def static_support(proxy, samples, frame, band=0.03, friction=1):
    model, data = proxy.model, proxy.data
    contacts = surface_contacts(proxy, samples, frame, band)
    mg = float(model.body_subtreemass[model.body("hips").id] * 9.81)
    if mg <= 0:
        raise ValueError("non-positive body mass")
    qfrc = data.qfrc_bias.copy()
    nv = model.nv
    # Generalized force directions of four positive-weight friction pyramid edges.
    pyramid = np.asarray([[friction, 1, 0], [-friction, 1, 0],
                          [0, 1, friction], [0, 1, -friction]], dtype=float)
    pyramid /= np.linalg.norm(pyramid, axis=1)[:, None]
    cols = []
    for name, pos in contacts:
        bid = model.body(name).id
        jac = np.zeros((3, nv))
        mujoco.mj_jac(model, data, jac, None, pos, bid)
        cols.extend((jac.T @ v for v in pyramid))
    a = np.asarray(cols).T if cols else np.zeros((nv, 0))
    hips_height = max(float(samples["rest"]["hips"][1]), 0.1)
    # Free root six dofs: translation forces and rotation torques. The contact
    # solver should pay a much higher cost for imbalance than joint torque.
    weights = np.full(nv, 1/mg)
    weights[:3] = 1e3/mg
    weights[3:6] = 1e3/(mg*hips_height)
    if a.shape[1]:
        aw = a * weights[:, None]
        bw = qfrc * weights
        # Light contact-force penalty removes gratuitous opposing contact forces.
        regularization = 1e-5/mg
        aw = np.vstack([aw, regularization*np.eye(a.shape[1])])
        bw = np.concatenate([bw, np.zeros(a.shape[1])])
        fit = lsq_linear(aw, bw, bounds=(0, np.inf), method="bvls", max_iter=150)
        residual = qfrc - a @ fit.x
    else:
        residual = qfrc
    root_force = float(np.linalg.norm(residual[:3])/mg)
    root_torque = float(np.linalg.norm(residual[3:6])/(mg*hips_height))
    return {
        "root_residual": round(max(root_force, root_torque), 6),
        "root_force_fraction": round(root_force, 6),
        "root_torque_fraction": round(root_torque, 6),
        "contacts": len(contacts),
        **joint_effort(proxy, residual, frame),
        "frame": frame,
    }

def evaluate(samples, limits=LIMITS):
    if "tpose" not in samples:
        raise ValueError("missing normalized T-pose reference")
    proxy = build_model(samples)
    frames = len(samples["bones"]["hips"]["position"])
    penetration = {"depth_m": 0., "pair": None, "frame": None}
    for i in range(frames):
        result = proxy.collisions(samples, i)
        if result["depth_m"] > penetration["depth_m"]:
            penetration = result
    supported = quiet_frames(samples, limits["quietSpeed"]["max"])
    effort_limit = resting_arm_limit(proxy, samples)
    if limits["supportJointEffort"]["max"] is not None:
        effort_limit = limits["supportJointEffort"]["max"]
    support = {"root_residual": 0, "effort_m": 0, "contacts": None,
               "joint": None, "frame": None, "observations": len(supported),
               "arm_limit_m": round(effort_limit, 6)}
    worst_root = {"root_residual": 0, "frame": None, "contacts": None}
    for frame in supported:
        result = static_support(proxy, samples, frame,
                               limits["contactBand"]["max"],
                               limits["contactFriction"]["value"])
        if result["root_residual"] > worst_root["root_residual"]:
            worst_root = {k: result[k] for k in ("root_residual", "root_force_fraction",
                                                 "root_torque_fraction", "frame", "contacts")}
        if result["effort_m"] > support["effort_m"]:
            support.update({k: result[k] for k in ("effort_m", "joint", "frame", "contacts")})
    support.update({"worst_root": worst_root})
    # A support failure is measured independently of penetration: either can fail.
    support["root_residual"] = worst_root["root_residual"]
    support["pass"] = (support["root_residual"] <= limits["supportRootResidual"]["max"] and
                       support["effort_m"] <= effort_limit)
    alignment, alignment_bone = max(proxy.alignment_error(samples, i)
                                   for i in {0, frames//2, frames-1})
    return {
        "pass": penetration["depth_m"] <= limits["penetration"]["max"] and support["pass"],
        "penetration": {**penetration,
                        "pass": penetration["depth_m"] <= limits["penetration"]["max"]},
        "support": support,
        "alignment": {"max_error_m": round(alignment, 6), "bone": alignment_bone},
        "fallback_radii": proxy.fallback, "checked_frames": frames,
        "limits": limits,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("samples")
    parser.add_argument("--limits", help="shared calibration table (JSON)")
    parser.add_argument("--output")
    args = parser.parse_args()
    samples = json.loads(Path(args.samples).read_text(encoding="utf-8"))
    limits = json.loads(Path(args.limits).read_text(encoding="utf-8")) if args.limits else LIMITS
    result = evaluate(samples, limits)
    answer = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(answer+"\n", encoding="utf-8")
    print(answer)
