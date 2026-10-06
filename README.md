# AFT Alignment Analyzer

Desktop app for measuring cytoskeletal alignment with **AFT (Alignment by
Fourier Transform)**
([OakesLab](https://github.com/OakesLab/AFT-Alignment_by_Fourier_Transform);
Marcotti et al., *Front. Comput. Sci.* 2021,
[doi:10.3389/fcomp.2021.745831](https://doi.org/10.3389/fcomp.2021.745831)).

You load CZI or TIFF images and assign each one a condition. You then tune the
parameters live or with a parameter sweep. The app gives you the order
parameter for each condition with statistics, plus the alignment map inside
each image.

![icon](aft_gui/icon.png)

---

## Download (no Python needed)

| Platform | File | How to run |
|---|---|---|
| Windows 10/11 (64-bit) | `AFTAnalyzer-windows.zip` | Unzip it and run `AFTAnalyzer\AFTAnalyzer.exe`. The first time, SmartScreen will warn you: click **More info → Run anyway**. |
| macOS (Apple Silicon) | `AFTAnalyzer-macos-arm64.zip` | Unzip it and move `AFTAnalyzer.app` to *Applications*. The first time, **right-click → Open → Open**, because the app is unsigned. |

You can get both files from the repository's **Releases** page, or from
**Actions → Build executables → (latest run) → Artifacts**. To build them
yourself, see [Building the executables](#building-the-executables).

## Install from source

```bash
conda create -n aft python=3.12 -y
conda activate aft
pip install -e .
aft-gui                     # or: python -m aft_gui
```

---

## Workflow

| Tab | What you do |
|---|---|
| **1 · Images** | Add `.czi` or `.tif` files or folders. Drag & drop also works. Choose the **channel** to quantify. Z-stacks are max-projected. Give each image a **Condition**: edit the cell, assign it to the selected rows, or click *All: condition = folder name*. **Replicate** is optional (for example, the differentiation batch). |
| **2 · Tune parameters** | Sliders for window size, overlap, neighbourhood radius, and the intensity and eccentricity thresholds. They start at the paper defaults: window 65 px, overlap 0.5, neighbourhood 5. Image **A** shows the orientation vectors and a map you can switch (local order, orientation or eccentricity). Image **B** is optional: load a sample you expect to look different, to check that the parameters separate the two. |
| **3 · Parameter sweep** *(optional)* | Runs the same search as `AFT_tools.parameter_search`: every combination of window size and neighbourhood radius. It shows the **A − B difference**, the **Mann-Whitney p-value**, or the median order parameter of one condition. Less reliable cells are marked ×. A ★ marks a suggestion. Nothing is applied until you click a cell and confirm. |
| **4 · Results** | Analyses all images. Plots the order parameter by condition as bar, box, violin or points, with one point per image or one per replicate mean. Shows Kruskal-Wallis and pairwise Mann-Whitney / Welch tests. Click an image to see its internal alignment map. |

### Choosing parameters

The sweep tab includes a short guide. In brief:

- **Judge by the difference between conditions, not by the highest value.**
  The order parameter always gets higher with small windows and radius 1, in
  every sample. That brightest corner is mostly noise: the FFT of a small
  window is nearly circular (see the paper).
- **Cells marked ×** have one of two problems:
  - Fewer than *Min windows* windows per image contribute, so the median is
    unstable.
  - More than half of the neighbours share pixels with the centre window.
    Their orientations then agree partly by construction. For example,
    overlap 0.5 with radius 1, or overlap 0.3 with radius ≤ 3.
- **Prefer a stable plateau** over an isolated peak. Choose a window that
  holds several fibres, and check the vectors in tab 2. The paper finds the
  optimal length scale near cell size.
- **Avoid circular selection.** If you pick the parameters that best separate
  the same images you will test, the significance is inflated. Tune on a pilot
  subset or a different replicate, fix the parameters, then analyse everything
  and report the parameters you used.

### Export

**Results → Export results…** writes these files:

| File | Contents |
|---|---|
| `aft_per_image.csv` | One row per image, with the parameters used |
| `aft_per_condition.csv` | Count, mean, SD and median for each condition |
| `aft_per_window.csv` | x, y, orientation, eccentricity and local order for every window |
| `aft_parameters.json`, `aft_stats.txt` | Parameters and statistics |
| `order_parameter.png` / `.svg` | The plot (PNG at 300 dpi) |

**Save project** (Ctrl+S) saves the image list, conditions, channel and
parameters to an `.aft.json` file.

---

## Building the executables

PyInstaller can't cross-compile, so each platform has to be built on that platform.

**On GitHub (both platforms):** push the repository, then go to **Actions →
Build executables → Run workflow**. Pushing a tag such as `v0.2.0` does the
same and attaches the zips to a Release. Each job runs the tests, builds the
app, checks the frozen binary with `--selftest` and makes sure the GUI starts.

**Locally** (Windows → `.exe` folder, macOS → `.app`):

```bash
pip install -r packaging/requirements-build.txt
pyinstaller packaging/AFTAnalyzer.spec --noconfirm
dist/AFTAnalyzer.app/Contents/MacOS/AFTAnalyzer --selftest   # macOS check
```

To regenerate the icons: `python packaging/make_icon.py`.

## Repository layout

```
aft_gui/
  core.py          vectorized AFT (orientation field, local order map, sweep)
  imageio.py       CZI / TIFF reading → 2D plane per channel
  common.py        project state, worker thread, image + map viewer
  app.py           main window
  tab_data.py      1 · images, channels, conditions
  tab_tune.py      2 · interactive tuning
  tab_sweep.py     3 · parameter sweep
  tab_results.py   4 · batch analysis, plot, stats, export
packaging/         PyInstaller spec, build requirements, icons
tests/             equivalence tests against the original AFT_tools (tests/reference)
.github/workflows/ CI build for Windows + macOS
```

## Implementation notes

- `aft_gui/core.py` is a vectorized rewrite of `AFT_tools.image_local_order`
  and `calculate_order_parameter`. It processes windows in batches and in
  chunks, so memory stays bounded. It reproduces the original's orientation,
  eccentricity and order parameter to 1e-9 (`pytest tests`) and also returns
  the order parameter of every window.
- *Overlap* uses the AFT convention: windows are `window_size × overlap`
  apart, so a smaller value gives a denser grid.
- Windows closer to the image edge than the neighbourhood radius get no local
  order value. This matches the original.

## License

MIT. The algorithm and the reference implementation are © OakesLab (MIT).
Please cite Marcotti et al. 2021 when you use this tool.
