# ADR-0007: Commit hygiene

- Status: accepted (Phase 0)

## Decision
No Co-Authored-By trailer and no AI attribution in commits/PRs. .claude/settings.json sets attribution.commit and attribution.pr to empty and includeCoAuthoredBy=false. Work directly on 'master'.

## Why
Mandatory repo rule (PROMPT 13.1). The repo had no commits when Phase 0 started; branch was 'master', so we stay on it.

## Consequences
Verify with 'git log -1 --format=%B' after each commit.
