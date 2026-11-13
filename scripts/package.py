"""Build the DGHub plugin and browser extension ZIPs for distribution."""

from argparse import ArgumentParser
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
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


def build_engine(directory: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--onedir", "--name", "ojhc-engine", "--distpath", str(directory),
         "--workpath", str(directory / "work"), "--specpath", str(directory),
         "--add-data", f"{ROOT / 'engine' / 'fixtures'}:fixtures",
         "--collect-submodules", "websockets", str(ROOT / "engine" / "ojhc.py")],
        check=True,
    )
    engine = directory / "ojhc-engine"
    if not (engine / "ojhc-engine.exe").is_file():
        raise RuntimeError("Build the DGHub package on Windows.")
    return engine


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as temporary:
        package(output, build_engine(Path(temporary)))


def package(output: Path, engine: Path) -> None:
    for name, (directory, files) in PACKAGES.items():
        source = ROOT / directory
        path = output / name
        entries = {filename: source / filename for filename in files}
        if directory == "starter/ojhc_link":
            entries.update({
                (Path("engine") / file.relative_to(engine)).as_posix(): file
                for file in sorted(engine.rglob("*")) if file.is_file()
            })
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for filename, file in entries.items():
                archive.write(file, arcname=filename)
        with ZipFile(path) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(entries):
                raise RuntimeError(f"Invalid package: {path}")
        print(path)


if __name__ == "__main__":
    main()
