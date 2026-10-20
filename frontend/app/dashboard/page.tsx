"use client";

import { ArrowRight, BookOpenCheck, Bot, CalendarDays, Gift, Info, ListChecks, PlaneTakeoff, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchBirthdays, fetchHolidays, fetchProfile, type Birthday, type Holiday } from "@/lib/api";
import { formatDateDDMMYY } from "@/lib/date";
import { cn } from "@/lib/utils";

const aiAssistants = [
  {
    title: "Policy RAG",
    tag: "HR Policies",
    href: "/policy-rag",
    icon: BookOpenCheck,
    accentColor: "border-emerald-300/20 bg-emerald-300/10 text-emerald-200",
    badgeColor: "border-emerald-300/20 bg-emerald-400/10 text-emerald-200",
    hoverBorder: "hover:border-emerald-500/40",
    description:
      "Answers are grounded only in the supplied leave, HR, and privacy policy documents, with source citations.",
    actionText: "Ask Policy Questions",
  },
  {
    title: "Query Assistant",
    tag: "HRMS Data",
    href: "/query-assistant",
    icon: Sparkles,
    accentColor: "border-cyan-300/20 bg-cyan-300/10 text-cyan-200",
    badgeColor: "border-cyan-300/20 bg-cyan-400/10 text-cyan-200",
    hoverBorder: "hover:border-cyan-500/40",
    description:
      "Ask questions about HRMS data, employees, projects, skills, leave, and tickets. Access depends on your role.",
    actionText: "Query HRMS Data",
  },
  {
    title: "Automate HR Tasks",
    tag: "Operations",
    href: "/automate-hr-tasks",
    icon: ListChecks,
    accentColor: "border-indigo-300/20 bg-indigo-300/10 text-indigo-200",
    badgeColor: "border-indigo-300/20 bg-indigo-400/10 text-indigo-200",
    hoverBorder: "hover:border-indigo-500/40",
    description:
      "Request leave, create tickets, and perform role-authorized HR actions. Changes are sent to existing HRMS APIs and require your confirmation.",
    actionText: "Automate Operations",
  },
];

