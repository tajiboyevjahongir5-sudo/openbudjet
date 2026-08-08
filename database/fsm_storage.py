import json
from typing import Any, Dict, Optional
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType
from sqlalchemy import Column, String, Text, select
from database.models import Base
from database.session import async_session


class FSMState(Base):
    __tablename__ = "fsm_states"
    __table_args__ = {"extend_existing": True}

    key = Column(String(255), primary_key=True, index=True)
    state = Column(String(255), nullable=True)
    data = Column(Text, nullable=True, default="{}")


class PostgresStorage(BaseStorage):
    """Railway restart bo'lganda holatlar saqlanib qoladi (MemoryStorage o'rniga)"""

    @staticmethod
    def _build_key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        db_key = self._build_key(key)
        state_str = state.state if hasattr(state, "state") else state
        async with async_session() as db:
            result = await db.execute(select(FSMState).where(FSMState.key == db_key))
            record = result.scalar_one_or_none()
            if record:
                record.state = state_str
            else:
                record = FSMState(key=db_key, state=state_str, data="{}")
                db.add(record)
            await db.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        db_key = self._build_key(key)
        async with async_session() as db:
            result = await db.execute(select(FSMState).where(FSMState.key == db_key))
            record = result.scalar_one_or_none()
            return record.state if record else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        db_key = self._build_key(key)
        async with async_session() as db:
            result = await db.execute(select(FSMState).where(FSMState.key == db_key))
            record = result.scalar_one_or_none()
            if record:
                record.data = json.dumps(data, ensure_ascii=False)
            else:
                record = FSMState(key=db_key, state=None, data=json.dumps(data, ensure_ascii=False))
                db.add(record)
            await db.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        db_key = self._build_key(key)
        async with async_session() as db:
            result = await db.execute(select(FSMState).where(FSMState.key == db_key))
            record = result.scalar_one_or_none()
            if record and record.data:
                return json.loads(record.data)
            return {}

    async def close(self) -> None:
        pass
