import asyncio
import json
import sqlite3
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    """Persistent FSM storage for aiogram using SQLite."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = asyncio.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fsm_states (
                bot_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                destiny TEXT NOT NULL,
                state TEXT,
                data TEXT,
                PRIMARY KEY (bot_id, chat_id, user_id, thread_id, destiny)
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _normalize_state(state: StateType = None) -> Optional[str]:
        if isinstance(state, State):
            return state.state
        return state

    @staticmethod
    def _normalize_thread_id(thread_id: Optional[int]) -> int:
        return thread_id if thread_id is not None else 0

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_value = self._normalize_state(state)
        thread_id = self._normalize_thread_id(key.thread_id)
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO fsm_states (bot_id, chat_id, user_id, thread_id, destiny, state, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, chat_id, user_id, thread_id, destiny)
                DO UPDATE SET state=excluded.state
                """,
                (key.bot_id, key.chat_id, key.user_id, thread_id, key.destiny, state_value, "{}"),
            )
            self._conn.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        thread_id = self._normalize_thread_id(key.thread_id)
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT state
                FROM fsm_states
                WHERE bot_id=? AND chat_id=? AND user_id=? AND thread_id=? AND destiny=?
                """,
                (key.bot_id, key.chat_id, key.user_id, thread_id, key.destiny),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise DataNotDictLikeError(
                f"Data must be a dict, got {type(data).__name__}"
            )

        thread_id = self._normalize_thread_id(key.thread_id)
        payload = json.dumps(data, ensure_ascii=False)
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT INTO fsm_states (bot_id, chat_id, user_id, thread_id, destiny, state, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, chat_id, user_id, thread_id, destiny)
                DO UPDATE SET data=excluded.data
                """,
                (key.bot_id, key.chat_id, key.user_id, thread_id, key.destiny, None, payload),
            )
            self._conn.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        thread_id = self._normalize_thread_id(key.thread_id)
        async with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT data
                FROM fsm_states
                WHERE bot_id=? AND chat_id=? AND user_id=? AND thread_id=? AND destiny=?
                """,
                (key.bot_id, key.chat_id, key.user_id, thread_id, key.destiny),
            )
            row = cursor.fetchone()

            if not row or not row[0]:
                return {}

            try:
                data = json.loads(row[0])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()


class DataNotDictLikeError(TypeError):
    pass
