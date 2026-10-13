# AFT web app (Streamlit)

Compute runs on the machine hosting the app; the interface is a browser.

```bash
pip install -e . --no-deps
pip install streamlit plotly numpy scipy pandas matplotlib scikit-image opencv-python-headless tifffile aicspylibczi
export OMP_NUM_THREADS=2
streamlit run aft_web/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Then from your computer: `ssh -L 8501:localhost:8501 user@host` and open http://localhost:8501

## Images on your own computer (drag & drop)
Tab 1 -> **On my computer (upload)**: drag files into the box. Uploads are temporary and
stored in node-local temp space (`/tmp/aft_uploads_<user>`), not in your shared home:

* deleted after `AFT_UPLOAD_TTL_HOURS` (default 3) without activity (swept on every page run),
* deleted by **Clear list**, or right after analysis (checkbox in tab 3),
* capped at `AFT_UPLOAD_QUOTA_GB` (default 10) per session; limit per file 2 GB
  (`.streamlit/config.toml`, so launch `streamlit run` from the repo root).

Override with env vars before launching, e.g. `AFT_UPLOAD_DIR=/dev/shm/aft` to keep uploads in RAM only.
