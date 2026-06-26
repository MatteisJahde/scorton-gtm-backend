import { Mail, UserRound, UserX } from "lucide-react";

type LeadContactFields = {
  contact_name?: string | null;
  contact_role?: string | null;
  verified_email?: string | null;
  contact_status?: "verified" | "review" | "no_contact_found" | "placeholder" | null;
  needs_review?: boolean | null;
  email_status?: string | null;
  buyer_name?: string | null;
  job_title?: string | null;
  work_email?: string | null;
};

function resolveContact(lead: LeadContactFields) {
  const name = (lead.contact_name || lead.buyer_name || "").trim();
  const role = (lead.contact_role || lead.job_title || "").trim();
  const email = (lead.verified_email || lead.work_email || "").trim();
  const status = lead.contact_status || (email ? "verified" : "no_contact_found");
  const needsReview =
    lead.needs_review === true ||
    status === "review" ||
    (lead.email_status || "").toLowerCase() === "review";
  return { name, role, email, status, needsReview };
}

type LeadContactProps = {
  lead: LeadContactFields;
  className?: string;
};

export function LeadContact({ lead, className = "" }: LeadContactProps) {
  const { name, role, email, status, needsReview } = resolveContact(lead);

  if (status === "no_contact_found" || name === "No Contact Found" || !email) {
    return (
      <div className={`space-y-1 text-sm text-muted-foreground ${className}`}>
        <span className="inline-flex items-center gap-1 font-medium">
          <UserX className="h-3.5 w-3.5" />
          No Contact Found
        </span>
        {role && role !== "—" ? <p>{role}</p> : null}
      </div>
    );
  }

  return (
    <div className={`space-y-1 text-sm ${className}`}>
      <p className="inline-flex items-center gap-1 font-medium">
        <UserRound className="h-3.5 w-3.5" />
        {name}
        {needsReview ? (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
            Review
          </span>
        ) : null}
      </p>
      {role ? <p className="text-muted-foreground">{role}</p> : null}
      <a
        href={`mailto:${email}`}
        className="inline-flex items-center gap-1 text-primary hover:underline"
      >
        <Mail className="h-3.5 w-3.5" />
        {email}
      </a>
    </div>
  );
}

export default LeadContact;
