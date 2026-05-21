const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export const api = {
  connect: (payload) => request("/repos/connect", { method: "POST", body: JSON.stringify(payload) }),
  triage: (owner, repo, payload) => request(`/repos/${owner}/${repo}/triage`, { method: "POST", body: JSON.stringify(payload) }),
  decide: (id, payload) => request(`/approvals/${id}/decision`, { method: "POST", body: JSON.stringify(payload) }),
  post: (id) => request(`/approvals/${id}/post`, { method: "POST" }),
  history: () => request("/history"),
  qa: (payload) => request("/qa", { method: "POST", body: JSON.stringify(payload) }),
};
