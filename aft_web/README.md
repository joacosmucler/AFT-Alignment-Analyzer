# AFT web app (Streamlit)

Compute runs on the machine hosting the app; the interface is a browser.

```bash
pip install -e . --no-deps
pip install streamlit plotly numpy scipy pandas matplotlib scikit-image opencv-python-headless tifffile aicspylibczi
export OMP_NUM_THREADS=2
streamlit run aft_web/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Then from your computer: `ssh -L 8501:localhost:8501 user@host` and open http://localhost:8501
