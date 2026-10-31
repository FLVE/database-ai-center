"""Generate credentials; --bootstrap runs only inside the isolated lab network."""

import argparse
import os
import secrets
import time
from pathlib import Path


def initialize(test=False, native_verify=False):
    root = Path(__file__).resolve().parents[1]
    name = "doris-native-verify" if native_verify else ("doris-test" if test else "doris-lab")
    directory = root / "dist" / name
    if directory.is_symlink():
        raise ValueError("credential directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "doris.env"
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid existing Doris credentials")
        print("PASS: existing Doris lab credentials retained")
        return
    env = "DBIC_DORIS_ENABLED=" + ("false" if test else "true") + "\n"
    env += "DBIC_DORIS_DIAG_PASSWORD=" + secrets.token_hex(32) + "\n"
    env += "DBIC_TEST_DORIS_ROOT_PASSWORD=" + secrets.token_hex(32) + "\n"
    if native_verify:
        env += "DBIC_PG_PASSWORD=" + secrets.token_hex(32) + "\n"
        env += "DBIC_API_TOKEN=" + secrets.token_hex(32) + "\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(env)
    print("PASS: generated private Doris lab credentials")


def bootstrap():
    import pymysql

    password = os.environ["DBIC_TEST_DORIS_ROOT_PASSWORD"]
    options = dict(host="doris-fe", port=9030, user="root", connect_timeout=3, read_timeout=5)
    try:
        conn = pymysql.connect(**options, password=password)
    except pymysql.err.OperationalError as exc:
        if exc.args[0] != 1045:
            raise RuntimeError("Doris bootstrap connection failed") from None
        conn = pymysql.connect(**options, password="")
    with conn, conn.cursor() as cursor:
        cursor.execute("SET PASSWORD FOR 'root' = PASSWORD(%s)", (password,))
        cursor.execute("SET PASSWORD FOR 'admin' = PASSWORD(%s)", (password,))
        cursor.execute(
            "CREATE USER IF NOT EXISTS 'diag' IDENTIFIED BY %s",
            (os.environ["DBIC_DORIS_DIAG_PASSWORD"],),
        )
        cursor.execute("SHOW BACKENDS")
        rows = cursor.fetchall()
        if not rows:
            cursor.execute('ALTER SYSTEM ADD BACKEND "doris-be:9050"')
        elif len(rows) != 1 or rows[0][1] != "doris-be":
            raise RuntimeError("refusing to modify unexpected Doris lab topology")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            cursor.execute("SHOW BACKENDS")
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            if len(rows) == 1 and str(rows[0][names.index("Alive")]).lower() == "true":
                print("PASS: Doris lab bootstrap and backend heartbeat")
                return
            time.sleep(1)
        raise RuntimeError("Doris backend heartbeat timed out")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--native-verify", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    if args.bootstrap:
        bootstrap()
    else:
        initialize(args.test, args.native_verify)
