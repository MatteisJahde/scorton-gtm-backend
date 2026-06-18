#!/usr/bin/env python3
"""
Outreach engine — scalable email draft generation pipeline.

Loads vetted accounts from the qualified companies dataset, renders personalized
drafts via Jinja2 templates, and persists them for human approval before send.

Usage:
    python outreach_engine.py

Future integrations:
    - Swap TemplateContentGenerator for LLMContentGenerator (OpenAI / Anthropic).
    - Plug EmailSender implementations (SendGrid / Resend) into OutreachPipeline.send().
"""

from __future__ import annotations

import csv
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from jinja2 import Environment, FileSystemLoader, select_autoescape

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET_PATH = PROJECT_ROOT / "data" / "top_100_qualified_accounts.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "email_drafts.json"
TEMPLATE_DIR = PROJECT_ROOT / "templates" / "outreach"

SENDER_NAME = "The Scorton team."
DRAFT_STATUS_PENDING = "pending_approval"

INDUSTRY_CYBERSECURITY_FOCUS = {
    "Financial Services": "AI risk governance and financial data security",
    "Insurance": "regulatory compliance, underwriting risk controls, and AI governance",
    "Accounting": "workflow integrity, audit readiness, and AI monitoring",
}

NOTES_FOCUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("compliance-heavy organization", "regulatory compliance and AI governance"),
    ("ai adoption likely", "secure AI adoption and model risk management"),
    ("workflow automation candidate", "workflow security and automated control monitoring"),
)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualifiedCompany:
    """Normalized account row from the qualified companies dataset."""

    id: int
    company_name: str
    recipient_email: str
    buyer_name: str
    job_title: str
    industry: str
    city: str
    notes: str
    priority_tier: str
    cybersecurity_focus: str


@dataclass
class EmailDraft:
    """Outbound email draft awaiting approval or send."""

    id: int
    company_name: str
    recipient_email: str
    subject: str
    body: str
    status: str = DRAFT_STATUS_PENDING
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company_name": self.company_name,
            "recipient_email": self.recipient_email,
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
        }


@dataclass(frozen=True)
class FocusNarrative:
    """Problem framing keyed to a lead's cybersecurity focus area."""

    challenge: str
    problem: str
    diagnostic_label: str
    authority_bridge: str


@dataclass(frozen=True)
class ProblemProfile:
    """Sector-specific bottleneck framing for expert-led outreach."""

    focus: str
    specific_challenge: str
    specific_problem: str
    specific_problem_short: str
    opening_insight: str
    problem_detail: str
    authority_bridge: str
    diagnostic_cta: str


