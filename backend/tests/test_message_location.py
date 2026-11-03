"""安装目录与用户档案分离，真实 SQLite/WAL 和旧 JSON 保留验证。"""

import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from app.services.message_archive import MessageArchive
from app.services.task_bus import TaskBus


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "message_migration_launcher",
        Path(__file__).parents[2] / "tools/remit_prod_app.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BACKEND_DIR", tmp_path / "安装目录" / "backend")
    monkeypatch.setattr(module, "DATA_DIR", tmp_path / "用户 data")
    module.DATA_DIR.mkdir()
    return module


def test_v1_upgrade_preserves_committed_wal_legacy_json_and_event_cursors(
    launcher, monkeypatch
):
    source = launcher.BACKEND_DIR / "logs/messages"
    source.mkdir(parents=True)
    (launcher.DATA_DIR / "migration-v1.json").write_text("{}")
    (source / "legacy.json").write_text(
        json.dumps([{"type": "system", "content": "历史消息"}]), encoding="utf-8"
    )
    archive = MessageArchive(source)
    first = archive.append("fixture", {"type": "system", "content": "第一条"})
    with closing(sqlite3.connect(archive.path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        second = archive.append(
            "fixture", {"type": "system", "content": "WAL 内的新消息"}
        )
        assert Path(str(archive.path) + "-wal").stat().st_size > 0
        launcher.prepare_user_data()
    destination = launcher.DATA_DIR / "logs/messages"
    monkeypatch.setenv("REMIT_MESSAGES_DIR", str(destination))
    manager = TaskBus()
    assert manager.messages_dir == destination
    messages = manager.archive.load("fixture")
    assert [row["sequence"] for row in messages] == [first, second]
    assert messages[-1]["content"] == "WAL 内的新消息"
    assert manager.archive.load("legacy")[0]["content"] == "历史消息"
    manager.archive.append("fixture", {"type": "system", "content": "迁移后的消息"})
    launcher.prepare_user_data()
    assert len(manager.archive.load("fixture")) == 3
    assert len(archive.load("fixture")) == 2
    assert (source / "legacy.json").exists()
    assert not destination.with_name("messages-migration-pending").exists()


def test_existing_user_archive_is_never_overwritten(launcher):
    source = launcher.BACKEND_DIR / "logs/messages"
    source.mkdir(parents=True)
    (source / "old.json").write_text("[]")
    destination = launcher.DATA_DIR / "logs/messages"
    destination.mkdir(parents=True)
    archive = MessageArchive(destination)
    archive.append("current", {"content": "保留"})
    launcher.prepare_message_archive()
    assert archive.load("current")[0]["content"] == "保留"
    assert (source / "old.json").exists()
    assert not (destination / "old.json").exists()


def test_copy_failure_preserves_source_and_does_not_mark_success(launcher, monkeypatch):
    source = launcher.BACKEND_DIR / "logs/messages"
    source.mkdir(parents=True)
    (source / "old.json").write_text("[]")

    def fail(*args):
        raise OSError("disk full fixture")

    monkeypatch.setattr(launcher.shutil, "copy2", fail)
    with pytest.raises(OSError, match="disk full"):
        launcher.prepare_message_archive()
    assert (source / "old.json").read_text() == "[]"
    assert not (launcher.DATA_DIR / "migration-messages-v2.json").exists()
    assert not (launcher.DATA_DIR / "logs/messages").exists()


def test_explicit_test_archive_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_MESSAGES_DIR", str(tmp_path / "configured"))
    manager = TaskBus(tmp_path / "isolated")
    assert manager.messages_dir == tmp_path / "isolated"
    assert not (tmp_path / "configured").exists()


def test_launcher_passes_user_archive_location_to_backend(launcher, monkeypatch):
    from unittest.mock import Mock

    runtime = launcher.DATA_DIR / "python.exe"
    runtime.touch()
    monkeypatch.setattr(launcher, "RUNTIME_PYTHON", runtime)
    monkeypatch.setattr(launcher, "port_is_open", lambda _: False)
    monkeypatch.setattr(launcher, "_wait_port", lambda *args, **kwargs: None)
    start = Mock()
    monkeypatch.setattr(launcher, "_start_hidden", start)
    launcher.ensure_backend_running()
    assert start.call_args.kwargs["env"]["REMIT_MESSAGES_DIR"] == str(
        launcher.DATA_DIR / "logs/messages"
    )
    assert start.call_args.args[1] == launcher.DATA_DIR
