"""Make the approved Kimodo sleep clip quiet, preserving its sleeping pose.

Run once on an original generated .vrma, not on the result of a prior run.
The body renderer provides subtle breathing separately, so a sleep clip does
not need the source generator's large limb and head movements.
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d


def quiet_sleep(source: Path, destination: Path):
    raw = bytearray(source.read_bytes())
    if raw[:4] != b'glTF':
        raise ValueError('not glb')
    json_length, = struct.unpack_from('<I', raw, 12)
    doc = json.loads(raw[20:20 + json_length])
    data_start = 20 + json_length + 8

    def floats(accessor_index):
        accessor = doc['accessors'][accessor_index]
        view = doc['bufferViews'][accessor['bufferView']]
        width = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4}[accessor['type']]
        offset = data_start + view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
        return np.ndarray((accessor['count'], width), '<f4', raw, offset=offset)

    for channel in doc['animations'][0]['channels']:
        sampler = doc['animations'][0]['samplers'][channel['sampler']]
        track = floats(sampler['output'])
        if channel['target']['path'] == 'rotation':
            # Align the double-covering quaternions, then suppress rapid motions
            # and most of the larger movement. Smooth across the loop seam.
            aligned = track.astype('f8').copy()
            aligned[np.einsum('ij,j->i', aligned, aligned[0]) < 0] *= -1
            smooth = gaussian_filter1d(aligned, sigma=12, axis=0, mode='wrap')
            smooth /= np.linalg.norm(smooth, axis=1, keepdims=True)
            centre = np.mean(smooth, axis=0)
            centre /= np.linalg.norm(centre)
            result = centre + .2 * (smooth - centre)
            result /= np.linalg.norm(result, axis=1, keepdims=True)
            result[-1] = result[0]
            # A wildly turning joint cannot become a subtle sleeping motion
            # merely by smoothing. Leave that joint at its representative pose.
            dot = np.abs(np.sum(result[:-1] * result[1:], axis=1))
            if np.max(2 * np.arccos(np.clip(dot, -1, 1))) > np.deg2rad(.5):
                result[:] = centre
            track[:] = result
        elif channel['target']['path'] == 'translation':
            smooth = gaussian_filter1d(track.astype('f8'), sigma=12, axis=0, mode='wrap')
            result = np.mean(smooth, axis=0) + .2 * (smooth - np.mean(smooth, axis=0))
            result[-1] = result[0]
            track[:] = result
    destination.write_bytes(raw)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: quiet_sleep.py ORIGINAL.vrma OUT.vrma')
    quiet_sleep(Path(sys.argv[1]), Path(sys.argv[2]))
