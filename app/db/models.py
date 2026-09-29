"""
SQLAlchemy Models for JTS-PowerTool database tables.
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, UniqueConstraint, func, Index, ForeignKey
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class ChannelFolder(Base):
    """
    Permanent folder to group channels / projects in the Channel API Key section.
    """
    __tablename__ = "channel_folders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ChannelSecretMapping(Base):
    """
    Maps Slack Channel (1 channel = 1 project) to AWS Secrets Manager secret references.
    CRITICAL SECURITY RULE: The actual secret key is NEVER stored here. Only the AWS Secret ARN/Name.
    """
    __tablename__ = "channel_secret_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(255), nullable=False, index=True)
    channel_name = Column(String(255), nullable=True)
    provider = Column(String(50), nullable=False)
    aws_secret_name = Column(String(255), nullable=False)
    aws_secret_arn = Column(String(512), nullable=True)
    status = Column(String(50), default="active", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by = Column(String(255), default="admin", nullable=True)

    __table_args__ = (
        UniqueConstraint("channel_id", "provider", name="uq_channel_provider"),
        Index("idx_channel_secret_lookup", "channel_id"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "provider": self.provider,
            "aws_secret_name": self.aws_secret_name,
            "aws_secret_arn": self.aws_secret_arn,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
        }


class ChannelMetadata(Base):
    """
    Stores human-readable names and types for Slack channels / projects.
    """
    __tablename__ = "channel_metadata"

    channel_id = Column(String(255), primary_key=True)
    channel_name = Column(String(255), nullable=False)
    channel_type = Column(String(50), default="channel", nullable=True)
    folder_id = Column(Integer, ForeignKey("channel_folders.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self):
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_type": self.channel_type,
            "folder_id": self.folder_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Organization(Base):
    """
    Stores tenant organization profiles.
    """
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workspace_id = Column(String(64), nullable=True)
    workspace_name = Column(String(255), nullable=True)
    name = Column(String(255), nullable=False)
    poc = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    billing_email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "poc": self.poc or "",
            "phone": self.phone or "",
            "email": self.email or "",
            "billing_email": self.billing_email or "",
            "address": self.address or "",
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }



