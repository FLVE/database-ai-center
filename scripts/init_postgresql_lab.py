"""Generate private credentials for the separate PostgreSQL diagnostic lab."""

import argparse
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize(test=False):
    directory = ROOT / "dist" / ("postgresql-test" if test else "postgresql-lab")
    if directory.is_symlink():
        raise ValueError("lab credential directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "postgresql.env"
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid existing PostgreSQL credential artifact")
        print("PASS: existing PostgreSQL lab credentials retained")
        return
    env = "DBIC_POSTGRESQL_ENABLED=" + ("false" if test else "true") + "\n"
    env += "DBIC_POSTGRESQL_DIAG_PASSWORD=" + secrets.token_hex(32) + "\n"
    env += "POSTGRES_PASSWORD=" + secrets.token_hex(32) + "\n"
    env += "POSTGRES_USER=lab_admin\nPOSTGRES_DB=dbic_lab\n"
    env += "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(env)
    print("PASS: generated private PostgreSQL lab credentials")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    initialize(parser.parse_args().test)
