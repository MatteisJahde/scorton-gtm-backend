/**
 * Lovable reference: fetch leads from Render backend.
 *
 * 1. In Lovable → Settings → Environment Variables, set:
 *    VITE_API_BASE_URL=https://scorton-gtm-backend.onrender.com
 *
 * 2. Copy this hook into src/hooks/useLeads.ts (or similar).
 * 3. Replace any hardcoded localhost URL or stale mock data.
 */

import { useCallback, useEffect, useState } from "react";
import type { LeadsSummaryResponse } from "./leads.types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ||
  "https://scorton-gtm-backend.onrender.com";

export function useLeadsSummary() {
  const [data, setData] = useState<LeadsSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/leads-summary`, {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`API ${response.status}: ${await response.text()}`);
      }

      const payload = (await response.json()) as LeadsSummaryResponse;
      setData(payload);
      return payload;
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load leads";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchLeads();
  }, [fetchLeads]);

  return { data, loading, error, refetch: fetchLeads, apiBaseUrl: API_BASE_URL };
}

/** Trigger backend CSV reload (run once after deploy, not on every page load). */
export async function reloadBackendFromCsv(
  baseUrl: string = API_BASE_URL,
): Promise<unknown> {
  const response = await fetch(`${baseUrl}/api/reload-from-csv`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Reload failed ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

export default useLeadsSummary;
