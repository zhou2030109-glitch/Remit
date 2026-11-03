"""归档项目删除：无旧消息也能删除，保留相邻项目，保护忙碌任务。"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.routers import project_router as projects, team_router as team_api
from app.routers import modeling_router, writing_router
from app.services import team_state as team
from app.services.task_bus import task_bus
from app.services.writing_workspace import write_json


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "project/work_dir/delete-test"
    root.mkdir(parents=True)
    write_json(root / ".project.json", {"title": "删除测试", "archived": True})
    (root / "input.csv").write_text("x\n1")
    (root / "paper").mkdir()
    (root / "paper/main.tex").write_text("paper")
    team.record(root, "user", "chat", "你好")
    team_api._io_locks.clear()
    team_api._locks.clear()
    monkeypatch.setattr(
        task_bus, "delete_task_record", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(task_bus, "load_task_messages", AsyncMock(return_value=[]))
    yield root
    modeling_router._scheduled_tasks.discard(root.name)
    writing_router._compiling.discard(root.name)
    writing_router._generations.pop(root.name, None)
    team_api._workers.clear()


@pytest.mark.anyio
async def test_delete_archived_chat_without_legacy_messages(project):
    sibling = project.parent / "keep-test"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep")
    result = await projects.delete_project(
        project.name, projects.ProjectDelete(confirmed=True)
    )
    assert result["deleted"]
    assert not project.exists()
    assert (sibling / "keep.txt").read_text() == "keep"
    assert not await projects.projects()
    task_bus.delete_task_record.assert_awaited_once_with(project.name)
    task_bus.load_task_messages.assert_not_called()


@pytest.mark.anyio
async def test_delete_requires_explicit_confirmation_and_archive(project):
    app = FastAPI()
    app.include_router(projects.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for body in ({}, {"confirmed": False}):
            response = await client.request(
                "DELETE", f"/api/projects/{project.name}", json=body
            )
            assert response.status_code == 422
        write_json(project / ".project.json", {"archived": False})
        response = await client.request(
            "DELETE", f"/api/projects/{project.name}", json={"confirmed": True}
        )
        assert response.status_code == 409
    assert (project / "input.csv").exists()
    task_bus.delete_task_record.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("busy", ["modeling", "compiling", "writing", "chat"])
async def test_delete_refuses_busy_project(project, busy):
    if busy == "modeling":
        modeling_router._scheduled_tasks.add(project.name)
    elif busy == "compiling":
        writing_router._compiling.add(project.name)
    elif busy == "writing":
        writing_router._generations[project.name] = object()
    else:
        team_api._workers[(project.name, "request-id")] = object()
    with pytest.raises(HTTPException) as exc:
        await projects.delete_project(
            project.name, projects.ProjectDelete(confirmed=True)
        )
    assert exc.value.status_code == 409
    assert (project / ".team.sqlite3").is_file()
    task_bus.delete_task_record.assert_not_called()


@pytest.mark.anyio
async def test_stream_ends_after_deletion_and_new_requests_fail(project):
    request = type(
        "Request", (), {"headers": {}, "is_disconnected": AsyncMock(return_value=False)}
    )()
    response = await team_api.stream(project.name, request, 0)
    await anext(response.body_iterator)
    await projects.delete_project(project.name, projects.ProjectDelete(confirmed=True))
    assert "event: deleted" in await anext(response.body_iterator)
    await response.body_iterator.aclose()
    with pytest.raises(HTTPException) as exc:
        await team_api.get_state(project.name)
    assert exc.value.status_code == 404
    assert not project.exists()


@pytest.mark.anyio
async def test_archive_rejects_messages_before_creating_worker(project):
    with pytest.raises(HTTPException) as exc:
        await team_api.send_message(
            project.name,
            team_api.ChatRequest(request_id="test-request", content="你好"),
        )
    assert exc.value.status_code == 409
    assert not team_api.task_busy(project.name)
    assert [event["content"] for event in team.events(project)] == ["你好"]


@pytest.mark.anyio
async def test_delete_waits_for_event_read(project, monkeypatch):
    locked = asyncio.Event()
    release = asyncio.Event()

    async def reader():
        async with team_api.io_lock(project.name):
            locked.set()
            await release.wait()
            assert team.events(project)

    read = asyncio.create_task(reader())
    await locked.wait()
    deleting = asyncio.create_task(
        projects.delete_project(project.name, projects.ProjectDelete(confirmed=True))
    )
    await asyncio.sleep(0)
    assert project.exists() and not deleting.done()
    release.set()
    await asyncio.gather(read, deleting)
    assert not project.exists()


def test_delete_cannot_escape_work_directory(project):
    with pytest.raises(HTTPException):
        projects._remove_project(project.parent)
    assert project.is_dir()
