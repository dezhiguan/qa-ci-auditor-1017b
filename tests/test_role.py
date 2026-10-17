import pytest
from unittest.mock import AsyncMock, MagicMock
import json
from typing import Dict, Any

# Import the execute function
from role import execute

@pytest.mark.asyncio
async def test_execute_normal_case():
    # Mock context
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # Simulate tool returns
    ctx.tools.call.side_effect = [
        {"log": "build passed; tests=42; coverage=85%"},
        {"diff": "diff --git a/main.py b/main.py\n+ def audit():\n+     return True"},
        [{"id": "DF-0006", "status": "IN_REVIEW", "title": "Add audit logic"}]
    ]
    
    # Mock prompt compilation
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="compiled prompt")
    ctx.prompt.return_value = mock_prompt
    
    # Mock LLM response with PASS
    ctx.llm.chat.return_value = '{"decision": "PASS", "diff_summary": "Adds audit() function", "block_reason": ""}'
    
    # Input with all required fields
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "PASS"
    assert "audit()" in result["diff_summary"]
    assert result["block_reason"] == ""
    assert "ci.log.fetch" in result["evidence_sources"]
    assert "git.pr.diff" in result["evidence_sources"]
    assert "devflow.job.read" in result["evidence_sources"]


@pytest.mark.asyncio
async def test_execute_missing_input_blocks():
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # Input missing jobId
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "Missing required input" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_llm_returns_blocked_despite_pass_in_text():
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # Simulate full evidence present
    ctx.tools.call.side_effect = [
        {"log": "build passed"},
        {"diff": "+ audit()"},
        [{"id": "DF-0006"}]
    ]
    
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="compiled prompt")
    ctx.prompt.return_value = mock_prompt
    
    # LLM returns BLOCKED but includes 'PASS' in explanation
    ctx.llm.chat.return_value = '{"decision": "BLOCKED", "diff_summary": "Diff looks safe", "block_reason": "CI log missing assertion lines — PASS is insufficient."}'
    
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "CI log missing assertion lines" in result["block_reason"]
    # Must NOT be overruled by 'PASS' in explanation