FOCUS_NARRATIVE_RULES: tuple[tuple[tuple[str, ...], FocusNarrative], ...] = (
    (
        ("secure ai adoption", "model risk"),
        FocusNarrative(
            challenge="model input governance breaking down as models move from sandbox to production",
            problem=(
                "ungoverned training data and inference inputs reaching client-facing "
                "workflows without lineage tracking"
            ),
            diagnostic_label="model input governance gaps",
            authority_bridge=(
                "At Scorton, we automate the model governance and pipeline security "
                "layers that help teams like yours bridge that gap without adding "
                "manual compliance overhead."
            ),
        ),
    ),
    (
        ("regulatory compliance", "ai governance"),
        FocusNarrative(
            challenge=(
                "control evidence fragmenting across AI governance and "
                "compliance workflows"
            ),
            problem=(
                "audit-trail gaps widening as AI governance platforms deploy faster "
                "than compliance teams can validate evidence chains"
            ),
            diagnostic_label="AI governance compliance gaps",
            authority_bridge=(
                "At Scorton, we automate control-evidence and audit-trail layers "
                "across data pipelines — giving teams like yours the visibility to "
                "close those gaps without adding manual compliance overhead."
            ),
        ),
    ),
    (
        ("workflow security", "automated control"),
        FocusNarrative(
            challenge=(
                "automated workflow changes outpacing access control and "
                "monitoring reviews"
            ),
            problem=(
                "control drift between audit cycles as automation ships faster "
                "than governance sign-off on new data paths"
            ),
            diagnostic_label="workflow control drift",
            authority_bridge=(
                "At Scorton, we automate workflow control monitoring across data "
                "pipelines — helping teams like yours catch drift before it becomes "
                "an audit finding, without slowing delivery."
            ),
        ),
    ),
    (
        ("ai governance platform", "control design"),
        FocusNarrative(
            challenge=(
                "policy gaps between AI platform evaluation and production "
                "control design"
            ),
            problem=(
                "teams selecting AI governance platforms without a shared control "
                "baseline for how model inputs are validated at deployment"
            ),
            diagnostic_label="AI platform control design gaps",
            authority_bridge=(
                "At Scorton, we automate the policy and governance layers between "
                "platform evaluation and production — so teams like yours ship "
                "with a consistent baseline, not a manual review bottleneck."
            ),
        ),
    ),
    (
        ("compliance audit readiness", "control evidence"),
        FocusNarrative(
            challenge=(
                "control evidence degrading faster than audit preparation cycles "
                "can reconcile"
            ),
            problem=(
                "incomplete audit trails across third-party data flows when "
                "evidence collection still relies on manual reconciliation"
            ),
            diagnostic_label="audit evidence gaps",
            authority_bridge=(
                "At Scorton, we automate continuous control-evidence collection "
                "across data pipelines — so teams like yours enter audits with "
                "defensible trails, not last-minute spreadsheet reconstruction."
            ),
        ),
    ),
    (
        ("digital transformation",),
        FocusNarrative(
            challenge=(
                "legacy security controls failing to cover new transformation "
                "data paths"
            ),
            problem=(
                "sensitive data entering modernized pipelines through "
                "transformation shortcuts that bypass existing access controls"
            ),
            diagnostic_label="transformation data-path gaps",
            authority_bridge=(
                "At Scorton, we automate pipeline visibility and data-flow controls "
                "across transformation initiatives — helping teams like yours "
                "modernize without losing control coverage."
            ),
        ),
    ),
    (
        ("endpoint security",),
        FocusNarrative(
            challenge="vulnerabilities in public endpoints exposed through rapid API expansion",
            problem=(
                "unpatched or misconfigured public endpoints becoming the entry "
                "point for data exfiltration before internal monitoring detects it"
            ),
            diagnostic_label="public endpoint vulnerabilities",
            authority_bridge=(
                "At Scorton, we automate endpoint discovery and exposure monitoring "
                "across data pipelines — giving teams like yours visibility into "
                "public attack surfaces without manual scanning overhead."
            ),
        ),
    ),
)


@dataclass(frozen=True)
class TemplateContext:
    """Variables injected into outreach templates."""

    company_name: str
    buyer_name: str
    buyer_first_name: str
    job_title: str
    industry: str
    city: str
    cybersecurity_focus: str
    subject_topic: str
    opening_insight: str
    problem_detail: str
    authority_bridge: str
    diagnostic_cta: str
    priority_tier: str
    sender_name: str


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


class QualifiedCompanyLoader:
    """Loads and normalizes vetted accounts from CSV."""

    def __init__(self, dataset_path: Path = DEFAULT_DATASET_PATH) -> None:
        self.dataset_path = dataset_path

    def load(self, limit: int | None = 100) -> list[QualifiedCompany]:
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Qualified dataset not found: {self.dataset_path}")

        with self.dataset_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        companies = [self._row_to_company(row, index=index) for index, row in enumerate(rows, start=1)]
        if limit is not None:
            return companies[:limit]
        return companies

    def _row_to_company(self, row: dict[str, str], index: int) -> QualifiedCompany:
        company_name = (row.get("company_name") or "").strip()
        recipient_email = (row.get("work_email") or "").strip()
        if not company_name:
            raise ValueError(f"Row {index} is missing company_name.")
        if not recipient_email:
            raise ValueError(f"Row {index} ({company_name}) is missing work_email.")

        industry = (row.get("industry") or "your industry").strip()
        notes = (row.get("notes") or "").strip()

        return QualifiedCompany(
            id=int(row.get("id") or index),
            company_name=company_name,
            recipient_email=recipient_email,
            buyer_name=(row.get("buyer_name") or "there").strip(),
            job_title=(row.get("job_title") or "leader").strip(),
            industry=industry,
            city=(row.get("city") or "").strip(),
            notes=notes,
            priority_tier=(row.get("priority_tier") or "").strip(),
            cybersecurity_focus=derive_cybersecurity_focus(industry=industry, notes=notes),
        )


