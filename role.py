import json
import re
from typing import Dict, Any, List, Optional

async def execute(data: dict, ctx) -> dict:
    # Extract required inputs from user request
    text = data.get("text", "")
    
    # Parse repo, runId, PR number, jobId from input text
    repo_match = re.search(r"仓库\s+([\w\-]+)", text)
    runid_match = re.search(r"CI\s+runId\s+(\d+)", text)
    pr_match = re.search(r"PR\s+#(\d+)", text)
    jobid_match = re.search(r"任务\s+(DF-\d+)", text)
    
    repo = repo_match.group(1) if repo_match else None
    runId = runid_match.group(1) if runid_match else None
    number = pr_match.group(1) if pr_match else None
    jobId = jobid_match.group(1) if jobid_match else None
    
    # Block if any required field missing
    if not all([repo, runId, number, jobId]):
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": "Missing required input: repo, runId, PR number, or jobId."
        }
    
    # Call tools — must use exact kwarg names per契约
    try:
        log_result = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
    except Exception:
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": f"ci.log.fetch failed for repo={repo}, runId={runId}."
        }
    
    try:
        diff_result = await ctx.tools.call("git.pr.diff", repo=repo, number=number)
    except Exception:
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": f"git.pr.diff failed for repo={repo}, number={number}."
        }
    
    try:
        job_result = await ctx.tools.call("devflow.job.read", jobId=jobId)
    except Exception:
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": f"devflow.job.read failed for jobId={jobId}."
        }
    
    # Validate tool returns per契约
    if not isinstance(log_result, dict) or ("log" not in log_result and not log_result.get("logsUnavailable", False)):
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": "ci.log.fetch returned invalid structure: missing 'log' or 'logsUnavailable'."
        }
    
    if not isinstance(diff_result, dict) or "diff" not in diff_result:
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": "git.pr.diff returned invalid structure: missing 'diff'."
        }
    
    if not isinstance(job_result, list):
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "diff_summary": "",
            "block_reason": "devflow.job.read returned non-list value."
        }
    
    # Block if any evidence is missing/unavailable
    if log_result.get("logsUnavailable", False):
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "diff_summary": "",
            "block_reason": "CI logs explicitly unavailable."
        }
    
    if not log_result.get("log", "").strip():
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch"],
            "diff_summary": "",
            "block_reason": "CI log content empty."
        }
    
    if not diff_result.get("diff", "").strip():
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["git.pr.diff"],
            "diff_summary": "",
            "block_reason": "PR diff content empty."
        }
    
    if len(job_result) == 0:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["devflow.job.read"],
            "diff_summary": "",
            "block_reason": "No matching devflow job found for jobId=" + jobId + "."
        }
    
    # Assemble evidence dict for prompt
    evidence = {
        "ci_log": log_result["log"][:2000] + "..." if len(log_result["log"]) > 2000 else log_result["log"],
        "pr_diff": diff_result["diff"][:2000] + "..." if len(diff_result["diff"]) > 2000 else diff_result["diff"],
        "job_records": job_result[:3]  # limit to avoid token explosion
    }
    
    # Load & compile prompt
    try:
        prompt = await ctx.prompt("role")
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence, ensure_ascii=False))
    except Exception as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["prompt.compilation"],
            "diff_summary": "",
            "block_reason": f"Prompt compilation failed: {str(e)}"
        }
    
    # LLM call — MUST pass prompt=prompt per static check
    try:
        llm_output = await ctx.llm.chat([
            {"role": "user", "content": compiled}
        ], prompt=prompt)
    except Exception:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["llm.chat"],
            "diff_summary": "",
            "block_reason": "LLM inference failed."
        }
    
    # Parse decision from LLM output
    decision_match = re.search(r'"decision":\s*"(PASS|BLOCKED)"', llm_output)
    if not decision_match:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["llm.output.parse"],
            "diff_summary": "",
            "block_reason": "LLM output missing valid decision field."
        }
    
    decision = decision_match.group(1)
    
    # Extract diff_summary and block_reason if present
    diff_summary = ""
    block_reason = ""
    
    diff_match = re.search(r'"diff_summary":\s*"([^"\\]*(?:\\.[^"\\]*)*)"', llm_output)
    if diff_match:
        diff_summary = diff_match.group(1).replace('\\n', '\n')
    
    reason_match = re.search(r'"block_reason":\s*"([^"\\]*(?:\\.[^"\\]*)*)"', llm_output)
    if reason_match:
        block_reason = reason_match.group(1).replace('\\n', '\n')
    
    # Always preserve BLOCKED even if explanation contains PASS
    if decision == "BLOCKED":
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
            "diff_summary": diff_summary,
            "block_reason": block_reason or "LLM explicitly decided BLOCKED."
        }
    
    # For PASS, still validate minimal evidence presence
    return {
        "decision": "PASS",
        "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
        "diff_summary": diff_summary or "No significant diff summary provided by LLM.",
        "block_reason": ""
    }