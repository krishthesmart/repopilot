const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8001/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    try {
      throw new Error(JSON.parse(body).detail || body);
    } catch {
      throw new Error(body);
    }
  }
  return response.json();
}

async function safeRequest(path, options = {}) {
  try {
    return await request(path, options);
  } catch (err) {
    if (err instanceof TypeError && err.message === "Failed to fetch") {
      throw new Error(`Cannot reach RepoPilot backend at ${API_URL}. Make sure uvicorn is running on the same port.`);
    }
    throw err;
  }
}

export const api = {
  auto: (payload) => safeRequest("/autonomous/run", { method: "POST", body: JSON.stringify(payload) }),
  repos: (payload) => safeRequest("/repos/list", { method: "POST", body: JSON.stringify(payload) }),
  connect: (payload) => safeRequest("/repos/connect", { method: "POST", body: JSON.stringify(payload) }),
  triage: (owner, repo, payload) => safeRequest(`/repos/${owner}/${repo}/triage`, { method: "POST", body: JSON.stringify(payload) }),
  decide: (id, payload) => safeRequest(`/approvals/${id}/decision`, { method: "POST", body: JSON.stringify(payload) }),
  post: (id, token) => safeRequest(`/approvals/${id}/post${token ? `?token=${encodeURIComponent(token)}` : ""}`, { method: "POST" }),
  history: () => safeRequest("/history"),
  qa: (payload) => safeRequest("/qa", { method: "POST", body: JSON.stringify(payload) }),
};
