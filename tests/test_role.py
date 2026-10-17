import pytest
from unittest.mock import AsyncMock, MagicMock
import json
from typing import Dict, Any

# Import the execute function
from role import execute

@pytest.mark.asyncio
async def test_execute_normal_case_from_text():
    # Mock context
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # Simulate tool returns matching契约
    ctx.tools.call.side_effect = [
        {"log": "build passed; tests=42; coverage=85%"},
        {"diff": "diff --git a/main.py b/main.py\n+ def audit():\n+     return True"},
        [{"id": "DF-0006", "status": "IN_REVIEW", "title": "Add audit logic"}]
    ]
    
    # Mock prompt compilation
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="compiled prompt")
    ctx.prompt.return_value = mock_prompt
    
    # Mock LLM response with PASS and diff_summary
    ctx.llm.chat.return_value = '{"decision": "PASS", "diff_summary": "Adds audit() function"}'
    
    # Input with natural language text — matches H2 PASS cases
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异，给出可追溯结论。严格按工具返回内容判断；这是证据核验，不代表自动合并或发布。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "PASS"
    assert "audit()" in result["diff_summary"]
    assert result["block_reason"] == ""
    assert "ci.log.fetch" in result["evidence_sources"]
    assert "git.pr.diff" in result["evidence_sources"]
    assert "devflow.job.read" in result["evidence_sources"]
    assert "steps" in result
    assert len(result["steps"]) == 3


@pytest.mark.asyncio
async def test_execute_missing_input_in_text_blocks():
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # Input text missing jobId
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "Missing required input" in result["block_reason"]
    assert "jobId" in result["block_reason"]
    assert len(result["evidence_sources"]) == 0


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
    
    # LLM returns BLOCKED — final decision must be BLOCKED regardless of explanation
    ctx.llm.chat.return_value = '{"decision": "BLOCKED", "diff_summary": "Diff looks safe", "block_reason": "CI log missing assertion lines."}'
    
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "CI log missing assertion lines" in result["block_reason"]
    assert result["diff_summary"] == "Diff looks safe"


@pytest.mark.asyncio
async def test_execute_tool_empty_log_blocks():
    ctx = MagicMock()
    ctx.tools.call = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.prompt = AsyncMock()
    
    # ci.log.fetch returns empty log string
    ctx.tools.call.side_effect = [
        {"log": ""},
        {"diff": "some diff"},
        [{"id": "DF-0006"}]
    ]
    
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "CI log content missing or empty" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_llm_returns_pass_with_block_reason_blocks():
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
    
    # LLM returns PASS but includes block_reason — violates contract
    ctx.llm.chat.return_value = '{"decision": "PASS", "diff_summary": "Safe change", "block_reason": "Needs manual sign-off"}'
    
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "Model returned PASS but included non-empty block_reason." in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_llm_invalid_json_blocks():
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
    
    # LLM returns malformed JSON
    ctx.llm.chat.return_value = '{"decision": "PASS", "diff_summary": "Safe change"'  # missing closing brace
    
    data = {
        "text": "请对仓库 qa-ci-evidence-1017 的 CI runId 39009850970、PR #1、任务 DF-0006 执行核对主分支构建与差异。"
    }
    
    result = await execute(data, ctx)
    
    assert result["decision"] == "BLOCKED"
    assert "invalid JSON" in result["block_reason"]