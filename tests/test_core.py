"""Numerical equivalence with the original OakesLab AFT_tools (tests/reference)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "reference"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import AFT_tools as AFT  # noqa: E402
from aft_gui import core  # noqa: E402


@pytest.mark.parametrize("ws,ov,n,it,et", [(65, .5, 5, 0, 0), (33, .3, 3, 100, .4), (40, .7, 2, 0, 0)])
def test_matches_original(ws, ov, n, it, et):
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:300, 0:260]
    ang = 0.6 * np.sin(xx / 60)
    im = (100 + 60 * np.sin((xx * np.cos(ang) + yy * np.sin(ang)) / 3)
          + rng.normal(0, 20, xx.shape)).astype(np.uint16)
    _, _, _, _, th, ec = AFT.image_local_order(im, ws, ov, intensity_thresh=it, eccentricity_thresh=et)
    r = core.analyze(im, core.AFTParams(ws, ov, n, it, et))
    assert np.allclose(th, r.theta, equal_nan=True, atol=1e-9)
    assert np.allclose(ec, r.eccentricity, equal_nan=True, atol=1e-9)
    assert np.isclose(np.ravel(AFT.calculate_order_parameter(th, n))[0], r.order_parameter, equal_nan=True)


def test_overlap_fraction():
    assert core.overlapping_neighbour_fraction(0.5, 1) == 1.0
    assert core.overlapping_neighbour_fraction(0.5, 2) == 8 / 24
    assert core.overlapping_neighbour_fraction(0.3, 3) == 1.0
