"""Streamlit front end for AFT: compute runs where the app runs (e.g. a cluster),
the interface is a browser.   streamlit run aft_web/app.py
Images are read from paths on the server, or dragged in from the user's computer;
uploads are temporary (auto-deleted, see UPLOAD_* settings below).
"""
from __future__ import annotations

import getpass
import io
import os
import shutil
import tempfile
import time
import uuid
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

# ------------------------------------------------- temporary upload storage
# Uploads live in node-local temp space (not the shared home) and are removed by:
#   * the "Clear list" / "delete after analysis" options,
#   * an inactivity TTL (swept on every page run, by any session),
#   * a per-session size quota.
UPLOAD_ROOT = os.environ.get("AFT_UPLOAD_DIR",
                             os.path.join(tempfile.gettempdir(), f"aft_uploads_{getpass.getuser()}"))
TTL_HOURS = float(os.environ.get("AFT_UPLOAD_TTL_HOURS", "3"))
QUOTA_GB = float(os.environ.get("AFT_UPLOAD_QUOTA_GB", "10"))


def _dir_bytes(path):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)


def sweep_uploads():
    """Delete session folders untouched for longer than TTL_HOURS."""
    if not os.path.isdir(UPLOAD_ROOT):
        return
    now = time.time()
    for name in os.listdir(UPLOAD_ROOT):
        d = os.path.join(UPLOAD_ROOT, name)
        try:
            if os.path.isdir(d) and now - os.path.getmtime(d) > TTL_HOURS * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def session_dir():
    return os.path.join(UPLOAD_ROOT, ss["upload_id"]) if ss.get("upload_id") else None


def delete_uploads():
    """Remove this session's uploaded files and their rows from the table."""
    d = session_dir()
    if d:
        shutil.rmtree(d, ignore_errors=True)
    t = ss["table"]
    ss["table"] = t[~t["path"].str.startswith(UPLOAD_ROOT + os.sep)].reset_index(drop=True)
    ss.pop("upload_id", None)


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
ss.setdefault("up_key", 0)
sweep_uploads()
_sd = session_dir()
if _sd and os.path.isdir(_sd):
    os.utime(_sd)                      # keep an active session's files alive
elif len(ss["table"]):                 # files vanished (TTL / manual): drop dead rows
    ss["table"] = ss["table"][ss["table"]["path"].map(os.path.exists)].reset_index(drop=True)

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
    mode = st.radio("Where are the images?", ["On the server (path)", "On my computer (upload)"],
                    horizontal=True)
    channel = st.number_input("Channel (0-based)", 0, 20, ss.get("channel", 0))
    ss["channel"] = int(channel)

    if mode.startswith("On the server"):
        st.markdown("Folder **on the server** (e.g. on `/CEPH/...`). Subfolders are searched; "
                    "the default condition is the image's folder name.")
        folder = st.text_input("Folder path", value=ss.get("folder", ""), placeholder="/CEPH/.../images")
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
    else:
        st.markdown("Drag `.czi` / `.tif` files from your computer into the box. They are kept on the "
                    f"server only temporarily: removed after **{TTL_HOURS:g} h** without activity, "
                    "when you press *Clear list*, or right after analysis if you tick the option in tab 3.")
        cond = st.text_input("Condition for this batch", value="", placeholder="e.g. control")
        ups = st.file_uploader("Images (drag & drop)", type=["czi", "tif", "tiff"],
                               accept_multiple_files=True, key=f"uploader{ss['up_key']}")
        if st.button("Add uploaded files", type="primary", disabled=not ups):
            ss.setdefault("upload_id", f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}")
            root = session_dir()
            used = _dir_bytes(root) if os.path.isdir(root) else 0
            if used + sum(u.size for u in ups) > QUOTA_GB * 1e9:
                st.error(f"Over the {QUOTA_GB:g} GB per-session quota "
                         f"({used / 1e9:.1f} GB already stored). Clear the list or upload fewer files.")
            else:
                dest = os.path.join(root, cond or "uploaded")
                os.makedirs(dest, exist_ok=True)
                new = []
                for u in ups:
                    fp = os.path.join(dest, os.path.basename(u.name))
                    with open(fp, "wb") as fh:
                        fh.write(u.getbuffer())
                    new.append(fp)
                add = pd.DataFrame(dict(include=True, path=new, condition=cond or "uploaded", replicate=""))
                ss["table"] = pd.concat([ss["table"], add]).drop_duplicates("path", ignore_index=True)
                ss["folder"] = root
                ss["results"] = None
                ss["up_key"] += 1      # resets the uploader: frees the copies held in RAM
                st.rerun()
        if session_dir() and os.path.isdir(session_dir()):
            st.caption(f"On the server now: {_dir_bytes(session_dir()) / 1e9:.2f} GB "
                       f"(quota {QUOTA_GB:g} GB)")

    if len(ss["table"]):
        st.caption("Edit the condition / replicate cells; untick to exclude an image.")
        ss["table"] = st.data_editor(ss["table"], width="stretch", hide_index=True,
                                     disabled=["path"], key="editor")
        st.write(f"{int(ss['table']['include'].sum())} of {len(ss['table'])} images included")
        if st.button("Clear list (also deletes uploaded files)"):
            delete_uploads()
            ss["table"] = ss["table"].iloc[0:0]
            ss["results"] = None
            st.rerun()

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
        has_up = bool(session_dir()) and t["path"].str.startswith(UPLOAD_ROOT + os.sep).any()
        del_after = st.checkbox("Delete uploaded files from the server after analysis "
                                "(re-analysing then needs a new upload)", value=False, disabled=not has_up)
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
            if del_after:
                delete_uploads()
                st.toast("Uploaded files deleted from the server.")
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
