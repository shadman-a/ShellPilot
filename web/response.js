window.shellPilotResponse = (() => {
function readResponse(response) {
  return response.text().then((raw) => {
    let data = {};
    if (raw.trim()) {
      try {
        data = JSON.parse(raw);
      } catch {
        const excerpt = raw.replace(/\s+/g, " ").trim().slice(0, 240);
        const suffix = excerpt ? `: ${excerpt}` : "";
        throw new Error(`Server returned ${response.status} ${response.statusText} instead of JSON${suffix}`);
      }
    }
    if (!data || typeof data !== "object") data = {};
    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status} ${response.statusText})`);
    }
    return data;
  });
}

function postJson(path, payload = {}) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(readResponse);
}

function getJson(path) {
  return fetch(path, { cache: "no-store" }).then(readResponse);
}

  return { getJson, postJson, readResponse };
})();
