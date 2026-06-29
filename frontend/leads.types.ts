/**
 * Lovable reference: API types for scorton-gtm-backend.
 * Copy into your Lovable project (e.g. src/types/leads.ts).
 */

export type ContactStatus =
  | "verified"
  | "review"
  | "no_contact_found"
  | "placeholder";

export type LeadSummary = {
  id?: number | null;
  company: string;
  website?: string | null;
  company_website?: string | null;
  domain?: string | null;
  website_status?: "ready" | "unavailable" | null;
  website_link?: string | null;
  industry?: string | null;
  city?: string | null;
  intent?: "high" | "low" | string | null;
  company_ai_signal?: number | null;
  signal_score?: number | null;
  buyer_name?: string | null;
  job_title?: string | null;
  work_email?: string | null;
  contact_name?: string | null;
  contact_role?: string | null;
  verified_email?: string | null;
  contact_status?: ContactStatus | null;
  needs_review?: boolean | null;
  email_status?:
    | "Verified"
    | "Review"
    | "Risky"
    | "Invalid"
    | "Role Account"
    | "Suppressed"
    | "Unverified"
    | string
    | null;
  email_provider?: string | null;
  zerobounce_status?: string | null;
  zerobounce_sub_status?: string | null;
  lead_verification_status?: string | null;
  verification_status?: string | null;
  contact_verification_status?: string | null;
};

export type LeadsDiagnostics = {
  db_ready: boolean;
  initialization_finished_at?: string | null;
  verification_summary?: {
    valid_emails: number;
    invalid_emails: number;
    failed_or_unknown_emails: number;
    missing_verification_fields: number;
  };
  api_base_url?: string;
};

export type LeadsSummaryResponse = {
  total_leads: number;
  high_intent_leads: number;
  top_leads: LeadSummary[];
  diagnostics?: LeadsDiagnostics;
};

export type DashboardMetricsResponse = {
  success: boolean;
  data: {
    total_leads: number;
    high_intent_leads: number;
  };
  meta?: Record<string, unknown>;
};
