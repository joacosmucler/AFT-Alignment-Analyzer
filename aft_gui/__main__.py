"""Entry point: python -m aft_gui   (or the frozen AFTAnalyzer executable)

    --selftest [image ...]   run the analysis headless (synthetic image, plus any
                             .czi/.tif given) and exit; used to check builds.
"""

import sys


def selftest(paths):
    import numpy as np
    import aicspylibczi  # noqa: F401  (CZI support must be bundled)
    from aft_gui import core, imageio

    yy, xx = np.mgrid[0:400, 0:400]
    im = 100 + 60 * np.sin((xx * np.cos(0.5) + yy * np.sin(0.5)) / 3)
    op = core.analyze(im, core.AFTParams()).order_parameter
    print(f"synthetic aligned image: order parameter {op:.3f}")
    ok = op > 0.9
    for p in paths:
        planes, names = imageio.read_image(p)
        r = core.analyze(planes[0], core.AFTParams())
        print(f"{p}: channels {names}, order parameter {r.order_parameter:.3f}")
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        sys.exit(selftest(sys.argv[i + 1:]))

    from PyQt6.QtWidgets import QApplication
    from aft_gui.app import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("AFT Alignment Analyzer")
    app.setOrganizationName("AFT Alignment Analyzer")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