def derive_cybersecurity_focus(*, industry: str, notes: str) -> str:
    """Map account signals to a human-readable cybersecurity focus area."""
    lowered_notes = notes.lower()
    for pattern, focus in NOTES_FOCUS_PATTERNS:
        if pattern in lowered_notes:
            return focus

    if " — " in notes:
        detail = notes.split(" — ", 1)[1].strip().lower()
        if "ai governance" in detail:
            return "AI governance platform evaluation and control design"
        if "compliance audit" in detail:
            return "compliance audit readiness and control evidence"
        if "digital transformation" in detail:
            return "security for digital transformation initiatives"

    return INDUSTRY_CYBERSECURITY_FOCUS.get(
        industry,
        "AI and cybersecurity risk management",
    )


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class OutreachTemplateEngine:
    """Renders multi-channel outreach templates with Jinja2."""

    def __init__(self, template_dir: Path = TEMPLATE_DIR) -> None:
        self._environment = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(default_for_string=False),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._subject_template = self._environment.get_template("subject.j2")
        self._body_template = self._environment.get_template("body.j2")
        self._linkedin_template = self._environment.get_template("linkedin_connection.j2")

    def render_subject(self, context: TemplateContext) -> str:
        return self._subject_template.render(**asdict(context)).strip()

    def render_body(self, context: TemplateContext) -> str:
        return self._body_template.render(**asdict(context)).strip()

    def render_linkedin_connection(self, context: TemplateContext) -> str:
        note = self._linkedin_template.render(**asdict(context)).strip()
        if len(note) > 200:
            raise ValueError(
                f"LinkedIn connection note exceeds 200 characters ({len(note)}): {note!r}"
            )
        return note


# Backwards-compatible alias
EmailTemplateEngine = OutreachTemplateEngine


def build_subject_topic(cybersecurity_focus: str) -> str:
    """Casual, lowercase subject prefix — no internal pipeline vocabulary."""
    lowered = cybersecurity_focus.lower()
    if "workflow" in lowered or "pipeline" in lowered or "monitoring" in lowered:
        return "data pipeline infrastructure"
    if "compliance" in lowered or "regulatory" in lowered or "audit" in lowered:
        return "compliance data flows"
    if "ai adoption" in lowered or "model risk" in lowered or "ai governance" in lowered:
        return "secure AI adoption"
    if "digital transformation" in lowered:
        return "infra hardening"
    return shorten_cybersecurity_focus(cybersecurity_focus, max_chars=36).lower()


def shorten_cybersecurity_focus(focus: str, *, max_chars: int = 48) -> str:
    """Compact focus line for subject lines and internal metadata."""
    if len(focus) <= max_chars:
        return focus

    if " and " in focus:
        lead_phrase = focus.split(" and ", 1)[0]
        if len(lead_phrase) <= max_chars:
            return lead_phrase

    trimmed = focus[: max_chars - 1].rsplit(" ", 1)[0]
    return f"{trimmed}…"


