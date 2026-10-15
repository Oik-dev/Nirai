"""Audit normalized VRM joint ranges for all recorded clips without exporting avatar data.

Run: python joint_audit.py <local-samples-directory>
Prints a JSON summary. Never stores body-derived samples in the Git repository.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from body_model import RANGES

def audit(folder: Path):
    samples = sorted(folder.glob("*.samples.json"))
    if not samples:
        raise ValueError("no local samples found")
    summary = {"files":len(samples),"bones":{},"violations":[]}
    for file in samples:
        data = json.loads(file.read_text(encoding="utf8"))
        if "tpose" not in data:
            raise ValueError(f"no normalized T-pose: {file.name}")
        for name, definition in RANGES["joints"].items():
            if name not in data["bones"] or definition.get("free"):
                continue
            kinds = ("bend", "offAxis", "twist")
            axes = [definition[k]["axis"] for k in kinds]
            sequence = "".join(a[-1].upper() for a in axes)
            quats = np.asarray(data["bones"][name]["rotation"])
            angles = Rotation.from_quat(quats).as_euler(sequence, degrees=True)
            angles *= np.array([-1 if a.startswith("-") else 1 for a in axes])
            entry = summary["bones"].setdefault(name, {k: {"min":180,"max":-180} for k in kinds})
            for index, key in enumerate(kinds):
                values = angles[:,index]
                entry[key]["min"] = round(min(entry[key]["min"],float(values.min())),4)
                entry[key]["max"] = round(max(entry[key]["max"],float(values.max())),4)
                lo,hi = definition[key].get("min"),definition[key].get("max")
                if lo is None or hi is None:
                    continue
                bad = np.flatnonzero((values < lo-1e-4)|(values > hi+1e-4))
                if len(bad):
                    summary["violations"].append({
                        "file":file.name,"bone":name,"component":key,
                        "frames":bad.tolist(),"measured":[round(float(values.min()),4),
                                                               round(float(values.max()),4)],
                        "bounds":[lo,hi]})
    return summary

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("samples",type=Path)
    args=parser.parse_args()
    result=audit(args.samples)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result["violations"]:
        raise SystemExit(2)
