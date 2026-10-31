#!/bin/sh
set -eu
# Keep the private host file root-owned while giving mongod only its container-local copy.
cp /run/dbic/keyfile /tmp/dbic-mongodb-keyfile
chown mongodb:mongodb /tmp/dbic-mongodb-keyfile
chmod 600 /tmp/dbic-mongodb-keyfile
exec /usr/local/bin/docker-entrypoint.sh mongod --replSet dbic-rs \
  --keyFile /tmp/dbic-mongodb-keyfile --bind_ip_all --wiredTigerCacheSizeGB 0.25
