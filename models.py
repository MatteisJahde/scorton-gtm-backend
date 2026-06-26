from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)

    # PDL company dataset fields
    website = Column(String, nullable=True)
    industry = Column(String, nullable=False)
    size = Column(String, nullable=True)
    locality = Column(String, nullable=True)
    country = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)

    # Week-to-week rotation tracking
    is_targeted = Column(Boolean, default=False, index=True)
    week_assigned = Column(Integer, nullable=True, index=True)

    # Legacy GTM pipeline fields (kept for existing endpoints)
    city = Column(String, nullable=True)
    employee_count = Column(Integer, nullable=True)
    score = Column(Integer, default=0)
    priority_tier = Column(String, default="low")

    contacts = relationship("Contact", back_populates="company")


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String, nullable=False)
    title = Column(String, nullable=False)
    role_type = Column(String, nullable=False)
    email = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)

    company = relationship("Company", back_populates="contacts")


class TargetAccount(Base):
    __tablename__ = "target_accounts"
    __table_args__ = (
        UniqueConstraint(
            "company_name",
            "website",
            name="uq_target_accounts_company_identity",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)
    company_name = Column(String, nullable=False)
    website = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    city = Column(String, nullable=False)
    employee_count = Column(Integer, nullable=True)
    funding = Column(String, nullable=True)
    revenue = Column(String, nullable=True)
    funding_amount = Column(String, nullable=True)
    funding_stage = Column(String, nullable=True)
    revenue_range = Column(String, nullable=True)
    buyer_name = Column(String, default="TBD")
    job_title = Column(String, nullable=False)
    work_email = Column(String, nullable=True)
    email_status = Column(String, default="Unverified")
    lead_verification_status = Column(String, default="Unverified")
    verification_status = Column(String, nullable=True)
    contact_verification_status = Column(String, nullable=True)
    contact_status = Column(String, nullable=True)
    enrichment_provider = Column(String, nullable=True)
    linkedin_url = Column(String, default="TBD")
    company_linkedin_url = Column(String, nullable=True)
    city_validated = Column(Boolean, default=False)
    ai_signal = Column(Integer, default=0)
    risk_signal = Column(Integer, default=0)
    buying_signal = Column(Integer, default=0)
    trust_opportunity_score = Column(Integer, default=0)
    icp_score = Column(Integer, default=0)
    priority_tier = Column(String, default="Tier 3")
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"
    __table_args__ = (UniqueConstraint("email", name="uq_email_suppressions_email"),)

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False, default="hard_bounce")
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