def resolve_focus_narrative(focus: str) -> FocusNarrative:
    """Select problem framing from the lead's cybersecurity focus field."""
    lowered = focus.lower()
    for keywords, narrative in FOCUS_NARRATIVE_RULES:
        if all(keyword in lowered for keyword in keywords):
            return narrative

    return FocusNarrative(
        challenge=f"control gaps emerging as teams scale {focus.lower()}",
        problem=(
            f"security blind spots specific to {focus.lower()} surfacing only "
            f"after production deployment, not during design review"
        ),
        diagnostic_label=f"{shorten_cybersecurity_focus(focus, max_chars=28).lower()} gaps",
        authority_bridge=(
            "At Scorton, we automate pipeline visibility and control layers "
            "aligned to your focus area — helping teams like yours close gaps "
            "without adding manual compliance overhead."
        ),
    )


def build_problem_profile(company: QualifiedCompany) -> ProblemProfile:
    """Build a focus-tailored problem narrative from the lead's database focus field."""
    focus = company.cybersecurity_focus
    industry = company.industry
    narrative = resolve_focus_narrative(focus)

    opening_insight = (
        f"For {industry} teams where {focus} is the priority, "
        f"{narrative.challenge} is becoming the first breaking point — "
        f"especially under GTM velocity pressure."
    )
    problem_detail = (
        f"In environments centered on {focus}, the specific bottleneck is "
        f"{narrative.problem} — typically surfacing after release, not during "
        f"architecture review."
    )
    diagnostic_cta = (
        f"I put together a 2-minute diagnostic breakdown of how "
        f"{company.company_name} could specifically resolve these "
        f"{narrative.diagnostic_label}. Worth sending over?"
    )

    return ProblemProfile(
        focus=focus,
        specific_challenge=narrative.challenge,
        specific_problem=narrative.problem,
        specific_problem_short=narrative.diagnostic_label,
        opening_insight=opening_insight,
        problem_detail=problem_detail,
        authority_bridge=narrative.authority_bridge,
        diagnostic_cta=diagnostic_cta,
    )


def build_template_context(
    company: QualifiedCompany,
    *,
    sender_name: str = SENDER_NAME,
) -> TemplateContext:
    first_name = company.buyer_name.split()[0] if company.buyer_name else "there"
    profile = build_problem_profile(company)
    return TemplateContext(
        company_name=company.company_name,
        buyer_name=company.buyer_name,
        buyer_first_name=first_name,
        job_title=company.job_title,
        industry=company.industry,
        city=company.city,
        cybersecurity_focus=company.cybersecurity_focus,
        subject_topic=build_subject_topic(company.cybersecurity_focus),
        opening_insight=profile.opening_insight,
        problem_detail=profile.problem_detail,
        authority_bridge=profile.authority_bridge,
        diagnostic_cta=profile.diagnostic_cta,
        priority_tier=company.priority_tier,
        sender_name=sender_name,
    )


# ---------------------------------------------------------------------------
# Content generation (template today, LLM tomorrow)
# ---------------------------------------------------------------------------


class ContentGenerator(Protocol):
    """Produces subject/body content for a single account."""

    def generate(self, company: QualifiedCompany) -> tuple[str, str]:
        ...


class TemplateContentGenerator:
    """Default generator — deterministic Jinja2 rendering."""

    def __init__(
        self,
        template_engine: EmailTemplateEngine | None = None,
        *,
        sender_name: str = SENDER_NAME,
    ) -> None:
        self.template_engine = template_engine or EmailTemplateEngine()
        self.sender_name = sender_name

    def generate(self, company: QualifiedCompany) -> tuple[str, str]:
        context = build_template_context(
            company,
            sender_name=self.sender_name,
        )
        subject = self.template_engine.render_subject(context)
        body = self.template_engine.render_body(context)
        return subject, body


class LLMProvider(ABC):
    """Interface for future OpenAI / Anthropic integrations."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return model text completion."""


