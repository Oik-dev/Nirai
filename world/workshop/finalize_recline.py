"""D0 candidate 019: uniformly retime and fit pose endpoints to window clips.

The active first two seconds are played at half speed (four seconds total).
The original quiet tail is discarded; the last 0.8 seconds joins the exact
sleep-loop start using motion_seams. The raw sleep clip is read from main,
so repeated execution cannot bake its orientation twice.

Usage: python finalize_recline.py <candidate-019.npz> <soma77.json> <out.vrma>
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

from npz_to_vrma import convert_arrays
from npz_to_vrma import position_animation
from motion_seams import fit_endpoints
from vrma import read_tracks, replace_tracks


SOURCE_FRAMES = tuple(range(61))
# Fingerprint of the unrotated clip. A future main merge must not silently
# rotate the already-baked asset again when this command is repeated.
RAW_SLEEP_SHA256 = "7bff03d423a9f1e938c7bca2de47a7c2d9c11c10fcd093cd70d643dabafcfd71"


def finalize(source: Path, skeleton_path: Path, out: Path) -> dict:
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))["skeleton"]
    if float(skeleton["fps"]) != 30:
        raise ValueError("Expected 30fps source skeleton")
    with np.load(source, allow_pickle=False) as input_arrays:
        rotation = input_arrays["global_rot_mats"]
        hips = input_arrays["root_positions"]
        if rotation.shape != (120, 77, 3, 3) or hips.shape != (120, 3):
            raise ValueError("Only the reviewed four-second SOMA77 motion is supported")
        arrays = {"global_rot_mats": rotation[list(SOURCE_FRAMES)],
                  "root_positions": hips[list(SOURCE_FRAMES)]}

    # Every source frame stays equally spaced: no accelerated jumps in the tail.
    retimed = {**skeleton, "fps": 15}
    preliminary, info = convert_arrays(arrays, retimed, label="寝転ぶ")
    rotations, root = read_tracks(preliminary)
    seat_rotations, seat_root = read_tracks(out.with_name("座る.vrma"))

    repository = Path(__file__).resolve().parents[2]
    original_sleep = subprocess.check_output(
        ["git", "show", "main:world/window/assets/motions/眠る.vrma"], cwd=repository)
    if hashlib.sha256(original_sleep).hexdigest() != RAW_SLEEP_SHA256:
        raise ValueError("main does not contain the original unrotated sleep clip; refusing double bake")
    sleep_rotations, sleep_root = read_tracks(original_sleep)
    corrected_sleep, corrected_root = position_animation(
        sleep_rotations, sleep_root, 135.0, root[-1, [0, 2]])
    baked_sleep = replace_tracks(original_sleep, corrected_sleep, corrected_root)

    fitted, fitted_root = fit_endpoints(
        rotations, root, seat_rotations, seat_root, corrected_sleep, corrected_root,
        frames=12)
    vrma = replace_tracks(preliminary, fitted, fitted_root)
    # Construct and validate both before changing either asset.
    read_tracks(baked_sleep)
    read_tracks(vrma)
    out.with_name("眠る.vrma").write_bytes(baked_sleep)
    out.write_bytes(vrma)
    return {**info, "duration_s": (len(SOURCE_FRAMES) - 1) / 15,
            "source_frames": len(rotation), "last_source_frame": SOURCE_FRAMES[-1],
            "sleep_yaw_baked": 135}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("skeleton", type=Path)
    cli.add_argument("out", type=Path)
    args = cli.parse_args()
    print(json.dumps(finalize(args.source, args.skeleton, args.out), ensure_ascii=False))
