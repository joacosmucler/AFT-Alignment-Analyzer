"""Vectorized AFT (Alignment by Fourier Transform).

Numerically equivalent to OakesLab's AFT_tools.image_local_order /
calculate_order_parameter, but processes windows in batches (chunked to
bound memory) and also returns the per-window local order-parameter map.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.fft import fft2, fftshift, ifft2
from skimage.morphology import disk

# Defaults from the AFT paper / reference notebooks (Marcotti et al. 2021)
DEFAULTS = dict(window_size=65, overlap=0.5, neighborhood_radius=5,
                intensity_thresh=0.0, eccentricity_thresh=0.0)


@dataclass
class AFTParams:
    window_size: int = DEFAULTS["window_size"]
    overlap: float = DEFAULTS["overlap"]
    neighborhood_radius: int = DEFAULTS["neighborhood_radius"]
    intensity_thresh: float = DEFAULTS["intensity_thresh"]
    eccentricity_thresh: float = DEFAULTS["eccentricity_thresh"]

    def to_dict(self):
        return asdict(self)


@dataclass
class AFTResult:
    x: np.ndarray            # window centre columns (1D, len = n_cols)
    y: np.ndarray            # window centre rows (1D, len = n_rows)
    theta: np.ndarray        # (n_rows, n_cols) orientation, rad, [-pi/2, pi/2]
    eccentricity: np.ndarray  # (n_rows, n_cols)
    local_order: np.ndarray  # (n_rows, n_cols) per-window order parameter
    order_parameter: float   # median of local_order (AFT image value)
    window_size: int


def _odd(ws: int) -> int:
    ws = int(ws)
    return ws + 1 if ws % 2 == 0 else ws


def window_mask(window_size: int) -> np.ndarray:
    radius = window_size // 2
    m = np.zeros((window_size, window_size))
    m[radius, radius] = 1
    m = cv2.filter2D(m, -1, disk(radius * .5))
    return np.rint(m) == 1


def _local_orientation_batch(wins: np.ndarray, wmask, X, Y, fxy):
    """wins: (N, w, w) float64. Returns theta, eccentricity (N,)."""
    n, w, _ = wins.shape
    v = np.zeros_like(wins)
    v[:, 0, :] = wins[:, 0, :] - wins[:, -1, :]
    v[:, -1, :] = -v[:, 0, :]
    v[:, :, 0] += wins[:, :, 0] - wins[:, :, -1]
    v[:, :, -1] += -wins[:, :, 0] + wins[:, :, -1]
    s = np.real(ifft2(fft2(v, workers=-1) * fxy, workers=-1))
    p = wins - s
    f = np.abs(fftshift(fft2(p, workers=-1), axes=(-2, -1))) * wmask
    M00 = f.sum((1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        xa = (f * X).sum((1, 2)) / M00
        ya = (f * Y).sum((1, 2)) / M00
        mu20 = (f * X * X).sum((1, 2)) / M00 - xa ** 2
        mu02 = (f * Y * Y).sum((1, 2)) / M00 - ya ** 2
        mu11 = (f * X * Y).sum((1, 2)) / M00 - xa * ya
        theta = -0.5 * np.arctan2(2 * mu11, mu20 - mu02)
        root = 0.5 * np.sqrt(4 * mu11 ** 2 + (mu20 - mu02) ** 2)
        l1 = 0.5 * (mu20 + mu02) + root
        l2 = 0.5 * (mu20 + mu02) - root
        ecc = np.sqrt(1 - l2 / l1)
    theta = theta + np.pi / 2
    theta = np.where(theta > np.pi / 2, theta - np.pi, theta)
    return theta, ecc


def local_orientation(im: np.ndarray, window_size=65, overlap=0.5, mask=None,
                      intensity_thresh=0.0, eccentricity_thresh=0.0,
                      chunk=2048, progress=None):
    """Equivalent of AFT_tools.image_local_order for a single 2D image."""
    im = np.asarray(im, dtype=np.float64)
    ws = _odd(window_size)
    r = ws // 2
    step = max(1, int(ws * overlap))
    rpos = np.arange(r, im.shape[0] - r, step)
    cpos = np.arange(r, im.shape[1] - r, step)
    theta = np.full((len(rpos), len(cpos)), np.nan)
    ecc = np.full_like(theta, np.nan)
    if theta.size == 0:
        return cpos, rpos, theta, ecc

    view = sliding_window_view(im, (ws, ws))  # no copy
    rr, cc = np.meshgrid(rpos, cpos, indexing="ij")
    rr, cc = rr.ravel(), cc.ravel()
    valid = np.ones(rr.size, bool) if mask is None else mask.astype(bool)[rr, cc]
    idx = np.flatnonzero(valid)

    wmask = window_mask(ws)
    X, Y = np.meshgrid(np.arange(ws), np.arange(ws))
    fx = np.tile(np.cos(2 * np.pi * np.arange(ws) / ws), (ws, 1))
    fy = fx.T.copy()
    fx[0, 0] = 0
    fxy = 0.5 / (2 - fx - fy)

    th_flat, ec_flat = theta.ravel(), ecc.ravel()
    for k in range(0, idx.size, chunk):
        sel = idx[k:k + chunk]
        wins = view[rr[sel] - r, cc[sel] - r]  # (n, ws, ws) copy of chunk only
        keep = wins.mean((1, 2)) > intensity_thresh
        sel, wins = sel[keep], wins[keep]
        if sel.size:
            t, e = _local_orientation_batch(wins, wmask, X, Y, fxy)
            bad = e < eccentricity_thresh
            t[bad] = np.nan
            e[bad] = np.nan
            th_flat[sel], ec_flat[sel] = t, e
        if progress:
            progress(min(1.0, (k + chunk) / idx.size))
    return cpos, rpos, theta, ecc


def local_order_map(theta: np.ndarray, neighborhood_radius: int) -> np.ndarray:
    """Per-window order parameter (2*<cos^2(dθ)-0.5> over the neighbourhood,
    centre excluded). Border windows (closer than radius to edge) are NaN,
    exactly as in AFT_tools.calculate_order_parameter."""
    n = int(neighborhood_radius)
    H, W = theta.shape
    out = np.full(theta.shape, np.nan)
    if H <= 2 * n or W <= 2 * n:
        return out
    c = theta[n:H - n, n:W - n]
    acc = np.zeros_like(c)
    cnt = np.zeros_like(c)
    for dr in range(-n, n + 1):
        for dc in range(-n, n + 1):
            if dr == 0 and dc == 0:
                continue
            nb = theta[n + dr:H - n + dr, n + dc:W - n + dc]
            val = np.cos(nb - c) ** 2 - 0.5
            ok = ~np.isnan(val)
            acc += np.where(ok, val, 0)
            cnt += ok
    with np.errstate(invalid="ignore", divide="ignore"):
        out[n:H - n, n:W - n] = np.where(cnt > 0, 2 * acc / cnt, np.nan)
    return out


def order_parameter(theta, neighborhood_radius) -> float:
    lo = local_order_map(theta, neighborhood_radius)
    return float(np.nanmedian(lo)) if np.isfinite(lo).any() else np.nan


def analyze(im, p: AFTParams, mask=None, progress=None) -> AFTResult:
    x, y, th, ec = local_orientation(im, p.window_size, p.overlap, mask,
                                     p.intensity_thresh, p.eccentricity_thresh,
                                     progress=progress)
    lo = local_order_map(th, p.neighborhood_radius)
    op = float(np.nanmedian(lo)) if np.isfinite(lo).any() else np.nan
    return AFTResult(x, y, th, ec, lo, op, _odd(p.window_size))


def sweep_windows(shape, min_ws=25, max_ws=None, step=10):
    """Window sizes as in AFT_tools.parameter_search."""
    if max_ws is None:
        max_ws = (max(shape) - 1) // 3
    ws = np.arange(min_ws, max_ws, step)
    ws[ws % 2 == 0] += 1
    return ws


def sweep_image(im, window_sizes, overlap, max_neighborhood=None,
                intensity_thresh=0.0, eccentricity_thresh=0.0, mask=None,
                cancelled=lambda: False):
    """Yields (window_size, neighborhood_radius, order_parameter, n_windows),
    n_windows = windows that contributed a local order value."""
    for ws in window_sizes:
        if cancelled():
            return
        _, _, th, _ = local_orientation(im, ws, overlap, mask,
                                        intensity_thresh, eccentricity_thresh)
        n_max = (max(th.shape) - 1) // 2
        if max_neighborhood:
            n_max = min(n_max, max_neighborhood)
        for n in range(1, n_max + 1):
            lo = local_order_map(th, n)
            ok = np.isfinite(lo)
            yield int(ws), n, (float(np.nanmedian(lo)) if ok.any() else np.nan), int(ok.sum())


def overlapping_neighbour_fraction(overlap, neighborhood_radius):
    """Fraction of the neighbourhood whose windows share pixels with the
    centre window (offset k windows overlaps while k * overlap < 1)."""
    k = min(int(np.ceil(1 / overlap)) - 1, neighborhood_radius)
    return ((2 * k + 1) ** 2 - 1) / ((2 * neighborhood_radius + 1) ** 2 - 1)
