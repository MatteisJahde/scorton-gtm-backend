import { ExternalLink, Link2Off } from "lucide-react";

type LeadWebsiteFields = {
  website?: string | null;
  company_website?: string | null;
  domain?: string | null;
  website_status?: "ready" | "unavailable" | null;
  website_link?: string | null;
};

function normalizeWebsite(raw?: string | null): string {
  const value = (raw || "").trim();
  if (!value) return "";
  if (value.startsWith("http://") || value.startsWith("https://")) return value;
  return `https://${value}`;
}

function domainFromWebsite(website: string): string {
  try {
    const host = new URL(website).hostname.toLowerCase();
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return website;
  }
}

function resolveWebsite(lead: LeadWebsiteFields) {
  const href = normalizeWebsite(
    lead.website_link || lead.website || lead.company_website || "",
  );
  const status = lead.website_status || (href ? "ready" : "unavailable");
  const label = (lead.domain || domainFromWebsite(href) || "").trim();
  return { href, status, label };
}

type LeadWebsiteLinkProps = {
  lead: LeadWebsiteFields;
  className?: string;
};

export function LeadWebsiteLink({ lead, className = "" }: LeadWebsiteLinkProps) {
  const { href, status, label } = resolveWebsite(lead);

  if (status !== "ready" || !href || !label) {
    return (
      <span
        className={`inline-flex items-center gap-1 text-sm text-muted-foreground ${className}`}
        title="Website link unavailable"
      >
        <Link2Off className="h-3.5 w-3.5" />
        Link Unavailable
      </span>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center gap-1 text-sm text-primary hover:underline ${className}`}
      title={href}
    >
      <ExternalLink className="h-3.5 w-3.5" />
      {label}
    </a>
  );
}

export default LeadWebsiteLink;
