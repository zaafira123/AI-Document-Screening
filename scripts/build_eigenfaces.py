"""Rebuild assets/models/eigenfaces.npz (baseline face descriptor).

Trains a whitened PCA ("eigenfaces") on the 100 public LFW face crops that ship
inside scikit-image (skimage.data.lfw_subset).  Run only if you want to
regenerate the file; the repository already contains it.
"""
from pathlib import Path

import cv2
import numpy as np
import skimage.data as data

K = 40
faces = np.array(data.lfw_subset()[:100], dtype=np.float32) * 255.0


def prep(im25):
    im = cv2.GaussianBlur(cv2.resize(im25, (25, 25), interpolation=cv2.INTER_AREA), (3, 3), 0)
    return ((im - im.mean()) / (im.std() + 1e-6)).reshape(-1)


X = np.stack([prep(f) for f in faces] + [prep(f[:, ::-1]) for f in faces])
mu = X.mean(0)
_, S, Vt = np.linalg.svd(X - mu, full_matrices=False)
W = Vt[:K] / (S[:K, None] / np.sqrt(len(X)))
out = Path(__file__).resolve().parents[1] / "assets" / "models" / "eigenfaces.npz"
np.savez_compressed(out, mu=mu.astype(np.float32), W=W.astype(np.float32))
print("wrote", out, W.shape)
