import { config } from './config.js';
import { query } from './db.js';
import { logAudit } from './audit.js';

// ─── Types ───────────────────────────────────────────────────────────

export interface RepoSnapshot {
  openPrs: number;
  openIssues: number;
  failingChecks: number;
  staleBranches: number;
  defaultBranch: string;
  recentCommits: Array<{ sha: string; message: string; author: string; date: string }>;
  prDetails: Array<{ number: number; title: string; author: string; draft: boolean; updatedAt: string }>;
  issueDetails: Array<{ number: number; title: string; labels: string[]; updatedAt: string }>;
}

// ─── GitHub API helpers ──────────────────────────────────────────────

const headers = (): Record<string, string> => ({
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${config.github.token}`,
  'X-GitHub-Api-Version': '2022-11-28',
});

async function ghFetch<T>(path: string): Promise<T> {
  const url = `${config.github.apiBase}${path}`;
  const res = await fetch(url, { headers: headers() });
  if (!res.ok) {
    throw new Error(`GitHub API ${res.status}: ${res.statusText} (${path})`);
  }
  return res.json() as Promise<T>;
}

// ─── Fetch repo snapshot ─────────────────────────────────────────────

export async function fetchRepoSnapshot(owner: string, repo: string): Promise<RepoSnapshot> {
  const [repoInfo, prs, issues, commits, branches] = await Promise.all([
    ghFetch<any>(`/repos/${owner}/${repo}`),
    ghFetch<any[]>(`/repos/${owner}/${repo}/pulls?state=open&per_page=10`),
    ghFetch<any[]>(`/repos/${owner}/${repo}/issues?state=open&per_page=10&filter=all`),
    ghFetch<any[]>(`/repos/${owner}/${repo}/commits?per_page=5`),
    ghFetch<any[]>(`/repos/${owner}/${repo}/branches?per_page=50`),
  ]);

  // Filter issues to exclude PRs (GitHub lists PRs as issues too)
  const realIssues = issues.filter((i: any) => !i.pull_request);

  // Check for stale branches (no commits in 30+ days)
  const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  let staleBranchCount = 0;
  // We only count non-default branches
  for (const branch of branches) {
    if (branch.name === repoInfo.default_branch) continue;
    // Branch API doesn't include last commit date easily; count as rough estimate
    staleBranchCount++;
  }
  // Rough heuristic: branches beyond the first 5 active are likely stale
  staleBranchCount = Math.max(0, branches.length - 5);

  // Check failing checks on default branch
  let failingChecks = 0;
  try {
    const checkRuns = await ghFetch<any>(
      `/repos/${owner}/${repo}/commits/${repoInfo.default_branch}/check-runs?per_page=10`,
    );
    failingChecks = (checkRuns.check_runs || []).filter(
      (c: any) => c.conclusion === 'failure',
    ).length;
  } catch {
    // check-runs may not be available on all repos
  }

  return {
    defaultBranch: repoInfo.default_branch,
    openPrs: prs.length,
    openIssues: realIssues.length,
    failingChecks,
    staleBranches: staleBranchCount,
    recentCommits: commits.slice(0, 5).map((c: any) => ({
      sha: c.sha.substring(0, 7),
      message: c.commit.message.split('\n')[0].substring(0, 80),
      author: c.commit.author.name,
      date: c.commit.author.date,
    })),
    prDetails: prs.map((pr: any) => ({
      number: pr.number,
      title: pr.title.substring(0, 80),
      author: pr.user.login,
      draft: pr.draft,
      updatedAt: pr.updated_at,
    })),
    issueDetails: realIssues.slice(0, 10).map((i: any) => ({
      number: i.number,
      title: i.title.substring(0, 80),
      labels: (i.labels || []).map((l: any) => l.name),
      updatedAt: i.updated_at,
    })),
  };
}

// ─── Sync to DB ──────────────────────────────────────────────────────

export async function syncProjectRepo(projectId: string, githubRepo: string): Promise<void> {
  const [owner, repo] = githubRepo.split('/');
  if (!owner || !repo) {
    throw new Error(`Invalid github_repo format: ${githubRepo} (expected owner/repo)`);
  }

  const start = Date.now();
  const snapshot = await fetchRepoSnapshot(owner, repo);

  await query(
    `INSERT INTO repo_status (project_id, current_branch, open_prs, open_issues, failing_checks, stale_branches, recent_commits, pr_details, issue_details)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)`,
    [
      projectId,
      snapshot.defaultBranch,
      snapshot.openPrs,
      snapshot.openIssues,
      snapshot.failingChecks,
      snapshot.staleBranches,
      JSON.stringify(snapshot.recentCommits),
      JSON.stringify(snapshot.prDetails),
      JSON.stringify(snapshot.issueDetails),
    ],
  );

  await logAudit({
    agentName: 'github-sync',
    actionType: 'repo_sync',
    riskClass: 'R0',
    inputSummary: githubRepo,
    outputSummary: `PRs:${snapshot.openPrs} Issues:${snapshot.openIssues} Checks:${snapshot.failingChecks}`,
    durationMs: Date.now() - start,
  });
}

export async function syncAllRepos(): Promise<number> {
  const result = await query<any>(
    `SELECT id, github_repo FROM projects WHERE is_active = true AND github_repo IS NOT NULL`,
  );

  let synced = 0;
  for (const row of result.rows) {
    try {
      await syncProjectRepo(row.id, row.github_repo);
      synced++;
    } catch (err) {
      console.error(`[GitHub] Failed to sync ${row.github_repo}:`, (err as Error).message);
    }
  }
  return synced;
}

export async function getLatestRepoStatus(projectId: string): Promise<RepoSnapshot | null> {
  const result = await query<any>(
    `SELECT current_branch, open_prs, open_issues, failing_checks, stale_branches, recent_commits, pr_details, issue_details
     FROM repo_status
     WHERE project_id = $1
     ORDER BY last_synced DESC
     LIMIT 1`,
    [projectId],
  );
  if (result.rows.length === 0) return null;
  const r = result.rows[0];
  return {
    defaultBranch: r.current_branch,
    openPrs: r.open_prs,
    openIssues: r.open_issues,
    failingChecks: r.failing_checks,
    staleBranches: r.stale_branches,
    recentCommits: r.recent_commits || [],
    prDetails: r.pr_details || [],
    issueDetails: r.issue_details || [],
  };
}

// ─── Format for WhatsApp ─────────────────────────────────────────────

export function formatRepoStatus(projectName: string, snapshot: RepoSnapshot): string {
  let msg = `*GitHub: ${projectName}*\n`;
  msg += `Branch: ${snapshot.defaultBranch}\n`;
  msg += `PRs: ${snapshot.openPrs} open | Issues: ${snapshot.openIssues} open\n`;
  if (snapshot.failingChecks > 0) {
    msg += `Failing checks: ${snapshot.failingChecks}\n`;
  }

  if (snapshot.prDetails.length > 0) {
    msg += `\n*Open PRs:*\n`;
    for (const pr of snapshot.prDetails.slice(0, 5)) {
      msg += `  #${pr.number} ${pr.title}${pr.draft ? ' (draft)' : ''}\n`;
    }
  }

  if (snapshot.recentCommits.length > 0) {
    msg += `\n*Recent commits:*\n`;
    for (const c of snapshot.recentCommits.slice(0, 3)) {
      msg += `  ${c.sha} ${c.message}\n`;
    }
  }

  return msg;
}

export function formatAllReposSummary(
  repos: Array<{ projectName: string; snapshot: RepoSnapshot }>,
): string {
  if (repos.length === 0) return 'No GitHub repos linked to any project.';

  let msg = '*GitHub Summary*\n\n';
  for (const { projectName, snapshot } of repos) {
    msg += `*${projectName}* (${snapshot.defaultBranch})\n`;
    msg += `  PRs: ${snapshot.openPrs} | Issues: ${snapshot.openIssues}`;
    if (snapshot.failingChecks > 0) msg += ` | Failing: ${snapshot.failingChecks}`;
    msg += '\n';
  }
  return msg;
}
