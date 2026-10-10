"use client";

import Link from "next/link";
import { Check, CircleAlert, ExternalLink, LoaderCircle, X } from "lucide-react";

import type { HRActionResult } from "@/lib/api";

type ActionResultCardProps = {
  action: HRActionResult;
  busy?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
};

const handoffLabels: Record<string, string> = {
  "/query-assistant": "Open Query Assistant",
  "/hr-policies": "Open HR Policies",
};

export function ActionResultCard({ action, busy = false, onConfirm, onCancel }: ActionResultCardProps) {
  if (action.status === "handoff" && action.target && handoffLabels[action.target]) {
    return (
      <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-700 pt-3">
        <span className="text-xs text-slate-400">{action.summary}</span>
        <Link href={action.target} className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-cyan-200 hover:text-cyan-100">
          {handoffLabels[action.target]} <ExternalLink className="h-3.5 w-3.5" />
        </Link>
      </div>
    );
  }

  if (action.status === "confirmation_required") {
    return (
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-amber-300/20 pt-3">
        <div className="flex min-w-0 items-center gap-2 text-sm text-amber-100">
          <CircleAlert className="h-4 w-4 shrink-0" />
          <span>{action.summary}</span>
        </div>
        <div className="flex shrink-0 gap-2">
          <button type="button" onClick={onCancel} disabled={busy} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-slate-600 px-3 text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50">
            <X className="h-3.5 w-3.5" /> Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={busy} className="inline-flex h-9 items-center gap-1.5 rounded-md bg-emerald-300 px-3 text-xs font-semibold text-slate-950 hover:bg-emerald-200 disabled:opacity-50">
            {busy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
            Confirm
          </button>
        </div>
      </div>
    );
  }

  const statusLabel = action.status === "completed" ? "Completed" : action.status === "denied" ? "Not permitted" : action.status === "cancelled" ? "Cancelled" : "Not completed";
  const statusClass = action.status === "completed" ? "text-emerald-200" : action.status === "cancelled" ? "text-slate-400" : "text-rose-200";
  return (
    <div className={`mt-3 flex items-start gap-2 border-t border-slate-700 pt-3 text-xs ${statusClass}`}>
      {action.status === "completed" ? <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <span><strong>{statusLabel}:</strong> {action.summary}</span>
    </div>
  );
}