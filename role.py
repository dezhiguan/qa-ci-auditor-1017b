import json
import re
from typing import Dict, Any, List, Optional

async def execute(data: dict, ctx) -> dict:
    # H2 PASS cases show input is {"text": "natural language..."}
    # Extract repo, runId, number (PR #N), jobId from data.text using explicit regex patterns
    text = data.get("text", "")
    if not isinstance(text, str):
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": "Input 'text' field missing or not a string."
        }

    # Extract repo: matches '仓库 <slug>' or 'repo=<slug>' or 'repository <slug>'
    repo_match = re.search(r'(?:仓库|repo(?:sitory)?)[\s：:\-]*([\w\-]+)', text)
    repo = repo_match.group(1).strip() if repo_match else None

    # Extract runId: digits only, after 'CI runId', 'runId', 'CI ID', or '#'
    runid_match = re.search(r'(?:CI\s+runId|runId|CI\s+ID|CI\s+#|run\s+#)\s*(\d+)', text)
    runId = runid_match.group(1).strip() if runid_match else None

    # Extract PR number: '#\d+' or 'PR #\d+' or 'PR \d+'
    pr_match = re.search(r'(?:PR\s*#?|pull\s+request\s*#?)\s*(\d+)', text)
    number = pr_match.group(1).strip() if pr_match else None

    # Extract jobId: 'DF-\d+' or 'task DF-\d+' or 'jobId=DF-\d+'
    job_match = re.search(r'(?:jobId|task|job|研发任务号)[\s：:\-]*([A-Z]{2}-\d+)', text)
    jobId = job_match.group(1).strip() if job_match else None

    # Fallback: try bare DF-\d+ anywhere if no jobId yet
    if not jobId:
        fallback_job = re.search(r'(DF-\d+)', text)
        jobId = fallback_job.group(1).strip() if fallback_job else None

    # Block if any required identifier missing
    if not all([repo, runId, number, jobId]):
        missing = []
        if not repo: missing.append("repo")
        if not runId: missing.append("runId")
        if not number: missing.append("PR number")
        if not jobId: missing.append("jobId")
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"Missing required input: {', '.join(missing)}. All must be explicitly stated in input text."
        }

    # Call tools — exact kwarg names per契约
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
        # Extract block_reason and diff_summary *if present*
        block_reason = ""
        reason_match = re.search(r'"block_reason"\s*:\s*"((?:[^"\\]|\\.)*)"', llm_output)
        if reason_match:
            block_reason = reason_match.group(1).replace('\\n', '\n').replace('\\"', '"')

        diff_summary = ""
        diff_match = re.search(r'"diff_summary"\s*:\s*"((?:[^"\\]|\\.)*)"', llm_output)
        if diff_match:
            diff_summary = diff_match.group(1).replace('\\n', '\n').replace('\\"', '"')

        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
            "delegate_results": [],
            "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
            "diff_summary": diff_summary,
            "block_reason": block_reason or "LLM explicitly decided BLOCKED."
        }

    # For PASS: extract diff_summary, ensure block_reason is empty
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