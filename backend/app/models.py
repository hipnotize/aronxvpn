from sqlalchemy import Column, Integer, String, Boolean, DateTime, func, UniqueConstraint
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    vpn_uuid = Column(String, unique=True, index=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = (
        UniqueConstraint("code", name="uq_invite_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=False, index=True, unique=True)

    is_used = Column(Boolean, default=False, nullable=False)

    used_by_telegram_id = Column(String, nullable=True)
    used_by_username = Column(String, nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
