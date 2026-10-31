const admin = db.getSiblingDB("admin");
if (!process.env.DBIC_MONGODB_DIAG_PASSWORD) throw new Error("diagnostic password missing");
admin.createRole({role: "dbicObserver", privileges: [
  {resource: {cluster: true}, actions: ["inprog", "replSetGetStatus"]}
], roles: []});
admin.createUser({user: "diag", pwd: process.env.DBIC_MONGODB_DIAG_PASSWORD,
  roles: [{role: "dbicObserver", db: "admin"}]});
