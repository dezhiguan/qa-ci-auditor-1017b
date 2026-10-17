import json
import re
from typing import Dict, Any, List, Optional

async def execute(data: dict, ctx) -> dict:
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

    # Extract repo: match '仓库 <slug>', 'repo=<slug>', 'repository <slug>'
    repo_match = re.search(r'(?:仓库|repo(?:sitory)?)[\s：:\-]*([\w\-]+)', text)
    repo = repo_match.group(1).strip() if repo_match else None

    # Extract runId: digits only, after 'CI runId', 'runId', 'CI ID', 'CI #', 'run #'
    runid_match = re.search(r'(?:CI\s+runId|runId|CI\s+ID|CI\s+#|run\s+#)\s*(\d+)', text)
    runId = runid_match.group(1).strip() if runid_match else None

    # Extract PR number: '#\d+' or 'PR #\d+' or 'PR \d+' or 'pull request \d+' — cast to int later
    pr_match = re.search(r'(?:PR\s*#?|pull\s+request)\s*(\d+)', text)
    pr_number_str = pr_match.group(1).strip() if pr_match else None
    number = int(pr_number_str) if pr_number_str and pr_number_str.isdigit() else None

    # Extract jobId: 'DF-\d+' pattern, case-insensitive, with optional prefix
    job_match = re.search(r'(?:jobId|task|job|研发任务号|任务号)[\s：:\-]*([A-Z]{2}-\d+)', text, re.IGNORECASE)
    jobId = job_match.group(1).strip() if job_match else None

    # Fallback: bare DF-\d+ *only if no other jobId found and exactly one match*
    if not jobId:
        all_df_matches = re.findall(r'DF-\d+', text, re.IGNORECASE)
        if len(all_df_matches) == 1:
            jobId = all_df_matches[0].strip()

    # Block on any missing required identifier
    missing = []
    if not repo:
        missing.append("repo")
    if not runId:
        missing.append("runId")
    if number is None:
        missing.append("PR number")
    if not jobId:
        missing.append("jobId")
    
    if missing:
        return {
            "decision": "BLOCKED",
            "evidence_sources": [],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"Missing required input: {', '.join(missing)}. All must be explicitly stated in input text."
        }

    # Call tools with exact kwarg names per契约
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

    # Enforce evidence presence per契约
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

    # Assemble evidence for prompt (truncated)
    evidence = {
        "ci_log": log_result["log"][:2000] + "..." if len(log_result["log"]) > 2000 else log_result["log"],
        "pr_diff": diff_result["diff"][:2000] + "..." if len(diff_result["diff"]) > 2000 else diff_result["diff"],
        "job_records": job_result[:3]
    }

    # Load & compile prompt
    try:
        prompt = await ctx.prompt("role")
        compiled = prompt.compile(input=text, evidence=json.dumps(evidence, ensure_ascii=False))
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

    # Strict JSON parse + decision validation
    try:
        parsed = json.loads(llm_output.strip())
        if not isinstance(parsed, dict):
            raise ValueError("LLM output not a JSON object")
        decision = parsed.get("decision")
        if decision not in ["PASS", "BLOCKED"]:
            raise ValueError(f"Invalid decision value: {decision}")
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["llm.output.parse"],
            "delegate_results": [],
            "steps": [],
            "diff_summary": "",
            "block_reason": f"LLM output invalid JSON or missing/invalid decision field: {str(e)}"
        }

    # Hard BLOCKED semantics: if model says BLOCKED → final BLOCKED
    if decision == "BLOCKED":
        block_reason = parsed.get("block_reason", "").strip()
        diff_summary = parsed.get("diff_summary", "").strip()
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
            "delegate_results": [],
            "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
            "diff_summary": diff_summary,
            "block_reason": block_reason or "LLM explicitly decided BLOCKED."
        }

    # For PASS: block_reason must be absent/empty
    diff_summary = parsed.get("diff_summary", "").strip()
    block_reason = parsed.get("block_reason", "").strip()
    if block_reason:
        return {
            "decision": "BLOCKED",
            "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
            "delegate_results": [],
            "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
            "diff_summary": diff_summary,
            "block_reason": "Model returned PASS but included non-empty block_reason."
        }

    return {
        "decision": "PASS",
        "evidence_sources": ["ci.log.fetch", "git.pr.diff", "devflow.job.read"],
        "delegate_results": [],
        "steps": ["validated_ci_log", "validated_pr_diff", "validated_job_record"],
        "diff_summary": diff_summary or "No diff summary provided by LLM.",
        "block_reason": ""
    }