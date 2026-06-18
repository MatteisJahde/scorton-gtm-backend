#!/usr/bin/env python3
"""
Preview multi-channel outreach drafts for the top qualified leads.

Usage:
    python print_first_draft.py
"""

from __future__ import annotations

import textwrap
from itertools import zip_longest

from outreach_engine import (
    OutreachTemplateEngine,
    QualifiedCompanyLoader,
    SENDER_NAME,
    build_problem_profile,
    build_template_context,
)

COLUMN_WIDTH = 36
PANEL_GAP = "  │  "
PREVIEW_LEAD_COUNT = 2


def wrap_column(text: str, width: int = COLUMN_WIDTH) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(paragraph, width=width) or [""])
    return lines


def print_side_by_side(left_title: str, left_body: str, right_title: str, right_body: str) -> None:
    left_lines = [left_title, "─" * COLUMN_WIDTH, *wrap_column(left_body)]
    right_lines = [right_title, "─" * COLUMN_WIDTH, *wrap_column(right_body)]

    for left, right in zip_longest(left_lines, right_lines, fillvalue=""):
        print(f"{left:<{COLUMN_WIDTH}}{PANEL_GAP}{right}")


def print_section(title: str, content: str, *, width: int = 72) -> None:
    rule = "=" * width
    print(rule)
    print(title)
    print(rule)
    print(content)
    print()


def render_email_preview(company, templates) -> tuple[str, str, str]:
    context = build_template_context(company)
    subject = templates.render_subject(context)
    body = templates.render_body(context)
    preview = (
        f"From:    {SENDER_NAME}\n"
        f"To:      {company.recipient_email}\n"
        f"Subject: {subject}\n"
        f"\n"
        f"{body}"
    )
    return templates.render_linkedin_connection(context), subject, preview


def preview_lead(company, templates, index: int) -> None:
    linkedin_note, email_subject, email_preview = render_email_preview(company, templates)
    profile = build_problem_profile(company)

    lead_summary = textwrap.dedent(
        f"""
        Company:     {company.company_name}
        Contact:     {company.buyer_name} ({company.job_title})
        Industry:    {company.industry}
        Focus:       {company.cybersecurity_focus}
        Tier:        {company.priority_tier}
        """
    ).strip()

    print_section(f"LEAD — INDEX {index}", lead_summary)
    print_section(
        f"INDEX {index} — PROBLEM STATEMENT (focus-tailored)",
        f"{profile.opening_insight}\n\n{profile.problem_detail}",
    )
    print("=" * 72)
    print(f"INDEX {index} — FINALIZED COPY (BOTH CHANNELS)")
    print("=" * 72)
    print()
    print_side_by_side(
        "LINKEDIN CONNECTION NOTE",
        linkedin_note,
        "COLD EMAIL",
        email_preview,
    )
    print()
    print_section(f"INDEX {index} — LINKEDIN (full)", linkedin_note)
    print_section(f"INDEX {index} — COLD EMAIL (full)", email_preview)


def main() -> None:
    loader = QualifiedCompanyLoader()
    companies = loader.load(limit=PREVIEW_LEAD_COUNT)
    if len(companies) < PREVIEW_LEAD_COUNT:
        raise SystemExit(f"Need at least {PREVIEW_LEAD_COUNT} qualified companies in dataset.")

    templates = OutreachTemplateEngine()

    for index, company in enumerate(companies):
        preview_lead(company, templates, index)

    comparison_lines = ["PROBLEM STATEMENT COMPARISON — INDEX 0 vs INDEX 1", ""]
    for index, company in enumerate(companies):
        profile = build_problem_profile(company)
        comparison_lines.extend(
            [
                f"[Index {index}] {company.company_name}",
                f"Focus: {profile.focus}",
                "",
                profile.opening_insight,
                "",
                profile.problem_detail,
                "",
                "─" * 72,
                "",
            ]
        )

    print_section("TAILORING PROOF", "\n".join(comparison_lines).strip())


if __name__ == "__main__":
    main()
