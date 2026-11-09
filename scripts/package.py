"""Build the DGHub plugin and browser extension ZIPs for distribution."""

from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "algorithmcontestpp-dghub.zip": (
        "starter/ojhc_link",
        ("manifest.json", "main.py"),
    ),
    "algorithmcontestpp-browser.zip": (
        "extension",
        ("manifest.json", "background.js", "content.js", "popup.html", "popup.js"),
    ),
}


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)

    for name, (directory, files) in PACKAGES.items():
        source = ROOT / directory
        path = output / name
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for filename in files:
                archive.write(source / filename, arcname=filename)
        with ZipFile(path) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(files):
                raise RuntimeError(f"Invalid package: {path}")
        print(path)


if __name__ == "__main__":
    main()
