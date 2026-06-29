/**
 * Lovable reference: ZeroBounce / API mapping diagnostic panel.
 *
 * Usage in Lovable:
 * 1. Copy this file + leads.types.ts + useLeads.ts into your project.
 * 2. Add a "Diagnostic" tab that renders <LeadDiagnosticsPanel />.
 * 3. Set VITE_API_BASE_URL=https://scorton-gtm-backend.onrender.com
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { LeadSummary, LeadsSummaryResponse } from "./leads.types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ||
  "https://scorton-gtm-backend.onrender.com";

function isFailedVerification(lead: LeadSummary): boolean {
  const emailStatus = (lead.email_status || "").trim().toLowerCase();
  const zbStatus = (lead.zerobounce_status || "").trim().toLowerCase();
  return (
    emailStatus === "error" ||
    emailStatus === "unknown" ||
    zbStatus === "error" ||
    zbStatus === "unknown"
  );
}

function formatTimestamp(value?: string | null): string {
  if (!value) return "Not finished yet";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

type LeadDiagnosticsPanelProps = {
  className?: string;
};

export function LeadDiagnosticsPanel({ className = "" }: LeadDiagnosticsPanelProps) {
  const [data, setData] = useState<LeadsSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showRawJson, setShowRawJson] = useState(false);

  const fetchDiagnostics = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/leads-summary`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`API ${response.status}: ${await response.text()}`);
      }
      const payload = (await response.json()) as LeadsSummaryResponse;
      setData(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load diagnostics");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchDiagnostics();
  }, [fetchDiagnostics]);

  const verification = data?.diagnostics?.verification_summary;
  const topLeads = data?.top_leads ?? [];

  const tableStats = useMemo(() => {
    let valid = 0;
    let invalid = 0;
    let flagged = 0;
    for (const lead of topLeads) {
      const emailStatus = (lead.email_status || "").toLowerCase();
      const zbStatus = (lead.zerobounce_status || "").toLowerCase();
      if (emailStatus === "verified" || zbStatus === "valid") valid += 1;
      if (
        emailStatus === "invalid" ||
        emailStatus === "risky" ||
        zbStatus === "invalid" ||
        zbStatus === "spamtrap"
      ) {
        invalid += 1;
      }
      if (isFailedVerification(lead)) flagged += 1;
    }
    return { valid, invalid, flagged };
  }, [topLeads]);

  if (loading) {
    return <div className={`p-4 text-sm text-muted-foreground ${className}`}>Loading diagnostics…</div>;
  }

  if (error) {
    return (
      <div className={`space-y-3 p-4 ${className}`}>
        <p className="text-sm text-destructive">{error}</p>
        <button
          type="button"
          onClick={() => void fetchDiagnostics()}
          className="rounded border px-3 py-1 text-sm"
        >
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className={`space-y-4 p-4 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">ZeroBounce Diagnostics</h2>
          <p className="text-sm text-muted-foreground">
            Raw mapping check for <code className="text-xs">/api/leads-summary</code>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowRawJson((value) => !value)}
            className="rounded border px-3 py-1 text-sm"
          >
            {showRawJson ? "Hide Raw JSON" : "Show Raw JSON"}
          </button>
          <button
            type="button"
            onClick={() => void fetchDiagnostics()}
            className="rounded border px-3 py-1 text-sm"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard label="Total leads processed" value={String(data?.total_leads ?? 0)} />
        <SummaryCard
          label="Valid emails (all leads)"
          value={String(verification?.valid_emails ?? tableStats.valid)}
        />
        <SummaryCard
          label="Invalid emails (all leads)"
          value={String(verification?.invalid_emails ?? tableStats.invalid)}
        />
        <SummaryCard
          label="Last backend init"
          value={formatTimestamp(data?.diagnostics?.initialization_finished_at)}
        />
      </div>

      <div className="rounded border bg-muted/30 p-3 text-sm">
        <p>
          <span className="font-medium">db_ready:</span>{" "}
          {data?.diagnostics?.db_ready ? "true" : "false"}
        </p>
        <p>
          <span className="font-medium">Flagged rows (error/unknown):</span>{" "}
          {verification?.failed_or_unknown_emails ?? tableStats.flagged}
        </p>
        <p>
          <span className="font-medium">Missing verification fields:</span>{" "}
          {verification?.missing_verification_fields ?? 0}
        </p>
      </div>

      {showRawJson ? (
        <pre className="max-h-[420px] overflow-auto rounded border bg-slate-950 p-3 text-xs text-slate-100">
          {JSON.stringify(data, null, 2)}
        </pre>
      ) : (
        <div className="overflow-x-auto rounded border">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-muted/50 text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-2">Company</th>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">email_status</th>
                <th className="px-3 py-2">zerobounce_status</th>
                <th className="px-3 py-2">zerobounce_sub_status</th>
                <th className="px-3 py-2">email_provider</th>
              </tr>
            </thead>
            <tbody>
              {topLeads.map((lead) => {
                const highlight = isFailedVerification(lead);
                return (
                  <tr
                    key={`${lead.id ?? lead.company}-${lead.verified_email ?? lead.work_email}`}
                    className={highlight ? "bg-yellow-100" : "odd:bg-background even:bg-muted/10"}
                  >
                    <td className="px-3 py-2 font-medium">{lead.company}</td>
                    <td className="px-3 py-2">{lead.verified_email || lead.work_email || "—"}</td>
                    <td className="px-3 py-2">{lead.email_status || "—"}</td>
                    <td className="px-3 py-2">{lead.zerobounce_status || "—"}</td>
                    <td className="px-3 py-2">{lead.zerobounce_sub_status || "—"}</td>
                    <td className="px-3 py-2">{lead.email_provider || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-card p-3 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}

type DiagnosticTabToggleProps = {
  active: boolean;
  onToggle: () => void;
};

/** Small header toggle — wire to your existing tab state in Lovable. */
export function DiagnosticTabToggle({ active, onToggle }: DiagnosticTabToggleProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`rounded px-3 py-1.5 text-sm ${
        active ? "bg-primary text-primary-foreground" : "border bg-background"
      }`}
    >
      Diagnostic
    </button>
  );
}

export default LeadDiagnosticsPanel;
