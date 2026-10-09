"""Save one *unapproved* Kimodo trial, with numerical measurements only.

Inference is injected by the caller. Importing this module never loads a model.
All output paths are numbered: descriptions and feature vectors are never
included in filenames, messages, or persisted result metadata.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np
from scipy.spatial.transform import Rotation

from generator import valid_glb
from npz_to_vrma import convert_arrays
from roll_metrics import pelvis_metrics


def generate_trial(backend, texts: list[str], candidate, out: Path) -> dict:
    """Generate and save a single candidate. The shared VRM gate is separate.

    A caller must prepare all candidates with prepare_recline_trials and verify
    the local feature cache *before* loading the model and invoking this step.
    Each result remains unapproved until look + the shared motion gate pass.
    """
    if (type(candidate.number) is not int or candidate.number < 1
            or not 0 <= candidate.text_index < len(texts)
            or not isinstance(candidate.constraints, tuple)
            or len(candidate.constraints) != 2
            or backend.fps * candidate.seconds != candidate.frames):
        raise ValueError("Invalid candidate")
    out = Path(out)
    stem = f"candidate-{candidate.number:03d}"
    npz_path = out / f"{stem}.npz"
    vrma_path = out / f"{stem}.vrma"
    if npz_path.exists() or vrma_path.exists():
        raise FileExistsError("Candidate number already exists; do not overwrite")

    result = backend.generate_arrays(
        texts[candidate.text_index], candidate.seconds, candidate.seed,
        constraints=list(candidate.constraints),
    )
    frames = candidate.frames
    shapes = {
        "local_rot_mats": (1, frames, 77, 3, 3),
        "global_rot_mats": (1, frames, 77, 3, 3),
        "root_positions": (1, frames, 3),
    }
    arrays = {}
    for name, expected in shapes.items():
        data = np.asarray(result[name])
        if data.shape != expected or not np.issubdtype(data.dtype, np.number) or not np.isfinite(data).all():
            raise ValueError("Generated motion has invalid numeric arrays")
        arrays[name] = data[0]
    end_hips = Rotation.from_rotvec(candidate.constraints[1]["local_joints_rot"][-1, 0]).as_matrix()
    metrics = pelvis_metrics(arrays["local_rot_mats"], fps=backend.fps, target=end_hips)
    vrma, _ = convert_arrays(
        {"global_rot_mats": arrays["global_rot_mats"],
         "root_positions": arrays["root_positions"]},
        backend.skeleton, label=stem,
    )
    if not valid_glb(vrma):
        raise ValueError("Generated VRM animation is invalid")

    out.mkdir(parents=True, exist_ok=True)
    temporary = []
    published = []
    try:
        with tempfile.NamedTemporaryFile(dir=out, suffix=".npz", delete=False) as tmp:
            temporary.append(Path(tmp.name))
            np.savez_compressed(tmp, **arrays)
        with tempfile.NamedTemporaryFile(dir=out, suffix=".vrma", delete=False) as tmp:
            temporary.append(Path(tmp.name))
            tmp.write(vrma)
        temporary[0].replace(npz_path)
        published.append(npz_path)
        temporary[1].replace(vrma_path)
        published.append(vrma_path)
    except BaseException:
        # A failure publishing the second file must not leave a half-candidate.
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    return {"number": candidate.number, "seconds": candidate.seconds,
            "seed": candidate.seed, **metrics}
