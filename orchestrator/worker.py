from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
# Kept after load_dotenv() so these modules start with .env loaded (E402).
from orchestrator.assets.manager import AssetManager  # noqa: E402
from orchestrator.db import init_db  # noqa: E402
from orchestrator.providers.base import VideoProvider  # noqa: E402
from orchestrator.providers.pixverse import PixVerseProvider  # noqa: E402
from orchestrator.queue.manager import QueueManager  # noqa: E402

MAX_CONCURRENT = 3
MAX_RETRY = 3


def get_provider(provider_name: str = "pixverse") -> VideoProvider:
    if provider_name == "pixverse":
        return PixVerseProvider()
    raise ValueError(f"Unknown provider: {provider_name}")


async def process_one_cycle(db_path: str | None = None) -> None:
    queue = QueueManager(db_path)
    asset_mgr = AssetManager(db_path)

    pending = await queue.get_shots_by_status("pending")
    generating = await queue.get_shots_by_status("generating")
    slots = max(0, MAX_CONCURRENT - len(generating))
    for shot in pending[:slots]:
        provider = get_provider(shot.provider)
        try:
            job_id = await provider.submit(shot)
            await queue.update_shot(shot.id, status="generating", provider_job_id=job_id)
        except Exception:
            count = await queue.increment_retry(shot.id)
            if count >= MAX_RETRY:
                await queue.update_shot(shot.id, status="failed")

    generating = await queue.get_shots_by_status("generating")
    for shot in generating:
        if not shot.provider_job_id:
            continue
        provider = get_provider(shot.provider)
        try:
            result = await provider.poll(shot.provider_job_id)
            if result.status == "finished" and result.video_url:
                dest = f"output/shots/{shot.id}/clip.mp4"
                await asset_mgr.download_and_register(
                    url=result.video_url,
                    dest_path=dest,
                    shot_id=shot.id,
                    asset_type="video_clip",
                )
                await queue.update_shot(shot.id, status="review_pending")
            elif result.status == "failed":
                count = await queue.increment_retry(shot.id)
                if count >= MAX_RETRY:
                    await queue.update_shot(shot.id, status="failed")
                else:
                    await queue.update_shot(shot.id, status="pending", provider_job_id=None)
        except Exception:
            pass


async def run(db_path: str | None = None, interval: int = 10) -> None:
    await init_db(db_path)
    while True:
        await process_one_cycle(db_path=db_path)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(run())
