import argparse
import json
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def build_site(data_dir: Path, web_dir: Path, output_dir: Path) -> Path:
    """Create a self-contained GitHub Pages artifact from web sources and report JSON."""
    required = (
        web_dir / "index.html",
        web_dir / "assets" / "style.css",
        web_dir / "assets" / "app.js",
        data_dir / "latest.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing site input: " + ", ".join(missing))

    payload = json.loads((data_dir / "latest.json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("deals"), list):
        raise ValueError("reports/data/latest.json has an unsupported schema")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(web_dir, output_dir)
    output_data = output_dir / "data"
    output_data.mkdir(parents=True, exist_ok=True)
    for source in data_dir.glob("*.json"):
        shutil.copy2(source, output_data / source.name)
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Flight Scout GitHub Pages site")
    parser.add_argument("--data", type=Path, default=BASE_DIR / "reports" / "data")
    parser.add_argument("--web", type=Path, default=BASE_DIR / "web")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "_site")
    args = parser.parse_args()
    output = build_site(args.data, args.web, args.output)
    print(f"Built GitHub Pages artifact: {output}")


if __name__ == "__main__":
    main()