export default function DashboardPage() {
  const [name, setName] = useState("User");
  const [error, setError] = useState("");
  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [holidayIndex, setHolidayIndex] = useState(0);
  const [birthdays, setBirthdays] = useState<Birthday[]>([]);
  const router = useRouter();
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), []);

  const { birthdaysToday, upcomingBirthdays } = useMemo(() => {
    const todays = birthdays.filter((person) => person.date === todayIso);
    const upcoming = birthdays
      .filter((person) => {
        if (person.date === todayIso) {
          return false;
        }
        return person.date > todayIso;
      })
      .sort((a, b) => {
        if (a.date !== b.date) {
          return a.date.localeCompare(b.date);
        }
        return a.name.localeCompare(b.name);
      })
      .slice(0, 5);

    return { birthdaysToday: todays, upcomingBirthdays: upcoming };
  }, [birthdays, todayIso]);

  const token = useMemo(() => {
    if (typeof window === "undefined") {
      return null;
    }
    return localStorage.getItem("hrms_access_token");
  }, []);

  useEffect(() => {
    const loadProfile = async () => {
      if (!token) {
        localStorage.removeItem("hrms_access_token");
        document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
        router.push("/login");
        return;
      }

      try {
        const profile = await fetchProfile(token);
        if (profile.status === 401) {
          localStorage.removeItem("hrms_access_token");
          document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
          router.push("/login");
          return;
        }
        if (profile.ok && "success" in profile.body && profile.body.success) {
          setName(profile.body.data.name);
        } else {
          setError("Unable to load dashboard profile");
        }

        const holidaysResponse = await fetchHolidays(token, { limit: 200 });
        if (holidaysResponse.status === 401) {
          localStorage.removeItem("hrms_access_token");
          document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
          router.push("/login");
          return;
        }
        if (holidaysResponse.ok && "success" in holidaysResponse.body && holidaysResponse.body.success) {
          const holidayItems = holidaysResponse.body.data;
          setHolidays(holidayItems);
          if (holidayItems.length > 0) {
            const today = new Date().toISOString().slice(0, 10);
            const upcomingIndex = holidayItems.findIndex((item) => item.date >= today);
            setHolidayIndex(upcomingIndex >= 0 ? upcomingIndex : holidayItems.length - 1);
          } else {
            setHolidayIndex(0);
          }
        }

        const birthdaysResponse = await fetchBirthdays(token, { limit: 500 });
        if (birthdaysResponse.status === 401) {
          localStorage.removeItem("hrms_access_token");
          document.cookie = "hrms_auth=; path=/; max-age=0; samesite=lax";
          router.push("/login");
          return;
        }
        if (birthdaysResponse.ok && "success" in birthdaysResponse.body && birthdaysResponse.body.success) {
          setBirthdays(birthdaysResponse.body.data);
        }
      } catch {
        setError("Unable to load dashboard profile");
      }
    };

    loadProfile();
  }, [router, token]);

  return (
    <main className="flex min-h-screen bg-[#0b1020]">
      <Sidebar />
      <section className="flex w-full min-w-0 flex-col bg-[#060a16]">
        <Topbar name={name} title="Dashboard" />
        <div className="space-y-8 p-4 md:p-6">
          {/* AI Assistants Section */}
          <div>
            <div className="mb-4">
              <div className="flex items-center gap-2">
                <Bot className="h-5 w-5 text-indigo-300" />
                <h2 className="text-xl font-semibold text-white">AI Assistants</h2>
              </div>
              <p className="mt-1 text-sm text-slate-400">
                Choose an AI assistant to query company policies, explore HRMS data, or automate HR operations.
              </p>
            </div>

            <div className="grid gap-5 md:grid-cols-3">
              {aiAssistants.map((assistant) => (
                <Card
                  key={assistant.title}
                  className={cn(
                    "flex flex-col justify-between border border-slate-800/90 bg-[#0d1428] text-white shadow-lg shadow-black/20 transition hover:-translate-y-0.5 hover:shadow-xl",
                    assistant.hoverBorder
                  )}
                >
                  <CardContent className="flex flex-1 flex-col justify-between p-5">
                    <div>
                      <div className="flex items-center justify-between">
                        <div
                          className={cn(
                            "inline-flex h-11 w-11 items-center justify-center rounded-xl border",
                            assistant.accentColor
                          )}
                        >
                          <assistant.icon className="h-5 w-5" />
                        </div>
                        <span className={cn("rounded-full border px-3 py-1 text-xs", assistant.badgeColor)}>
                          {assistant.tag}
                        </span>
                      </div>

                      <div className="mt-4">
                        <h3 className="text-lg font-semibold text-white">{assistant.title}</h3>
                      </div>

                      <div className="mt-3 rounded-xl border border-slate-700/60 bg-slate-950/80 p-3 text-xs leading-relaxed text-slate-200">
                        <div className="flex items-start gap-2.5">
                          <Info className="mt-0.5 h-4 w-4 shrink-0 text-cyan-300" />
                          <span>{assistant.description}</span>
                        </div>
                      </div>
                    </div>

                    <div className="mt-6">
                      <Button
                        className="w-full justify-between transition hover:bg-indigo-600 hover:text-white"
                        onClick={() => router.push(assistant.href)}
                      >
                        <span>{assistant.actionText}</span>
                        <ArrowRight className="h-4 w-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>

          <div className="grid items-start gap-5 xl:grid-cols-[1.05fr_1fr_1fr]">
            <Card className="border border-slate-800/90 bg-gradient-to-br from-[#171a3f] via-[#342f72] to-[#6f5bd3] text-white shadow-[0_30px_80px_-45px_rgba(99,102,241,0.7)]">
              <CardContent className="p-5">
                <div className="flex items-center justify-between">
                  <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-white/15 bg-white/10">
                    <CalendarDays className="h-5 w-5" />
                  </div>
                  <span className="rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs text-indigo-100">Holidays</span>
                </div>
                <h2 className="mt-4 text-xl font-semibold">Upcoming Holidays</h2>
                <p className="mt-1 text-sm text-indigo-100/85">Plan leave requests around upcoming holidays.</p>
                <div className="mt-4 space-y-2">
                  {holidays.length > 0 ? (
                    <div
                      className="flex w-full items-center justify-between rounded-xl border border-white/10 bg-black/10 px-3 py-2 text-left"
                      key={holidays[holidayIndex].name}
                    >
                      <button
                        type="button"
                        onClick={() => setHolidayIndex((prev) => Math.max(prev - 1, 0))}
                        disabled={holidayIndex === 0}
                        className="mr-3 rounded-md border border-white/15 px-2 py-1 text-sm text-indigo-100 disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label="Previous holiday"
                      >
                        {"<"}
                      </button>
                      <div>
                        <p className="text-sm font-medium text-white">{holidays[holidayIndex].name}</p>
                        <p className="text-xs text-indigo-100/80">{formatDateDDMMYY(holidays[holidayIndex].date)}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setHolidayIndex((prev) => Math.min(prev + 1, holidays.length - 1))}
                        disabled={holidayIndex === holidays.length - 1}
                        className="ml-3 rounded-md border border-white/15 px-2 py-1 text-sm text-indigo-100 disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label="Next holiday"
                      >
                        {">"}
                      </button>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-white/10 bg-black/10 px-3 py-3 text-sm text-indigo-100/80">
                      No holidays available right now.
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card className="border border-slate-800/90 bg-[#0d1428] text-white shadow-lg shadow-black/20">
              <CardContent className="p-5">
                <div className="flex items-center justify-between">
                  <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-emerald-300/15 bg-emerald-400/10 text-emerald-200">
                    <PlaneTakeoff className="h-5 w-5" />
                  </div>
                  <span className="rounded-full border border-emerald-300/15 bg-emerald-400/10 px-3 py-1 text-xs text-emerald-200">Leave</span>
                </div>
                <h2 className="mt-4 text-xl font-semibold">Request Leave</h2>
                <p className="mt-1 text-sm text-slate-300">Open the leaves module to submit a request and track approvals.</p>
                <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
                  <p className="text-sm text-slate-300">Use this to apply for casual, sick, or earned leave.</p>
                  <Button className="mt-4 w-full" onClick={() => router.push("/leaves")}>Open Leave Module</Button>
                </div>
              </CardContent>
            </Card>

            <Card className="border border-slate-800/90 bg-[#0d1428] text-white shadow-lg shadow-black/20">
              <CardContent className="p-5">
                <div className="flex items-center justify-between">
                  <div className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-amber-300/15 bg-amber-400/10 text-amber-200">
                    <Gift className="h-5 w-5" />
                  </div>
                  <span className="rounded-full border border-amber-300/15 bg-amber-400/10 px-3 py-1 text-xs text-amber-200">Birthdays</span>
                </div>
                <h2 className="mt-4 text-xl font-semibold">Employee Birthdays</h2>
                <p className="mt-1 text-sm text-slate-300">Upcoming celebrations across teams.</p>
                <div className="mt-4 space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-amber-200/90">Birthdays Today</p>
                  {birthdaysToday.map((person) => (
                    <div className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-3" key={`${person.name}-${person.date}`}>
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-white">{person.name}</p>
                          <p className="truncate text-xs text-slate-400">{person.team}</p>
                        </div>
                        <p className="shrink-0 text-xs text-amber-200">{formatDateDDMMYY(person.date)}</p>
                      </div>
                    </div>
                  ))}
                  {birthdaysToday.length === 0 ? (
                    <div className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-3 text-sm text-slate-400">
                      No birthdays today.
                    </div>
                  ) : null}

                  <p className="pt-2 text-xs font-semibold uppercase tracking-wide text-amber-200/90">Upcoming Birthdays</p>
                  {upcomingBirthdays.map((person) => (
                    <div className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-3" key={`${person.name}-${person.date}`}>
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-white">{person.name}</p>
                          <p className="truncate text-xs text-slate-400">{person.team}</p>
                        </div>
                        <p className="shrink-0 text-xs text-amber-200">{formatDateDDMMYY(person.date)}</p>
                      </div>
                    </div>
                  ))}
                  {upcomingBirthdays.length === 0 ? (
                    <div className="rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-3 text-sm text-slate-400">
                      No upcoming birthdays.
                    </div>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          </div>

          {error ? <p className="mt-4 text-sm text-rose-300">{error}</p> : null}
        </div>
      </section>
    </main>
  );
}
