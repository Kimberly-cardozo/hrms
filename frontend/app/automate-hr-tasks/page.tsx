"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bot, LoaderCircle, Send, UserRound, WandSparkles } from "lucide-react";

import { ActionResultCard } from "@/components/ai/action-result-card";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import {
  askHRActionAssistant,
  fetchProfile,
  type HRActionAssistantResponse,
} from "@/lib/api";

type ChatEntry = {
  role: "user" | "assistant";
  content: string;
  action?: HRActionAssistantResponse["action"];
};

function suggestionsFor(role: string) {
  const suggestions = [
    "Apply for earned leave from 2026-11-12 to 2026-11-13 for personal work.",
    "Create a high-priority IT ticket for my VPN issue.",
    "Check my leave balance.",
    "Show my recent tickets.",
  ];
  if (role === "MANAGER") {
    suggestions.push("Show pending leave requests for my team.", "Assign ticket 1 to employee 3.");
  }
  if (role === "ADMIN") {
    suggestions.push("Create a project called Skills Directory.", "Create an announcement for the next town hall.");
  }
  return suggestions;
}

function responseError(body: unknown): string {
  if (typeof body !== "object" || body === null) {
    return "The HR task request could not be completed.";
  }
  const detail = "detail" in body ? body.detail : "error" in body ? body.error : null;
  if (typeof detail === "object" && detail !== null && "error" in detail) {
    const error = (detail as { error?: { message?: string } }).error;
    if (error?.message) return error.message;
  }
  return "The HR task request could not be completed.";
}

export default function AutomateHRTasksPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [name, setName] = useState("User");
  const [role, setRole] = useState("EMPLOYEE");
  const [messages, setMessages] = useState<ChatEntry[]>([
    { role: "assistant", content: "What HR task would you like to take care of?" },
  ]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [busyConfirmation, setBusyConfirmation] = useState("");
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
          setRole(profile.body.data.role);
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
      const response = await askHRActionAssistant(token, { message, history });
      if (response.status === 401) {
        localStorage.removeItem("hrms_access_token");
        document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
        router.push("/login");
        return;
      }
      if (response.ok && "data" in response.body && response.body.success) {
        const responseData = response.body.data;
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: responseData.answer,
            action: responseData.action ?? undefined,
          },
        ]);
      } else {
        setMessages((current) => [
          ...current,
          { role: "assistant", content: responseError(response.body) },
        ]);
      }
    } catch {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "Unable to reach the HR task assistant. Check the API connection and try again." },
      ]);
    } finally {
      setPending(false);
    }
  }

  async function confirmAction(confirmationToken: string) {
    if (!token || pending || busyConfirmation) return;
    setPending(true);
    setBusyConfirmation(confirmationToken);
    setError("");

    try {
      const response = await askHRActionAssistant(token, { confirmation_token: confirmationToken });
      if (response.status === 401) {
        localStorage.removeItem("hrms_access_token");
        document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
        router.push("/login");
        return;
      }
      if (response.ok && "data" in response.body && response.body.success) {
        const responseData = response.body.data;
        setMessages((current) => [
          ...current.map((entry) =>
            entry.action?.confirmation_token === confirmationToken
              ? { ...entry, action: { ...entry.action, status: "completed" as const, confirmation_token: undefined } }
              : entry
          ),
          {
            role: "assistant",
            content: responseData.answer,
            action: responseData.action ?? undefined,
          },
        ]);
      } else {
        const message = responseError(response.body);
        setMessages((current) => current.map((entry) =>
          entry.action?.confirmation_token === confirmationToken
            ? { ...entry, action: { ...entry.action, status: "failed" as const, confirmation_token: undefined, summary: message } }
            : entry
        ));
      }
    } catch {
      setError("Unable to confirm the action. Check the API connection and try again.");
    } finally {
      setPending(false);
      setBusyConfirmation("");
    }
  }

  function cancelAction(confirmationToken: string) {
    setMessages((current) => current.map((entry) =>
      entry.action?.confirmation_token === confirmationToken
        ? { ...entry, action: { ...entry.action, status: "cancelled" as const, confirmation_token: undefined, summary: "Action cancelled; no API call was made." } }
        : entry
    ));
  }

  function submitForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendQuestion(draft);
  }

  return (
    <main className="flex min-h-screen bg-[#0b1020] text-slate-100">
      <Sidebar />
      <section className="flex min-w-0 flex-1 flex-col bg-[#060a16]">
        <Topbar name={name} title="Automate HR Tasks" />
        <div className="mx-auto flex min-h-0 w-full max-w-5xl flex-1 flex-col px-4 pb-5 pt-5 md:px-8 md:pb-8">
          <header className="mb-5 flex items-center gap-3 border-b border-slate-800 pb-5">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-emerald-300/20 bg-emerald-300/10 text-emerald-200">
              <WandSparkles className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-emerald-200/80">HR operations</p>
              <h1 className="text-xl font-semibold text-white">Automate HR Tasks</h1>
            </div>
          </header>

          <div className="flex min-h-[320px] flex-1 flex-col gap-5 overflow-y-auto pb-5" aria-live="polite">
            {messages.map((entry, index) => (
              <article key={`${entry.role}-${index}`} className={`flex gap-3 ${entry.role === "user" ? "flex-row-reverse" : ""}`}>
                <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${entry.role === "user" ? "bg-indigo-300/15 text-indigo-200" : "bg-emerald-300/10 text-emerald-200"}`}>
                  {entry.role === "user" ? <UserRound className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
                </div>
                <div className={`min-w-0 max-w-[88%] rounded-xl border px-4 py-3 ${entry.role === "user" ? "border-indigo-300/15 bg-indigo-300/10" : "border-slate-800 bg-[#0b1222]"}`}>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-200">{entry.content}</p>
                  {entry.action && (
                    <ActionResultCard
                      action={entry.action}
                      busy={pending && busyConfirmation === entry.action.confirmation_token}
                      onConfirm={() => entry.action?.confirmation_token && void confirmAction(entry.action.confirmation_token)}
                      onCancel={() => entry.action?.confirmation_token && cancelAction(entry.action.confirmation_token)}
                    />
                  )}
                </div>
              </article>
            ))}
            {pending && !busyConfirmation && (
              <div className="flex items-center gap-3 text-sm text-slate-400">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-300/10 text-emerald-200"><Bot className="h-4 w-4" /></span>
                <LoaderCircle className="h-4 w-4 animate-spin" />
                <span>Checking the authorized HR tools</span>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {messages.length === 1 && (
            <div className="mb-4 flex flex-wrap gap-2">
              {suggestionsFor(role).map((suggestion) => (
                <button key={suggestion} type="button" disabled={!token || pending} onClick={() => void sendQuestion(suggestion)} className="rounded-full border border-slate-700 bg-slate-900/70 px-3 py-2 text-left text-xs text-slate-300 transition hover:border-emerald-300/40 hover:text-white disabled:opacity-50">
                  {suggestion}
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
              placeholder="Describe an HR task..."
              aria-label="Describe an HR task"
              className="max-h-36 min-h-10 flex-1 resize-y bg-transparent px-2 py-2 text-sm text-white outline-none placeholder:text-slate-500"
            />
            <button type="submit" disabled={!draft.trim() || !token || pending} title="Send task" aria-label="Send task" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-emerald-300 text-slate-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-40">
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      </section>
    </main>
  );
}