class LLMContentGenerator:
    """
    Optional LLM-backed generator.

    Wire an LLMProvider implementation and prompt templates to replace
  TemplateContentGenerator without changing the pipeline.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        system_prompt: str,
        user_prompt_template: str,
        fallback: ContentGenerator | None = None,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.user_prompt_template = user_prompt_template
        self.fallback = fallback or TemplateContentGenerator()

    def generate(self, company: QualifiedCompany) -> tuple[str, str]:
        context = build_template_context(company)
        prompt = self.user_prompt_template.format(**asdict(context))
        completion = self.provider.complete(self.system_prompt, prompt)

        try:
            payload = json.loads(completion)
            return str(payload["subject"]).strip(), str(payload["body"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError):
            return self.fallback.generate(company)


# ---------------------------------------------------------------------------
# Draft persistence
# ---------------------------------------------------------------------------


class DraftRepository:
    """Reads and writes draft records to local JSON."""

    def __init__(self, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
        self.output_path = output_path

    def save(self, drafts: Iterable[EmailDraft]) -> Path:
        records = [draft.to_record() for draft in drafts]
        self.output_path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return self.output_path

    def load(self) -> list[dict[str, Any]]:
        if not self.output_path.exists():
            return []
        return json.loads(self.output_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Email delivery (future)
# ---------------------------------------------------------------------------


class EmailSender(ABC):
    """Interface for future SendGrid / Resend integrations."""

    @abstractmethod
    def send(self, draft: EmailDraft) -> dict[str, Any]:
        """Send an approved draft and return provider metadata."""


class DraftGenerator:
    """Creates EmailDraft objects from qualified companies."""

    def __init__(self, content_generator: ContentGenerator | None = None) -> None:
        self.content_generator = content_generator or TemplateContentGenerator()

    def generate_all(self, companies: Iterable[QualifiedCompany]) -> list[EmailDraft]:
        drafts: list[EmailDraft] = []
        for sequence_id, company in enumerate(companies, start=1):
            subject, body = self.content_generator.generate(company)
            drafts.append(
                EmailDraft(
                    id=sequence_id,
                    company_name=company.company_name,
                    recipient_email=company.recipient_email,
                    subject=subject,
                    body=body,
                    metadata={
                        "source_account_id": company.id,
                        "buyer_name": company.buyer_name,
                        "job_title": company.job_title,
                        "industry": company.industry,
                        "cybersecurity_focus": company.cybersecurity_focus,
                        "priority_tier": company.priority_tier,
                    },
                )
            )
        return drafts


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


class OutreachPipeline:
    """End-to-end draft generation workflow."""

    def __init__(
        self,
        loader: QualifiedCompanyLoader | None = None,
        generator: DraftGenerator | None = None,
        repository: DraftRepository | None = None,
        email_sender: EmailSender | None = None,
    ) -> None:
        self.loader = loader or QualifiedCompanyLoader()
        self.generator = generator or DraftGenerator()
        self.repository = repository or DraftRepository()
        self.email_sender = email_sender

    def run(self, *, limit: int = 100) -> list[EmailDraft]:
        companies = self.loader.load(limit=limit)
        drafts = self.generator.generate_all(companies)
        self.repository.save(drafts)
        return drafts

    def send_approved(self, drafts: Iterable[EmailDraft]) -> list[dict[str, Any]]:
        if self.email_sender is None:
            raise RuntimeError("No EmailSender configured.")

        results: list[dict[str, Any]] = []
        for draft in drafts:
            if draft.status != "approved":
                continue
            results.append(self.email_sender.send(draft))
        return results


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    pipeline = OutreachPipeline()
    drafts = pipeline.run(limit=100)
    output_path = pipeline.repository.output_path

    print(f"Loaded {len(drafts)} qualified companies from {DEFAULT_DATASET_PATH.name}")
    print(f"Generated {len(drafts)} email drafts -> {output_path}")
    if drafts:
        sample = drafts[0]
        print("\nSample draft:")
        print(f"  To: {sample.recipient_email}")
        print(f"  Subject: {sample.subject}")
        print(f"  Status: {sample.status}")


if __name__ == "__main__":
    main()
