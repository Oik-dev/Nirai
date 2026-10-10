"""D0 candidate 019: keep the active recline and the constrained sleep endpoint.

The original 120 frames spend most of their second half nearly motionless, but
the last five frames reach the sleep constraint. Removing that entire tail
would lose the required endpoint. Keep the moving first 49 frames, sparse
samples of the quiet interval, and the final five constraint frames; retime
to half speed so the 1.6 s active section lasts 3.2 s and the clip 4.0 s.

Usage: python finalize_recline.py <candidate-019.npz> <soma77.json> <out.vrma>
"""
import argparse
import json
from pathlib import Path

import numpy as np

from npz_to_vrma import convert_arrays


SOURCE_FRAMES = tuple(range(49)) + (50, 55, 65, 80, 95, 107, 111, 115, 116, 117, 118, 119)


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

    # The converter calculates key times from fps. Reindexing + 15fps yields
    # twice the real-time duration while preserving both endpoint poses.
    retimed = {**skeleton, "fps": 15}
    vrma, info = convert_arrays(arrays, retimed, label="寝転ぶ")
    out.write_bytes(vrma)
    return {**info, "duration_s": (len(SOURCE_FRAMES) - 1) / 15,
            "source_frames": len(rotation), "last_source_frame": SOURCE_FRAMES[-1]}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("skeleton", type=Path)
    cli.add_argument("out", type=Path)
    args = cli.parse_args()
    print(json.dumps(finalize(args.source, args.skeleton, args.out), ensure_ascii=False))
