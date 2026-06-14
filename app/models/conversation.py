"""Conversation memory — one row per chat, one row per message.

This is Aura's memory: every Telegram message and every reply is stored here so
the model gets the recent history as context on each turn. SQLite by default;
the same models run on Postgres unchanged.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="telegram", index=True
    )
    # Channel-native chat identifier (e.g. the Telegram chat_id).
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index(
            "uq_conv_channel_chat_thread",
            "channel_type", "chat_id", "thread_id",
            unique=True,
            postgresql_where=text("thread_id IS NOT NULL"),
            sqlite_where=text("thread_id IS NOT NULL"),
        ),
        Index(
            "uq_conv_channel_chat_main",
            "channel_type", "chat_id",
            unique=True,
            postgresql_where=text("thread_id IS NULL"),
            sqlite_where=text("thread_id IS NULL"),
        ),
    )

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", lazy="select"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )
