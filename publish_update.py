import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RELEASE_SCRIPT = ROOT / "release_update.py"
RELEASE_PATHS = [
    "plugin.video.hdmozi/addon.xml",
    "repo",
    "index.html",
    "release_update.py",
    "publish_update.py",
    "publish-update.ps1",
]


def run(*args, check=True, capture_output=False):
    return subprocess.run(
        list(args),
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def run_release(version: str | None):
    cmd = ["python", str(RELEASE_SCRIPT)]
    if version:
        cmd.extend(["--version", version])
    run(*cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--message", required=True, help="Git commit message")
    parser.add_argument("--version", help="Explicit target version, e.g. 1.1.3")
    args = parser.parse_args()

    if not (ROOT / ".git").exists():
        raise SystemExit("Ez a mappa nincs git repokent inicializalva.")
    if not (ROOT / "repo").exists():
        raise SystemExit("A repo mappa nem talalhato.")

    run_release(args.version)
    run("git", "add", *RELEASE_PATHS)

    diff = run("git", "diff", "--cached", "--name-only", capture_output=True)
    if not diff.stdout.strip():
        print("Nincs commitolhato release valtozas.")
        return

    run("git", "commit", "-m", args.message)
    run("git", "push", "origin", "main")


if __name__ == "__main__":
    main()
