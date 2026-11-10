from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.db import init_db
from orchestrator.models import ShotTask, VideoTask
from orchestrator.queue.manager import QueueManager
from orchestrator.worker import process_one_cycle


@pytest.fixture
async def setup(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    queue = QueueManager(db_path)
    task = VideoTask(title="t", script="s", status="processing")
    await queue.create_video_task(task)
    shot = ShotTask(video_task_id=task.id, shot_index=0, prompt="海边")
    await queue.create_shots([shot])
    return db_path, queue, shot


@pytest.mark.asyncio
async def test_pending_shot_submitted(setup, tmp_path):
    db_path, queue, shot = setup
    mock_provider = AsyncMock()
    mock_provider.submit = AsyncMock(return_value="pv-job-001")
    with patch("orchestrator.worker.get_provider", return_value=mock_provider):
        await process_one_cycle(db_path=db_path)
    updated = await queue.get_shot(shot.id)
    assert updated.status == "generating"
    assert updated.provider_job_id == "pv-job-001"


@pytest.mark.asyncio
async def test_generating_shot_polled_to_review(setup, tmp_path):
    db_path, queue, shot = setup
    await queue.update_shot(shot.id, status="generating", provider_job_id="pv-job-002")
    mock_provider = AsyncMock()
    from orchestrator.providers.base import ProviderResult
    mock_provider.poll = AsyncMock(
        return_value=ProviderResult(
            status="finished",
            video_url="https://cdn.example.com/video.mp4",
        )
    )
    mock_asset_mgr = AsyncMock()
    mock_asset_mgr.download_and_register = AsyncMock()
    with patch("orchestrator.worker.get_provider", return_value=mock_provider), \
         patch("orchestrator.worker.AssetManager", return_value=mock_asset_mgr):
        await process_one_cycle(db_path=db_path)
    updated = await queue.get_shot(shot.id)
    assert updated.status == "review_pending"


@pytest.mark.parametrize("generating_count", [2, 3, 4])
async def test_pending_submissions_respect_available_slots(setup, generating_count):
    db_path, queue, shot = setup
    generating = [
        ShotTask(video_task_id=shot.video_task_id, shot_index=i + 1, prompt="busy",
                 status="generating", provider_job_id=f"job-{i}")
        for i in range(generating_count)
    ]
    pending = [
        ShotTask(video_task_id=shot.video_task_id, shot_index=i + 10, prompt="waiting")
        for i in range(4)
    ]
    await queue.create_shots(generating + pending)
    provider = AsyncMock()
    provider.submit.return_value = "new-job"
    from orchestrator.providers.base import ProviderResult
    provider.poll.return_value = ProviderResult(status="processing")

    with patch("orchestrator.worker.get_provider", return_value=provider):
        await process_one_cycle(db_path=db_path)

    assert provider.submit.await_count == max(0, 3 - generating_count)
    assert len(await queue.get_shots_by_status("generating")) == max(3, generating_count)
