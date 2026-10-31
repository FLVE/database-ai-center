const admin = db.getSiblingDB("admin");
if (!admin.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)) {
  throw new Error("bootstrap authentication failed");
}
try {
  const current = admin.runCommand({replSetGetConfig: 1});
  if (current.config._id !== "dbic-rs" || current.config.members.length !== 1 ||
      current.config.members[0].host !== "mongodb-lab:27017") {
    throw new Error("existing replica configuration differs; refusing to reconfigure");
  }
} catch (error) {
  if (error.code !== 94) throw error;
  admin.runCommand({replSetInitiate: {_id: "dbic-rs",
    members: [{_id: 0, host: "mongodb-lab:27017"}]}});
}
let ready = false;
for (let attempt = 0; attempt < 60; attempt++) {
  if (admin.runCommand({hello: 1}).isWritablePrimary === true) { ready = true; break; }
  sleep(1000);
}
if (!ready) throw new Error("lab primary did not become ready");
print("PASS: authenticated single-member dbic-rs ready; existing configuration retained");
