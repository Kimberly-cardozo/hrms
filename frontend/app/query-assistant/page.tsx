"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, Info, LoaderCircle, Send, Sparkles, UserRound } from "lucide-react";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { askSQLAssistant, fetchProfile, type SQLAssistantResponse } from "@/lib/api";

type ChatEntry = {
  role: "user" | "assistant";
  content: string;
  result?: SQLAssistantResponse;
};

const suggestions = [
  "Which projects are ongoing?",
  "Show my current project assignments.",
  "What is my remaining leave balance?",
  "Show open tickets assigned to me.",
];

export default function QueryAssistantPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [name, setName] = useState("User");
  const [messages, setMessages] = useState<ChatEntry[]>([
    {
      role: "assistant",
      content: "What would you like to know about your HRMS data?",
    },
  ]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const accessToken = localStorage.getItem("hrms_access_token");
    if (!accessToken) {
      router.push("/login");
      return;
    }
    setToken(accessToken);
    fetchProfile(accessToken).then((profile) => {
      if (profile.status === 401) {
        localStorage.removeItem("hrms_access_token");
        document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
        router.push("/login");
      } else if (profile.ok && "success" in profile.body && profile.body.success) {
        setName(profile.body.data.name);
      }
    }).catch(() => setError("Unable to connect to your HRMS profile."));
  }, [router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  async function submitQuestion(event: FormEvent<HTMLFormElement>, question = draft) {
    event.preventDefault();
    const message = question.trim();
    if (!message || !token || pending) return;

    const previousMessages = messages;
    setMessages((current) => [...current, { role: "user", content: message }]);
    setDraft("");
    setPending(true);
    setError("");

    try {
      const history = previousMessages.slice(-8).map(({ role, content }) => ({ role, content }));
      const response = await askSQLAssistant(token, message, history);
      if (response.status === 401) {
        localStorage.removeItem("hrms_access_token");
        document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
        router.push("/login");
        return;
      }
      if (response.ok && "data" in response.body && response.body.success) {
        const result = response.body.data;
        setMessages((current) => [
          ...current,
          { role: "assistant", content: result.answer, result },
        ]);
      } else {
        const detail = "detail" in response.body ? response.body.detail : null;
        const messageText = typeof detail === "object" && detail !== null && "error" in detail
          ? (detail.error as { message?: string } | null)?.message
          : undefined;
        setMessages((current) => [
          ...current,
          { role: "assistant", content: messageText || "I couldn't complete that query. Please try again." },
        ]);
      }
    } catch {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "Unable to reach the SQL assistant. Check the API connection and try again." },
      ]);
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen bg-[#0b1020] text-slate-100">
      <Sidebar />
      <section className="flex min-w-0 flex-1 flex-col bg-[#060a16]">
        <Topbar name={name} title="Query Assistant" />
        <div className="mx-auto flex w-full max-w-5xl min-h-0 flex-1 flex-col px-4 pb-5 pt-5 md:px-8 md:pb-8">
          <header className="mb-5 flex items-center gap-3 border-b border-slate-800 pb-5">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-200/80">HRMS data</p>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-semibold text-white">Query Assistant</h1>
                <button
                  type="button"
                  title="Ask questions about HRMS data, employees, projects, skills, leave, and tickets. Access depends on your role."
                  aria-label="About Query Assistant"
                  className="text-slate-500 transition hover:text-cyan-200 focus-visible:text-cyan-200"
                >
                  <Info className="h-4 w-4" />
                </button>
              </div>
            </div>
          </header>

          <div className="flex min-h-[320px] flex-1 flex-col gap-5 overflow-y-auto pb-5" aria-live="polite">
            {messages.map((entry, index) => (
              <article key={`${entry.role}-${index}`} className={`flex gap-3 ${entry.role === "user" ? "flex-row-reverse" : ""}`}>
                <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${entry.role === "user" ? "bg-indigo-300/15 text-indigo-200" : "bg-cyan-300/10 text-cyan-200"}`}>
                  {entry.role === "user" ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <div className={`min-w-0 max-w-[88%] rounded-xl border px-4 py-3 ${entry.role === "user" ? "border-indigo-300/15 bg-indigo-300/10" : "border-slate-800 bg-[#0b1222]"}`}>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-200">{entry.content}</p>
                  {entry.result && entry.result.rows.length > 0 && (
                    <div className="mt-4 max-w-full overflow-x-auto rounded-lg border border-slate-800">
                      <table className="w-full min-w-max text-left text-xs">
                        <thead className="bg-slate-900/90 text-slate-400">
                          <tr>{Object.keys(entry.result.rows[0]).map((column) => <th key={column} className="px-3 py-2 font-medium">{column.replaceAll("_", " ")}</th>)}</tr>
                        </thead>
                        <tbody>
                          {entry.result.rows.map((row, rowIndex) => (
                            <tr key={rowIndex} className="border-t border-slate-800 text-slate-300">
                              {Object.values(row).map((value, valueIndex) => <td key={valueIndex} className="max-w-64 px-3 py-2">{value === null ? "-" : String(value)}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {entry.result?.truncated && <p className="mt-2 text-xs text-slate-500">Showing the first 100 rows.</p>}
                  {entry.result?.sql && (
                    <details className="mt-3 border-t border-slate-800 pt-2 text-xs text-slate-400">
                      <summary className="cursor-pointer select-none">View generated SQL</summary>
                      <pre className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap rounded-md bg-black/20 p-3 text-cyan-100">{entry.result.sql}</pre>
                    </details>
                  )}
                </div>
              </article>
            ))}
            {pending && (
              <div className="flex items-center gap-3 text-sm text-slate-400">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-300/10 text-cyan-200"><Bot className="h-4 w-4" /></span>
                <LoaderCircle className="h-4 w-4 animate-spin" />
                <span>Working on your query</span>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {messages.length === 1 && (
            <div className="mb-4 flex flex-wrap gap-2">
              {suggestions.map((suggestion) => (
                <button key={suggestion} type="button" disabled={!token || pending} onClick={(event) => void submitQuestion(event as unknown as FormEvent<HTMLFormElement>, suggestion)} className="rounded-full border border-slate-700 bg-slate-900/70 px-3 py-2 text-left text-xs text-slate-300 transition hover:border-cyan-300/40 hover:text-white disabled:opacity-50">
                  {suggestion}
                </button>
              ))}
            </div>
          )}

          {error && <p role="status" className="mb-3 text-sm text-rose-300">{error}</p>}
          <form onSubmit={(event) => void submitQuestion(event)} className="flex items-end gap-3 rounded-xl border border-slate-700 bg-[#0b1222] p-3 focus-within:border-cyan-300/40">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              rows={1}
              maxLength={2000}
              placeholder="Ask about projects, leave, skills, or tickets..."
              aria-label="Ask a question about HRMS data"
              className="max-h-36 min-h-10 flex-1 resize-y bg-transparent px-2 py-2 text-sm text-white outline-none placeholder:text-slate-500"
            />
            <button type="submit" disabled={!draft.trim() || !token || pending} title="Send question" aria-label="Send question" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-cyan-300 text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-40">
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}