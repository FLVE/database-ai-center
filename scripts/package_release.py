"""Build a source-only deployment archive without local credentials or environments."""

import argparse
import hashlib
import tarfile
from pathlib import Path

ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "alembic.ini",
    ".python-version",
    ".dockerignore",
    ".gitignore",
    ".env.example",
}
TREES = {"src", "scripts", "tests", "migrations", "configs", "deploy", "docs", "postman"}
SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".mako", ".ini"}


def release_files(root):
    for name in sorted(ROOT_FILES):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"release input missing or symlink: {name}")
        yield path
    for tree in sorted(TREES):
        for path in sorted((root / tree).rglob("*")):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise ValueError(f"release symlink forbidden: {relative}")
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            if path.is_file() and (
                path.suffix in SUFFIXES
                or relative == Path("deploy/Dockerfile")
                or (relative.parts[0] == "postman" and path.suffix == ".js")
            ):
                yield path


def package(root, output):
    paths = list(release_files(root))
    if output.resolve() in {path.resolve() for path in paths}:
        raise ValueError("archive must not overwrite a release input")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a rerun replacing an artifact already handed to an operator.
    with output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path in paths:
            info = archive.gettarinfo(str(path), arcname=str(path.relative_to(root)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with path.open("rb") as source:
                archive.addfile(info, source)
    return len(paths), hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    count, digest = package(Path(__file__).resolve().parents[1], args.output)
    print(f"PASS: {count} source files packaged: {args.output}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
