import { FileText } from "lucide-react";

import type { PolicyRagSource } from "@/lib/api";

export function PolicySourceList({ sources }: { sources: PolicyRagSource[] }) {
  if (!sources.length) return null;

  return (
    <section className="mt-4 border-t border-slate-800 pt-3" aria-label="Policy sources">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">Sources</h3>
      <ul className="space-y-3">
        {sources.map((source) => (
          <li key={source.source_id} className="flex gap-2.5 text-xs">
            <FileText className="mt-0.5 h-4 w-4 shrink-0 text-emerald-200" />
            <div className="min-w-0">
              <p className="font-medium text-slate-200">
                {source.title}
                {source.page !== null ? ` · page ${source.page}` : ""}
              </p>
              <p className="mt-0.5 text-slate-500">{source.filename}</p>
              <p className="mt-1 whitespace-pre-wrap leading-5 text-slate-400">{source.excerpt}</p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}