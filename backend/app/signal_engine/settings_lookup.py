"""Shared helper to load the latest confirmed contract settings."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import ContractSettings


async def get_latest_confirmed_settings(session: AsyncSession) -> ContractSettings | None:
    result = await session.execute(
        select(ContractSettings)
        .where(ContractSettings.is_confirmed == True)  # noqa: E712
        .order_by(ContractSettings.confirmed_at.desc(), ContractSettings.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
