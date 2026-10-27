"""A new interpreter must execute the real data host while Qt is forbidden."""

import subprocess
import sys

from open_garden_planner.core.plan_io import read_plan, write_plan


def test_autonomous_operations_and_mcp_builder_without_qt(tmp_path):
    path = tmp_path / "pure.ogp"
    write_plan(path, {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []})
    code = r'''
import asyncio
import importlib.abc
import sys
from pathlib import Path
class NoQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt6', 'PySide6', 'PyQt5', 'PySide2'}:
            raise AssertionError('Qt import forbidden: ' + fullname)
sys.meta_path.insert(0, NoQt())
from open_garden_planner.core.document_session import DocumentSession
from open_garden_planner.agent_api.document_tools import DomainProviders
from open_garden_planner.agent_api.server import build_server
from open_garden_planner.services.document_analysis import assess_document_soil
with DocumentSession(Path(sys.argv[1])) as host:
    mcp = build_server(DomainProviders(host.dispatch), writes_enabled=True, write_token='synthetic')
    capability_tool = mcp._tool_manager.get_tool('get_capabilities')
    capabilities = asyncio.run(capability_tool.fn())
    assert capabilities['mode'] == 'autonomous'
    assert 'set_planting_date' in capabilities['operation_schemas']['document']['operations']
    assert capabilities['retry_policy']['persistent_request_key'] == ['apply_document_operations']
    preview = host.dispatch('prepare', {'operations': [{'operation': 'add_manual_task', 'fields': {'title': 'Pure task'}}]})
    assert not host.snapshot().get('manual_tasks')
    host.dispatch('apply', {'preparation_id': preview['preparation_id'], 'request_key': 'pure-1'})
    assert host.history.undo_depth == 1
    host.dispatch('undo', {})
    assert not host.snapshot().get('manual_tasks')
    host.dispatch('redo', {})
    advice = assess_document_soil(host.snapshot())
    assert advice['recorded_intervention'] is False
assert not any(name.split('.')[0] in {'PyQt6','PySide6'} for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert len(read_plan(path)["manual_tasks"]) == 1


def test_writer_lease_prevents_another_process(tmp_path):
    from open_garden_planner.core.document_session import DocumentSession

    path = tmp_path / "owned.ogp"
    write_plan(path, {"version": "1.4", "canvas": {"width": 500, "height": 500}, "objects": []})
    with DocumentSession(path):
        result = subprocess.run([sys.executable, "-c", '''
import sys
from pathlib import Path
from open_garden_planner.core.document_session import DocumentSession
try:
    DocumentSession(Path(sys.argv[1]))
except ValueError as exc:
    assert 'owned by another process' in str(exc)
else:
    raise AssertionError('second process acquired the project')
''', str(path)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
