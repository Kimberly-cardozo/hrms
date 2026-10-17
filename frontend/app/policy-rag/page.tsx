"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, BookOpenCheck, Info, LoaderCircle, Send, UserRound } from "lucide-react";

import { PolicySourceList } from "@/components/ai/policy-source-list";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { askPolicyRag, fetchProfile, type PolicyRagSource } from "@/lib/api";

type PolicyChatEntry = {
  role: "user" | "assistant";
  content: string;
  sources?: PolicyRagSource[];
  refused?: boolean;
};

const suggestedQuestions = [
  "What does the leave policy say about leave requests?",
  "What does the privacy policy say about employee data?",
  "What does the HR policy document cover?",
];

function getErrorMessage(body: unknown): string {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return "The policy question could not be answered.";
  }
  const detail = body.detail;
  if (typeof detail === "object" && detail !== null && "error" in detail) {
    const error = (detail as { error?: { message?: string } }).error;
    if (error?.message) return error.message;
  }
  return "The policy question could not be answered.";
}

export default function PolicyRagPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [name, setName] = useState("User");
  const [messages, setMessages] = useState<PolicyChatEntry[]>([
    { role: "assistant", content: "Ask a question grounded in the supplied leave, HR, and privacy policies." },
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
    fetchProfile(accessToken)
      .then((profile) => {
        if (profile.status === 401) {
          localStorage.removeItem("hrms_access_token");
          document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
          router.push("/login");
        } else if (profile.ok && "success" in profile.body && profile.body.success) {
          setName(profile.body.data.name);
        }
      })
      .catch(() => setError("Unable to connect to your HRMS profile."));
  }, [router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  async function sendQuestion(question: string) {
    const message = question.trim();
    if (!message || !token || pending) return;

    const previousMessages = messages;
    setMessages((current) => [...current, { role: "user", content: message }]);
    setDraft("");
    setPending(true);
    setError("");

    try {
      const history = previousMessages.slice(-8).map(({ role, content }) => ({ role, content }));
      const response = await askPolicyRag(token, message, history);
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
          {
            role: "assistant",
            content: result.answer,
            sources: result.sources,
            refused: result.refused,
          },
        ]);
      } else {
        setMessages((current) => [...current, { role: "assistant", content: getErrorMessage(response.body) }]);
      }
    } catch {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "Unable to reach Policy RAG. Check the API connection and try again." },
      ]);
    } finally {
      setPending(false);
    }
  }

  function submitForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendQuestion(draft);
  }

  return (
    <main className="flex min-h-screen bg-[#0b1020] text-slate-100">
      <Sidebar />
      <section className="flex min-w-0 flex-1 flex-col bg-[#060a16]">
        <Topbar name={name} title="Policy RAG" />
        <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col px-4 pb-5 pt-5 md:px-8 md:pb-8">
          <header className="mb-5 flex items-center gap-3 border-b border-slate-800 pb-5">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-emerald-300/20 bg-emerald-300/10 text-emerald-200">
              <BookOpenCheck className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-semibold text-white">Policy RAG</h1>
                <button
                  type="button"
                  title="Answers are grounded only in the supplied leave, HR, and privacy policy documents, with source citations."
                  aria-label="About Policy RAG"
                  className="text-slate-500 transition hover:text-emerald-200 focus-visible:text-emerald-200"
                >
                  <Info className="h-4 w-4" />
                </button>
              </div>
              <p className="text-xs text-emerald-200/80">Policy sources</p>
            </div>
          </header>

          <div className="flex min-h-[320px] flex-1 flex-col gap-5 overflow-y-auto pb-5" aria-live="polite">
            {messages.map((entry, index) => (
              <article key={`${entry.role}-${index}`} className={`flex gap-3 ${entry.role === "user" ? "flex-row-reverse" : ""}`}>
                <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${entry.role === "user" ? "bg-indigo-300/15 text-indigo-200" : "bg-emerald-300/10 text-emerald-200"}`}>
                  {entry.role === "user" ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <div className={`min-w-0 max-w-[88%] rounded-xl border px-4 py-3 ${entry.role === "user" ? "border-indigo-300/15 bg-indigo-300/10" : "border-slate-800 bg-[#0b1222]"}`}>
                  {entry.refused && <p className="mb-1 text-xs font-medium text-amber-200">Outside policy scope</p>}
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-200">{entry.content}</p>
                  {entry.sources && <PolicySourceList sources={entry.sources} />}
                </div>
              </article>
            ))}
            {pending && (
              <div className="flex items-center gap-3 text-sm text-slate-400">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-300/10 text-emerald-200"><Bot className="h-4 w-4" /></span>
                <LoaderCircle className="h-4 w-4 animate-spin" />
                <span>Searching policy sources</span>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {messages.length === 1 && (
            <div className="mb-4 flex flex-wrap gap-2">
              {suggestedQuestions.map((question) => (
                <button key={question} type="button" disabled={!token || pending} onClick={() => void sendQuestion(question)} className="rounded-full border border-slate-700 bg-slate-900/70 px-3 py-2 text-left text-xs text-slate-300 transition hover:border-emerald-300/40 hover:text-white disabled:opacity-50">
                  {question}
                </button>
              ))}
            </div>
          )}

          {error && <p role="status" className="mb-3 text-sm text-rose-300">{error}</p>}
          <form onSubmit={submitForm} className="flex items-end gap-3 rounded-xl border border-slate-700 bg-[#0b1222] p-3 focus-within:border-emerald-300/40">
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
              placeholder="Ask about the supplied policies..."
              aria-label="Ask a question about supplied HR policies"
              className="max-h-36 min-h-10 flex-1 resize-y bg-transparent px-2 py-2 text-sm text-white outline-none placeholder:text-slate-500"
            />
            <button type="submit" disabled={!draft.trim() || !token || pending} title="Send question" aria-label="Send question" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-300 text-slate-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-40">
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}