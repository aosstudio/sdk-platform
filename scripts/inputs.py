"""Fetch and validate the pinned build inputs."""

from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest, sha256
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from typing import Literal, NotRequired, TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "locks" / "inputs.json"
OUT = ROOT / "out"
CACHE = OUT / "inputs"


class Input(TypedDict):
    url: str
    sha256: str
    license: str
    role: str
    format: NotRequired[Literal["gitiles-tree", "gitiles-text"]]


class Lock(TypedDict):
    version: str
    revision: int
    android_version: str
    extension_level: str
    snapshots: dict[str, str]
    files: dict[str, Input]


def read_lock() -> Lock:
    return cast(Lock, json.loads(LOCK_PATH.read_bytes()))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def gitiles_tree_digest(path: Path) -> str:
    # Gitiles can change archive timestamps when serving the same tree.
    entries: dict[str, tuple[str, int, str]] = {}
    with tarfile.open(path) as archive:
        for entry in archive:
            if entry.name in entries:
                raise ValueError(f"Duplicate source entry: {entry.name}")
            content = entry.linkname
            stream = archive.extractfile(entry) if entry.isfile() else None
            if stream is not None:
                with stream:
                    content = sha256(stream.read()).hexdigest()
            entries[entry.name] = (entry.type.decode("ascii"), entry.mode, content)
    return sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def fetch(name: str, spec: Input) -> None:
    if Path(name).name != name or name in (".", ".."):
        raise ValueError(f"Invalid input name: {name}")
    url = spec["url"]
    if not url.startswith("https://"):
        raise ValueError(f"An HTTPS URL is required for {name}")
    target = CACHE / name
    checksum = gitiles_tree_digest if spec.get("format") == "gitiles-tree" else digest
    if target.is_file():
        try:
            if checksum(target) == spec["sha256"]:
                return
        except (OSError, tarfile.TarError):
            pass

    with TemporaryDirectory(dir=CACHE) as directory:
        download = Path(directory) / name
        print(f"Fetching {name}", flush=True)
        subprocess.run(
            [
                "curl",
                "--disable",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--retry",
                "3",
                "--connect-timeout",
                "20",
                "--max-time",
                "300",
                "--output",
                download,
                url,
            ],
            check=True,
        )
        if spec.get("format") == "gitiles-text":
            download.write_bytes(b64decode(download.read_bytes()))
        if checksum(download) != spec["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {name}: {url}")
        download.replace(target)


def fetch_all(lock: Lock, *, include_verification: bool = False) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        downloads = [
            pool.submit(fetch, name, spec)
            for name, spec in lock["files"].items()
            if include_verification or spec["role"] != "verification"
        ]
        for download in downloads:
            download.result()


if __name__ == "__main__":
    fetch_all(read_lock(), include_verification=True)
