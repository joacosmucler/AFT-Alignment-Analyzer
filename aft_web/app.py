"""Streamlit front end for AFT: compute runs where the app runs (e.g. a cluster),
the interface is a browser.   streamlit run aft_web/app.py
Images are read from paths on the server, so nothing is uploaded.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from scipy.stats import kruskal, mannwhitneyu, ttest_ind

from aft_gui import core, imageio

st.set_page_config(page_title="AFT Alignment Analyzer", layout="wide")


# ---------------------------------------------------------------- compute
def _analyze_one(args):
    """Top-level (picklable) worker: one image -> summary row + per-window rows."""
    path, channel, pdict = args
    p = core.AFTParams(**pdict)
    r = core.analyze(imageio.get_plane(path, channel), p)
    ok = np.isfinite(r.local_order)
    yy, xx = np.meshgrid(r.y, r.x, indexing="ij")
    win = pd.DataFrame(dict(x=xx[ok], y=yy[ok], theta=r.theta[ok],
                            eccentricity=r.eccentricity[ok], local_order=r.local_order[ok]))
    return path, r.order_parameter, int(ok.sum()), win


@st.cache_data(show_spinner=False, max_entries=8)
def analyze_cached(path, channel, pkey):
    p = core.AFTParams(**dict(pkey))
    return core.analyze(imageio.get_plane(path, channel), p)


@st.cache_data(show_spinner=False, max_entries=8)
def plane_cached(path, channel):
    return imageio.get_plane(path, channel)


def stats_text(groups):
    g = {k: v[np.isfinite(v)] for k, v in groups.items() if np.isfinite(v).sum() > 0}
    keys, lines = list(g), []
    for k in keys:
        v = g[k]
        sd = v.std(ddof=1) if len(v) > 1 else 0
        lines.append(f"{k}: n={len(v)}, mean={v.mean():.3f} ± {sd:.3f} SD, median={np.median(v):.3f}")
    if len(keys) >= 3 and all(len(g[k]) > 1 for k in keys):
        lines.append(f"\nKruskal-Wallis: p = {kruskal(*g.values()).pvalue:.3g}")
    if len(keys) >= 2:
        lines.append("\nPairwise Mann-Whitney U (two-sided, uncorrected) | Welch t-test:")
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = g[keys[i]], g[keys[j]]
                pm = mannwhitneyu(a, b).pvalue
                pt = ttest_ind(a, b, equal_var=False).pvalue if len(a) > 1 and len(b) > 1 else np.nan
                lines.append(f"  {keys[i]} vs {keys[j]}: p_MW = {pm:.3g} | p_t = {pt:.3g}")
    return "\n".join(lines)


# ---------------------------------------------------------------- sidebar
ss = st.session_state
ss.setdefault("table", pd.DataFrame(columns=["include", "path", "condition", "replicate"]))
ss.setdefault("results", None)

with st.sidebar:
    st.title("AFT Analyzer")
    st.caption("Parameters (defaults from the AFT paper)")
    d = core.DEFAULTS
    window = st.slider("Window size (px)", 15, 301, d["window_size"], 2)
    overlap = st.slider("Overlap", 0.1, 0.9, d["overlap"], 0.05)
    nbr = st.slider("Neighbourhood radius", 1, 15, d["neighborhood_radius"])
    ithr = st.number_input("Intensity threshold", 0.0, value=float(d["intensity_thresh"]))
    ethr = st.slider("Eccentricity threshold", 0.0, 1.0, float(d["eccentricity_thresh"]), 0.01)
    workers = st.number_input("CPU workers (batch)", 1, os.cpu_count() or 1,
                              min(4, os.cpu_count() or 1),
                              help="Shared machine: keep this modest.")
    params = core.AFTParams(window, overlap, nbr, ithr, ethr)
    pkey = tuple(sorted(params.to_dict().items()))

tab_img, tab_tune, tab_res = st.tabs(["1 · Images", "2 · Tune parameters", "3 · Results"])

# ---------------------------------------------------------------- tab 1
with tab_img:
    st.markdown("Folder **on the server** (e.g. on `/CEPH/...`). Subfolders are searched; "
                "the default condition is the image's folder name.")
    c1, c2 = st.columns([4, 1])
    folder = c1.text_input("Folder path", value=ss.get("folder", ""), placeholder="/CEPH/.../images")
    channel = c2.number_input("Channel (0-based)", 0, 20, ss.get("channel", 0))
    ss["channel"] = int(channel)
    if st.button("Scan folder", type="primary"):
        if not os.path.isdir(folder):
            st.error("Not a directory on the server.")
        else:
            ss["folder"] = folder
            files = imageio.find_images(folder)
            ss["table"] = pd.DataFrame(dict(
                include=True, path=files,
                condition=[os.path.basename(os.path.dirname(f)) for f in files],
                replicate=""))
            ss["results"] = None
    if len(ss["table"]):
        st.caption("Edit the condition / replicate cells; untick to exclude an image.")
        ss["table"] = st.data_editor(ss["table"], width="stretch", hide_index=True,
                                     disabled=["path"], key="editor")
        st.write(f"{int(ss['table']['include'].sum())} of {len(ss['table'])} images included")

# ---------------------------------------------------------------- tab 2
with tab_tune:
    t = ss["table"]
    if not len(t):
        st.info("Scan a folder in tab 1 first.")
    else:
        labels = {os.path.relpath(f, ss.get("folder", "")): f for f in t["path"]}
        path = labels[st.selectbox("Image", list(labels))]
        view = st.radio("Map", ["Local order", "Orientation (°)", "Eccentricity", "None"], horizontal=True)
        with st.spinner("Computing…"):
            im = plane_cached(path, ss["channel"])
            r = analyze_cached(path, ss["channel"], pkey)
        m1, m2, m3 = st.columns(3)
        m1.metric("Order parameter", f"{r.order_parameter:.3f}")
        m2.metric("Windows", f"{r.theta.shape[1]}×{r.theta.shape[0]}")
        m3.metric("Valid", f"{int(np.isfinite(r.local_order).sum())}/{r.theta.size}")

        fig, ax = plt.subplots(figsize=(9, 9 * im.shape[0] / im.shape[1]))
        lo, hi = np.percentile(im, [1, 99.7])
        ax.imshow(im, cmap="gray", vmin=lo, vmax=hi, interpolation="nearest")
        step = max(1, int(core._odd(window) * overlap))
        ext = (r.x[0] - step / 2, r.x[-1] + step / 2, r.y[-1] + step / 2, r.y[0] - step / 2) if len(r.x) and len(r.y) else None
        mp = {"Local order": (r.local_order, (-1, 1), "viridis"),
              "Orientation (°)": (np.degrees(r.theta), (-90, 90), "hsv"),
              "Eccentricity": (r.eccentricity, (0, 1), "magma")}.get(view)
        if mp is not None and ext:
            ax.imshow(mp[0], extent=ext, vmin=mp[1][0], vmax=mp[1][1], cmap=mp[2],
                      alpha=0.45, interpolation="nearest")
        yy, xx = np.meshgrid(r.y, r.x, indexing="ij")
        ok = np.isfinite(r.theta)
        L = step * 0.45
        dx, dy = np.cos(r.theta[ok]) * L, -np.sin(r.theta[ok]) * L
        ax.plot(np.stack([xx[ok] - dx, xx[ok] + dx]), np.stack([yy[ok] - dy, yy[ok] + dy]),
                color="yellow", lw=0.8)
        ax.set_axis_off()
        st.pyplot(fig, width="stretch")
        plt.close(fig)

# ---------------------------------------------------------------- tab 3
with tab_res:
    t = ss["table"]
    if not len(t):
        st.info("Scan a folder in tab 1 first.")
    else:
        sel = t[t["include"]]
        if st.button(f"Analyze {len(sel)} images with current parameters", type="primary"):
            jobs = [(p_, ss["channel"], params.to_dict()) for p_ in sel["path"]]
            bar, rows, wins, t0 = st.progress(0.0), {}, [], time.time()
            with ProcessPoolExecutor(max_workers=int(workers)) as ex:
                for i, (p_, op, nwin, w) in enumerate(ex.map(_analyze_one, jobs), 1):
                    rows[p_] = (op, nwin)
                    w.insert(0, "image", os.path.basename(p_))
                    wins.append(w)
                    bar.progress(i / len(jobs), f"{i}/{len(jobs)} · {time.time() - t0:.0f}s")
            df = sel.copy()
            df["image"] = df["path"].map(os.path.basename)
            df["order_parameter"] = df["path"].map(lambda k: rows[k][0])
            df["n_windows"] = df["path"].map(lambda k: rows[k][1])
            for k, v in params.to_dict().items():
                df[k] = v
            ss["results"] = dict(df=df.drop(columns="include"), windows=pd.concat(wins),
                                 params=params.to_dict())
        res = ss["results"]
        if res is not None:
            df = res["df"]
            st.caption("Parameters used: " + ", ".join(f"{k}={v}" for k, v in res["params"].items()))
            kind = st.radio("Plot", ["box", "violin", "points"], horizontal=True)
            by_rep = st.checkbox("One point per replicate mean (needs replicate labels)")
            pdf = df
            if by_rep and (df["replicate"].astype(str) != "").any():
                pdf = df.groupby(["condition", "replicate"], as_index=False)["order_parameter"].mean()
            args = dict(x="condition", y="order_parameter")
            if kind == "box":
                fg = px.box(pdf, points="all", **args)
            elif kind == "violin":
                fg = px.violin(pdf, points="all", box=True, **args)
            else:
                fg = px.strip(pdf, **args)
            fg.update_layout(yaxis_title="Order parameter", xaxis_title="")
            st.plotly_chart(fg, width="stretch")
            st.code(stats_text({c: g["order_parameter"].to_numpy() for c, g in pdf.groupby("condition")}))
            st.dataframe(df.drop(columns="path"), width="stretch", hide_index=True)

            per_cond = df.groupby("condition")["order_parameter"].agg(["count", "mean", "std", "median"]).reset_index()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("aft_per_image.csv", df.to_csv(index=False))
                z.writestr("aft_per_condition.csv", per_cond.to_csv(index=False))
                z.writestr("aft_per_window.csv", res["windows"].to_csv(index=False))
                z.writestr("aft_parameters.json", pd.Series(res["params"]).to_json())
            st.download_button("Download results (zip)", buf.getvalue(), "aft_results.zip", "application/zip")
