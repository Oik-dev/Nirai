"""Real local Kimodo backend; loads only after the standalone preflight passes.

No model is loaded by importing this module. The HTTP layer calls load() once
in a background thread, then generate() for one request at a time.
"""
from __future__ import annotations

from pathlib import Path

from preflight import pin_text_models


class _TextEncoder:
    """Adapter keeping the CPU LLM2Vec encoder out of Kimodo's GPU module tree."""
    llm_dim = 4096

    def __init__(self, encoder):
        self.encoder = encoder

    def __call__(self, texts):
        return self.encoder(texts)


class KimodoBackend:
    def __init__(self, model, torch, skeleton: dict, fps: float, steps: int):
        self.model = model
        self.torch = torch
        self.skeleton = skeleton
        self.fps = fps
        self.steps = steps

    @classmethod
    def load(cls, paths: dict[str, Path], hf_home: Path, *, steps: int = 100, threads: int = 4):
        """Call only after local_models, capacity and offline_only have succeeded."""
        if not 1 <= steps <= 1000 or not 1 <= threads <= 32:
            raise RuntimeError("Invalid generation configuration")
        pin_text_models(paths, hf_home)
        import torch
        from kimodo import load_model
        from kimodo.model.llm2vec import LLM2VecEncoder

        torch.set_num_threads(threads)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        torch.set_float32_matmul_precision("highest")
        torch.backends.cuda.matmul.allow_tf32 = False

        encoder = LLM2VecEncoder(
            str(paths["mntp"]), str(paths["supervised"]),
            dtype="bfloat16", llm_dim=4096, device="cpu",
        )
        if any(parameter.device.type != "cpu" for parameter in encoder.model.parameters()):
            raise RuntimeError("Text encoder was not kept on CPU")
        if not any(parameter.dtype == torch.bfloat16 for parameter in encoder.model.parameters()):
            raise RuntimeError("Text encoder was not loaded in BF16")

        adapter = _TextEncoder(encoder)
        model = load_model("kimodo-soma-rp-v1.1", device="cuda:0", text_encoder=adapter).float().eval()
        if model.text_encoder is not adapter:
            raise RuntimeError("Kimodo did not use the approved local text encoder")
        if any(parameter.dtype != torch.float32 for parameter in model.parameters()):
            raise RuntimeError("Kimodo did not use FP32 parameters")
        skeleton = model.output_skeleton
        names = list(skeleton.bone_order_names)
        if skeleton.name != "somaskel77" or len(names) != 77:
            raise RuntimeError("Unexpected motion skeleton")
        description = {
            "joint_names": names,
            "parents": skeleton.joint_parents.detach().cpu().tolist(),
            "neutral_joints_m": skeleton.neutral_joints.detach().cpu().tolist(),
            "fps": float(model.fps),
        }
        return cls(model, torch, description, float(model.fps), steps)

    def generate_arrays(self, text: str, seconds: float, seed: int, *, constraints=None):
        """Generate one set of motion arrays; private constraints stay internal."""
        import numpy as np
        from kimodo.tools import seed_everything

        frames = int(seconds * self.fps)
        seed_everything(seed % (2**32))
        with self.torch.inference_mode():
            result = self.model(
                text, frames, num_denoising_steps=self.steps, num_samples=1,
                multi_prompt=False, constraint_lst=constraints if constraints is not None else [], post_processing=False,
                return_numpy=True, cfg_type="separated", cfg_weight=[2.0, 2.0],
            )
        if result["posed_joints"].shape != (1, frames, 77, 3):
            raise RuntimeError("Unexpected generated motion dimensions")
        if any(not np.isfinite(value).all() for value in result.values() if isinstance(value, np.ndarray)):
            raise RuntimeError("Generated motion is not finite")
        return result

    def generate(self, text: str, seconds: float, seed: int) -> bytes:
        """Public HTTP path: generate and convert without saving the prompt."""
        from npz_to_vrma import convert_arrays

        result = self.generate_arrays(text, seconds, seed)
        arrays = {
            "global_rot_mats": result["global_rot_mats"][0],
            "root_positions": result["root_positions"][0],
        }
        vrma, _ = convert_arrays(arrays, self.skeleton, label="motion")
        return vrma
