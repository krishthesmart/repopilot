import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Bot, Check, GitPullRequest, History, MessageSquare, Send, ShieldCheck, Tags, X } from "lucide-react";
import { api } from "./api";
import "./style.css";

function App() {
  const [form, setForm] = useState({ owner: "demo", repo: "repopilot", github_token: "" });
  const [repos, setRepos] = useState([]);
  const [connected, setConnected] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [history, setHistory] = useState([]);
  const [qa, setQa] = useState({ question: "How are write actions protected?", answer: "" });
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const stats = useMemo(() => ({
    pending: approvals.filter((item) => item.status === "pending").length,
    approved: approvals.filter((item) => item.status === "approved").length,
    posted: approvals.filter((item) => item.status === "posted").length,
  }), [approvals]);

  async function run(fn) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function payload() {
    return { ...form, github_token: form.github_token || null };
  }

  function selectRepo(fullName) {
    const [owner, repo] = fullName.split("/");
    setForm({ ...form, owner, repo });
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><Bot size={22} /> RepoPilot</div>
        <button className="nav active"><MessageSquare size={16} /> Issue triage</button>
        <button className="nav"><GitPullRequest size={16} /> PR review</button>
        <button className="nav"><History size={16} /> Trace history</button>
        <div className="trace">
          <ShieldCheck size={18} />
          <span>LangSmith tracing via <code>LANGSMITH_TRACING=true</code></span>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>AI maintainer copilot</h1>
            <p>Connect a repo, classify issues, review suggestions, then approve any GitHub write.</p>
          </div>
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            const result = await api.connect(payload());
            setConnected(result);
          })}>Connect repo</button>
        </header>

        {error && <div className="error">{error}</div>}

        <section className="connect panel">
          <input value={form.github_token} onChange={(e) => setForm({ ...form, github_token: e.target.value })} placeholder="GitHub token optional" type="password" />
          <button disabled={busy} onClick={() => run(async () => {
            const result = await api.repos(payload());
            setRepos(result.repos);
            if (result.repos[0]) selectRepo(result.repos[0].full_name);
            setNotice(result.repos.length ? `Loaded ${result.repos.length} repo(s).` : "No repositories returned. Type owner and repo manually.");
          })}>Load repos</button>
          <select value={`${form.owner}/${form.repo}`} onChange={(e) => selectRepo(e.target.value)}>
            <option value={`${form.owner}/${form.repo}`}>{form.owner}/{form.repo}</option>
            {repos.map((repo) => <option key={repo.full_name} value={repo.full_name}>{repo.full_name}</option>)}
          </select>
          <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} placeholder="owner" />
          <input value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} placeholder="repo" />
          <button disabled={busy} onClick={() => run(async () => {
            const result = await api.connect(payload());
            setConnected(result);
            setNotice(result.message);
          })}>Fetch issues</button>
          <button className="primary" disabled={busy} onClick={() => run(async () => {
            const result = await api.triage(form.owner, form.repo, payload());
            setApprovals(result);
            setNotice(result.length ? `Created ${result.length} approval item(s).` : "No open issues found, so no triage items were created.");
          })}>Fetch and triage</button>
        </section>

        {notice && <div className="notice">{notice}</div>}

        <section className="grid">
          <Metric label="Open issues" value={connected?.issues?.length ?? "0"} />
          <Metric label="Pending approvals" value={stats.pending} />
          <Metric label="Approved" value={stats.approved} />
          <Metric label="Posted" value={stats.posted} />
        </section>

        <section className="columns">
          <div className="panel queue">
            <div className="panel-title"><Tags size={18} /> Human review queue</div>
            {approvals.length === 0 && <p className="empty">Run triage to create approval-gated suggestions.</p>}
            {approvals.map((item) => (
              <ReviewCard key={item.id} item={item} busy={busy} onChange={(next) => setApprovals(approvals.map((a) => a.id === next.id ? next : a))} />
            ))}
          </div>

          <div className="panel side">
            {connected?.issues?.length > 0 && (
              <div className="issue-list">
                <div className="panel-title">Fetched issues</div>
                {connected.issues.map((issue) => <div className="history" key={issue.number}>#{issue.number} · {issue.title}</div>)}
              </div>
            )}
            <div className="panel-title">Repo Q&A</div>
            <textarea value={qa.question} onChange={(e) => setQa({ ...qa, question: e.target.value })} />
            <button onClick={() => run(async () => {
              const result = await api.qa({ ...payload(), question: qa.question });
              setQa({ ...qa, answer: result.answer });
            })}>Ask repo</button>
            {qa.answer && <p className="answer">{qa.answer}</p>}

            <div className="panel-title history-title">Workflow history</div>
            <button onClick={() => run(async () => setHistory(await api.history()))}>Refresh history</button>
            {history.map((item) => <div className="history" key={item.run_id}>{item.status} · #{item.issue} · {item.trace_project}</div>)}
          </div>
        </section>
      </section>
    </main>
  );
}

function Metric({ label, value }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function ReviewCard({ item, busy, onChange }) {
  const [labels, setLabels] = useState(item.result.labels.join(", "));
  const [response, setResponse] = useState(item.result.response);
  const decide = (approved) => api.decide(item.id, { approved, labels: labels.split(",").map((x) => x.trim()).filter(Boolean), response }).then(onChange);
  return (
    <article className="review">
      <div className="review-head">
        <strong>#{item.issue_number} {item.result.issue.title}</strong>
        <span className={`status ${item.status}`}>{item.status}</span>
      </div>
      <div className="chips"><span>{item.result.category}</span><span>{item.result.priority}</span><span>{Math.round(item.result.confidence * 100)}% confidence</span></div>
      <label>Labels<input value={labels} onChange={(e) => setLabels(e.target.value)} /></label>
      <label>Maintainer response<textarea value={response} onChange={(e) => setResponse(e.target.value)} /></label>
      <p className="rationale">{item.result.rationale}</p>
      <div className="actions">
        <button disabled={busy || item.status === "posted"} onClick={() => decide(true)}><Check size={15} /> Approve</button>
        <button disabled={busy || item.status === "posted"} onClick={() => decide(false)}><X size={15} /> Reject</button>
        <button className="primary" disabled={busy || item.status !== "approved"} onClick={() => api.post(item.id).then(onChange)}><Send size={15} /> Post</button>
      </div>
    </article>
  );
}

createRoot(document.getElementById("root")).render(<App />);
