import json
import re
from typing import Dict, Any, List, Optional

async def execute(data: dict, ctx) -> dict:
    # Extract required inputs — per H1: MUST be explicitly provided by caller, not scraped
    # Caller guarantees structured presence; if absent, BLOCKED
    repo = data.get("repo")
    runId = data.get("runId")
    number = data.get("number")
    jobId = data.get("jobId")
    
    # Block if any required field missing
    if not all([repo, runId, number, jobId]):
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "Missing required input: repo, runId, PR number, or jobId."
        }
    
    # Call tools — must use exact kwarg names per契约
    try:
        log_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"ci.log.fetch failed for repo={repo}, runId={runId}: {str(e)}"
        }
    
    try:
        diff_result = await ctx.tools.call("git.pr.diff", repo=repo, number=number)
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["git.pr.diff"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"git.pr.diff failed for repo={repo}, number={number}: {str(e)}"
        }
    
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["devflow.job.read"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"devflow.job.read failed for jobId={jobId}: {str(e)}"
        }
    
    # Validate tool returns per契约
    if not isinstance(log_result, dict):
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "ci.log.fetch returned non-dict value."
        }
    
    if not isinstance(diff_result, dict):
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["git.pr.diff"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "git.pr.diff returned non-dict value."
        }
    
    if not isinstance(job_result, list):
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["devflow.job.read"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "devflow.job.read returned non-list value."
        }
    
    # Block if evidence explicitly unavailable or empty per契约
    if log_result.get("logsUnavailable", False):
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "CI logs explicitly unavailable."
        }
    
    if not isinstance(log_result.get("log"), str) or not log_result.get("log", "").strip():
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "CI log content missing or empty."
        }
    
    if not isinstance(diff_result.get("diff"), str) or not diff_result.get("diff", "").strip():
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["git.pr.diff"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "PR diff content missing or empty."
        }
    
    if len(job_result) == 0:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["devflow.job.read"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"No devflow job found for jobId={jobId}."
        }
    
    # Assemble evidence dict for prompt (truncated to avoid overflow)
    evidence = {
        "ci_log": log_result["log"][:2000] + "..." if len(log_result["log"]) > 2000 else log_result["log"],
        "pr_diff": diff_result["diff"][:2000] + "..." if len(diff_result["diff"]) > 2000 else diff_result["diff"],
        "job_records": job_result[:3]
    }
    
    # Load & compile prompt
    try:
        prompt = await ctx.prompt("role")
        compiled = prompt.compile(input=json.dumps(data, ensure_ascii=False), evidence=json.dumps(evidence, ensure_ascii=False))
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["prompt.compilation"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"Prompt compilation failed: {str(e)}"
        }
    
    # LLM call
    try:
        llm_output = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["llm.chat"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"LLM inference failed: {str(e)}"
        }
    
    # Parse decision from LLM output — strict JSON-like field extraction
    decision_match = re.search(r'"decision"\s*:\s*"(PASS|BLOCKED)"', llm_output)
    if not decision_match:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["llm.output.parse"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "LLM output missing valid decision field."
        }
    
    decision = decision_match.group(1)
    
    # Enforce hard BLOCKED semantics: if model says BLOCKED, it blocks — no override
    if decision == "BLOCKED":
        # Extract block_reason if present
        block_reason = ""
        reason_match = re.search(r'"block_reason"\s*:\s*"((?:[^"\\]|\\.)*)"', llm_output)
        if reason_match:
            block_reason = reason_match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
            "delegate_results": [],
            "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
            "diff_summary": "",
            "block_reason": block_reason or "LLM explicitly decided BLOCKED."
        }
    
    # For PASS: extract diff_summary only (block_reason must be empty)
    diff_summary = ""
    diff_match = re.search(r'"diff_summary"\s*:\s*"((?:[^"\\]|\\.)*)"', llm_output)
    if diff_match:
        diff_summary = diff_match.group(1).replace('\\n', '\n').replace('\\"', '"')
    
    return {
        "decision": "PASS",
        "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
        "delegate_results": [],
        "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
        "diff_summary": diff_summary or "No diff summary provided by LLM.",
        "block_reason": ""
    }