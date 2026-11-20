from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiosqlite

_DB_PATH: str = "orchestrator.db"


def set_db_path(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init_db(path: str | None = None) -> None:
    if path:
        set_db_path(path)
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS video_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                script TEXT NOT NULL,
                style_pack_id TEXT,
                character_pack_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shot_tasks (
                id TEXT PRIMARY KEY,
                video_task_id TEXT NOT NULL,
                shot_index INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                duration REAL NOT NULL DEFAULT 5.0,
                aspect_ratio TEXT NOT NULL DEFAULT '9:16',
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT 'pixverse',
                provider_job_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (video_task_id) REFERENCES video_tasks(id)
            );
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                shot_id TEXT,
                type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS packs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publish_records (
                id TEXT PRIMARY KEY,
                video_task_id TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'youtube',
                platform_video_id TEXT,
                url TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                published_at TEXT,
                FOREIGN KEY (video_task_id) REFERENCES video_tasks(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_records_task_platform
                ON publish_records(video_task_id, platform);
        """)
        await conn.commit()
