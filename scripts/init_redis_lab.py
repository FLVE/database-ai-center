"""Generate isolated Redis lab ACL/env artifacts; does not contact or change any database."""

import argparse
import hashlib
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize(test=False):
    directory = ROOT / "dist" / ("redis-test" if test else "redis-lab")
    if directory.is_symlink():
        raise ValueError("Redis artifact directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    env_file, acl_file = directory / "redis.env", directory / "users.acl"
    if env_file.exists() or acl_file.exists():
        if not all(p.is_file() and not p.is_symlink() for p in (env_file, acl_file)):
            raise ValueError("incomplete Redis artifacts; inspect existing files before retrying")
        print("PASS: existing Redis artifacts retained: " + str(directory.relative_to(ROOT)))
        return
    password = secrets.token_hex(32)
    hashed = hashlib.sha256(password.encode()).hexdigest()
    acl = "user default off\nuser diag on #" + hashed + " -@all +ping +info +slowlog|get\n"
    env = "DBIC_REDIS_ENABLED=" + ("false" if test else "true") + "\n"
    env += "DBIC_REDIS_DIAG_PASSWORD=" + password + "\n"
    if test:
        admin = secrets.token_hex(32)
        acl += "user fixture on #" + hashlib.sha256(admin.encode()).hexdigest() + " +@all ~* &*\n"
        env += "DBIC_TEST_REDIS_ADMIN_PASSWORD=" + admin + "\n"
    # Plaintext stays in a private env file; Redis only mounts password hashes.
    for path, content, mode in ((env_file, env, 0o600), (acl_file, acl, 0o644)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
    print("PASS: generated private Redis artifacts: " + str(directory.relative_to(ROOT)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="isolated test Redis credentials")
    initialize(parser.parse_args().test)
