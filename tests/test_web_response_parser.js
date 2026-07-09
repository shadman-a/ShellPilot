const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("web/response.js", "utf8");
const context = { window: {} };
vm.runInNewContext(source, context, { filename: "web/response.js" });
const { readResponse } = context.window.shellPilotResponse;

async function run() {
  const valid = await readResponse({ ok: true, status: 200, statusText: "OK", text: async () => '{"ok":true}' });
  assert.strictEqual(valid.ok, true);
  await assert.rejects(
    readResponse({ ok: true, status: 200, statusText: "OK", text: async () => "<html>proxy error</html>" }),
    /instead of JSON: <html>proxy error<\/html>/,
  );
  await assert.rejects(
    readResponse({ ok: false, status: 502, statusText: "Bad Gateway", text: async () => '{"error":"upstream unavailable"}' }),
    /upstream unavailable/,
  );
  await assert.rejects(
    readResponse({ ok: false, status: 504, statusText: "Gateway Timeout", text: async () => "timeout" }),
    /instead of JSON: timeout/,
  );
}

run().then(() => process.stdout.write("web response parser tests passed\n"));
