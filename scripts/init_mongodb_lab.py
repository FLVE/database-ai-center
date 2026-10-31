"""Generate private credentials for new MongoDB lab containers; never contacts a database."""

import argparse
import base64
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize(test=False):
    directory = ROOT / "dist" / ("mongodb-test" if test else "mongodb-lab")
    if directory.is_symlink():
        raise ValueError("MongoDB artifact directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = (directory / "mongodb.env", directory / "keyfile")
    if any(p.exists() or p.is_symlink() for p in files):
        if not all(p.is_file() and not p.is_symlink() for p in files):
            raise ValueError("incomplete MongoDB artifacts; inspect before retrying")
        print("PASS: existing MongoDB artifacts retained: " + str(directory.relative_to(ROOT)))
        return
    env = "DBIC_MONGODB_ENABLED=" + ("false" if test else "true") + "\n"
    env += "DBIC_MONGODB_DIAG_PASSWORD=" + secrets.token_hex(32) + "\n"
    env += "MONGO_INITDB_ROOT_USERNAME=bootstrap\n"
    env += "MONGO_INITDB_ROOT_PASSWORD=" + secrets.token_hex(32) + "\n"
    key = base64.b64encode(secrets.token_bytes(512)).decode() + "\n"
    for path, content in zip(files, (env, key), strict=True):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
    print("PASS: generated private MongoDB artifacts: " + str(directory.relative_to(ROOT)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    initialize(parser.parse_args().test)
