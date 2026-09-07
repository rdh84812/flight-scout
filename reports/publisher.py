import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _run_git(repo_dir: Path, arguments: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *arguments],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def publish_report_files(
    repo_dir: Path,
    report_paths: Iterable[Path],
    report_date: str,
    remote: str = "origin",
    branch: str = "main",
) -> Dict[str, Any]:
    """Commit only public report JSON files and push them to GitHub."""
    repo_dir = repo_dir.resolve()
    allowed_dir = (repo_dir / "reports" / "data").resolve()
    relative_paths: List[str] = []
    for path in report_paths:
        resolved = Path(path).resolve()
        try:
            relative = resolved.relative_to(allowed_dir)
        except ValueError as error:
            raise ValueError(f"Report path is outside {allowed_dir}: {resolved}") from error
        if resolved.suffix.lower() != ".json" or len(relative.parts) != 1:
            raise ValueError(f"Only reports/data/*.json can be published: {resolved}")
        relative_paths.append(resolved.relative_to(repo_dir).as_posix())

    if not relative_paths:
        return {"published": False, "reason": "no_report_files"}

    _run_git(repo_dir, ["add", "--", *relative_paths])
    diff = _run_git(repo_dir, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        return {"published": False, "reason": "no_changes"}
    if diff.returncode != 1:
        raise RuntimeError(diff.stderr.strip() or "Unable to inspect staged report changes")

    message = f"data: update flight report for {report_date}"
    _run_git(repo_dir, ["commit", "-m", message])
    push = _run_git(repo_dir, ["push", remote, f"HEAD:{branch}"])
    return {
        "published": True,
        "reason": "pushed",
        "commit": _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip(),
        "remote_output": (push.stderr or push.stdout).strip(),
    }
