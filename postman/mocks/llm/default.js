const http = require("http");

// These models select local test scenarios, never real provider models.
http.createServer(async (req, res) => {
  const send = (status, body) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
    return send(501, { error: "Unmodeled route" });
  }
  try {
    const body = req.body;
    if (!body || !body.tools || body.tools[0].function.name !== "propose_checks" ||
        body.tool_choice.function.name !== "propose_checks" ||
        body.parallel_tool_calls !== false || body.store !== false) {
      return send(400, { error: "Invalid function-call contract" });
    }
    const context = JSON.parse(body.messages[1].content);
    const source = context.evidence[0];
    if (!source || Object.keys(source).some(key =>
      !["evidence_id", "source", "status", "kind", "has_limitations"].includes(key))) {
      return send(400, { error: "Unexpected evidence fields" });
    }
    if (["401", "429", "503"].includes(body.model)) {
      return send(Number(body.model), { error: "fixture-upstream-secret" });
    }
    if (body.model === "timeout") await new Promise(resolve => setTimeout(resolve, 1000));
    const item = {
      tool_id: "mysql.lock_chain", instance_id: "mysql-lab-01", reason: "refresh_snapshot",
      evidence_ids: [body.model === "injection" ? "00000000-0000-0000-0000-000000000000" : source.evidence_id]
    };
    return send(200, { choices: [{ finish_reason: "tool_calls", message: {
      role: "assistant", content: null, tool_calls: [{ id: "fixture", type: "function",
        function: { name: "propose_checks", arguments: JSON.stringify({ suggestions: [item] }) }
      }]
    }}] });
  } catch (err) {
    return send(400, { error: "Invalid mock request shape" });
  }
}).listen(process.env.PORT, "127.0.0.1");
