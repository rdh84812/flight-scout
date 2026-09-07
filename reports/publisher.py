import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _run_git(repo_dir: Path, arguments: List[str], check: bool = True) -> subprocess.CompletedProcess:
    # Background scans must never wait for an account picker or terminal input.
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="false", GCM_GUI_PROMPT="false")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Git 發布操作超過 60 秒；請檢查網路後重試。") from error
    if check and result.returncode:
        detail = (result.stderr or result.stdout).lower()
        if any(word in detail for word in ("credential", "authentication", "username", "interactiv", "terminal prompts", "permission denied")):
            raise RuntimeError(
                "GitHub 驗證失敗；請確認 reports.github_username，並在終端機手動執行 git push origin HEAD:main 完成登入後重試。背景掃描不會開啟登入視窗。"
            )
        if "non-fast-forward" in detail or "fetch first" in detail:
            raise RuntimeError("GitHub 分支有較新的提交；請先同步本機與遠端分支後重試發布。")
        raise RuntimeError(f"Git 操作失敗（結束碼 {result.returncode}）；請在終端機檢查 Git 設定、儲存庫權限與網路。")
    return result


def publish_report_files(
    repo_dir: Path,
    report_paths: Iterable[Path],
    report_date: str,
    remote: str = "origin",
    branch: str = "main",
    github_username: str = "",
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
    diff = _run_git(repo_dir, ["diff", "--cached", "--quiet", "--", *relative_paths], check=False)
    if diff.returncode not in (0, 1):
        raise RuntimeError(diff.stderr.strip() or "Unable to inspect staged report changes")

    if diff.returncode == 1:
        message = f"data: update flight report for {report_date}"
        _run_git(repo_dir, ["commit", "--only", "-m", message, "--", *relative_paths])
    # Retry a previous failed push even when the report itself is unchanged.
    options = ["-c", f"credential.https://github.com.username={github_username}"] if github_username else []
    push = _run_git(repo_dir, [*options, "push", remote, f"HEAD:{branch}"])
    return {
        "published": True,
        "reason": "pushed",
        "commit": _run_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip(),
        "remote_output": (push.stderr or push.stdout).strip(),
    }
