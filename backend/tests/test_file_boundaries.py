"""文件 API 与批量上传的目录、名称、资源限制回归。"""

import asyncio
import re
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, UploadFile

from app.config.setting import settings
from app.routers.files_router import router
from app.services.task_intake import UploadLimitError, persist_uploads
from app.utils.common_utils import create_task_id, create_work_dir, get_work_dir


def upload(name: str, body: bytes = b"data") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(body))


@pytest.mark.parametrize(
    "task_id", ["../outside", "..\\outside", "/tmp", "C:\\Windows", "a/b"]
)
def test_directory_helpers_reject_path_identifiers(tmp_path, monkeypatch, task_id):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        create_work_dir(task_id)
    with pytest.raises(ValueError):
        get_work_dir(task_id)


def test_create_task_id_is_safe_and_uses_a_random_suffix():
    first = create_task_id()
    second = create_task_id()

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", first)
    assert first != second


def test_file_api_cannot_list_or_open_an_external_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "private.txt").write_text("synthetic")
    app = FastAPI()
    app.include_router(router)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for endpoint in (
                "/files",
                "/open_folder",
                "/download_url",
                "/download_all_url",
            ):
                response = await client.get(
                    endpoint,
                    params={"task_id": str(external), "filename": "private.txt"},
                )
                assert response.status_code == 400
                assert "private.txt" not in response.text
            response = await client.get("/files", params={"task_id": "missing"})
            assert response.status_code == 404

    asyncio.run(scenario())


def test_valid_download_encodes_special_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = Path(create_work_dir("safe"))
    (directory / "data #1.csv").write_text("a\n1")
    app = FastAPI()
    app.include_router(router)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            good = await client.get(
                "/download_url", params={"task_id": "safe", "filename": "data #1.csv"}
            )
            assert good.status_code == 200
            assert good.json()["download_url"].endswith("/safe/data%20%231.csv")
            bad = await client.get(
                "/download_url",
                params={"task_id": "safe", "filename": "../private.txt"},
            )
            assert bad.status_code == 400

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name",
    [
        "../file",
        "..\\file",
        "a:b.csv",
        "NUL.csv",
        "CON",
        "file.",
        ".env",
        "workflow_state.json",
        "ques1_quality_report.json",
    ],
)
def test_upload_rejects_unsafe_and_reserved_names(tmp_path, name):
    with pytest.raises(ValueError):
        asyncio.run(persist_uploads([upload(name)], tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_same_name_uploads_are_rejected_before_any_write(tmp_path):
    with pytest.raises(ValueError, match="重复"):
        asyncio.run(
            persist_uploads(
                [upload("data.csv", b"one"), upload("DATA.csv", b"two")], tmp_path
            )
        )
    assert list(tmp_path.iterdir()) == []


def test_upload_never_overwrites_existing_file(tmp_path):
    (tmp_path / "data.csv").write_bytes(b"original")
    with pytest.raises(ValueError):
        asyncio.run(persist_uploads([upload("data.csv")], tmp_path))
    assert (tmp_path / "data.csv").read_bytes() == b"original"


@pytest.mark.parametrize(
    "single,total,bodies", [(4, 20, [b"ok", b"large"]), (10, 6, [b"1234", b"5678"])]
)
def test_size_failure_rolls_back_the_entire_batch(
    tmp_path, monkeypatch, single, total, bodies
):
    monkeypatch.setattr(settings, "UPLOAD_MAX_FILE_BYTES", single)
    monkeypatch.setattr(settings, "UPLOAD_MAX_TOTAL_BYTES", total)
    files = [upload(f"{i}.csv", body) for i, body in enumerate(bodies)]
    with pytest.raises(UploadLimitError):
        asyncio.run(persist_uploads(files, tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_upload_reads_in_bounded_chunks_and_skips_empty_file(tmp_path):
    class BoundedUpload(UploadFile):
        async def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return await super().read(size)

    body = b"x" * (1024 * 1024 + 7)
    result = asyncio.run(
        persist_uploads(
            [
                BoundedUpload(filename="data.csv", file=BytesIO(body)),
                upload("empty.csv", b""),
            ],
            tmp_path,
        )
    )
    assert result == ["data.csv"]
    assert (tmp_path / "data.csv").read_bytes() == body
    assert [path.name for path in tmp_path.iterdir()] == ["data.csv"]
