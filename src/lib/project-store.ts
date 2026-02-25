import { query } from './db.js';

// ─── Types ───────────────────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  description: string | null;
  githubRepo: string | null;
  localPath: string | null;
  isActive: boolean;
  createdAt: Date;
  updatedAt: Date;
}

export interface TaskNode {
  id: string;
  title: string;
  content: string | null;
  priority: string;
  status: string;
  nextAction: string | null;
  projectId: string | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface ProjectDashboard {
  project: Project;
  taskCounts: { active: number; completed: number; archived: number };
  recentItems: Array<{ id: string; rawText: string; classifiedAs: string; createdAt: Date }>;
  recentNodes: Array<{ id: string; title: string; nodeType: string; createdAt: Date }>;
}

export interface SimilarNode {
  id: string;
  title: string;
  similarity: number;
}

export interface StaleItem {
  id: string;
  title: string;
  nodeType: string;
  updatedAt: Date;
  projectName: string | null;
}

// ─── Project CRUD ────────────────────────────────────────────────────

export async function createProject(
  name: string,
  description?: string,
  githubRepo?: string,
  localPath?: string,
): Promise<string> {
  const result = await query<{ id: string }>(
    `INSERT INTO projects (name, description, github_repo, local_path)
     VALUES ($1, $2, $3, $4)
     RETURNING id`,
    [name, description || null, githubRepo || null, localPath || null],
  );
  return result.rows[0].id;
}

export async function getProjectByName(name: string): Promise<Project | null> {
  const result = await query<any>(
    `SELECT id, name, description, github_repo, local_path, is_active, created_at, updated_at
     FROM projects WHERE LOWER(name) = LOWER($1)`,
    [name],
  );
  if (result.rows.length === 0) return null;
  return mapProject(result.rows[0]);
}

export async function getProjectById(id: string): Promise<Project | null> {
  const result = await query<any>(
    `SELECT id, name, description, github_repo, local_path, is_active, created_at, updated_at
     FROM projects WHERE id = $1`,
    [id],
  );
  if (result.rows.length === 0) return null;
  return mapProject(result.rows[0]);
}

export async function listActiveProjects(): Promise<Project[]> {
  const result = await query<any>(
    `SELECT id, name, description, github_repo, local_path, is_active, created_at, updated_at
     FROM projects WHERE is_active = true ORDER BY name`,
  );
  return result.rows.map(mapProject);
}

export async function archiveProject(name: string): Promise<boolean> {
  const result = await query(
    `UPDATE projects SET is_active = false, updated_at = NOW()
     WHERE LOWER(name) = LOWER($1) AND is_active = true`,
    [name],
  );
  return (result.rowCount ?? 0) > 0;
}

export async function updateProjectRepo(name: string, repo: string): Promise<boolean> {
  const result = await query(
    `UPDATE projects SET github_repo = $1, updated_at = NOW()
     WHERE LOWER(name) = LOWER($2)`,
    [repo, name],
  );
  return (result.rowCount ?? 0) > 0;
}

export async function updateProjectPath(name: string, path: string): Promise<boolean> {
  const result = await query(
    `UPDATE projects SET local_path = $1, updated_at = NOW()
     WHERE LOWER(name) = LOWER($2)`,
    [path, name],
  );
  return (result.rowCount ?? 0) > 0;
}

// ─── Task CRUD ───────────────────────────────────────────────────────

export async function createTask(
  projectId: string,
  title: string,
  priority: string = 'medium',
): Promise<string> {
  const result = await query<{ id: string }>(
    `INSERT INTO brain_nodes (title, node_type, priority, project_id, status)
     VALUES ($1, 'task', $2, $3, 'active')
     RETURNING id`,
    [title, priority, projectId],
  );
  return result.rows[0].id;
}

export async function listProjectTasks(
  projectId: string,
  status?: string,
): Promise<TaskNode[]> {
  const whereStatus = status ? `AND bn.status = $2` : `AND bn.status = 'active'`;
  const params: any[] = [projectId];
  if (status) params.push(status);

  const result = await query<any>(
    `SELECT bn.id, bn.title, bn.content, bn.priority, bn.status, bn.next_action, bn.project_id, bn.created_at, bn.updated_at
     FROM brain_nodes bn
     WHERE bn.node_type = 'task' AND bn.project_id = $1 ${whereStatus}
     ORDER BY
       CASE bn.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
       bn.created_at DESC`,
    params,
  );
  return result.rows.map(mapTask);
}

export async function completeTask(taskId: string): Promise<boolean> {
  const result = await query(
    `UPDATE brain_nodes SET status = 'completed', updated_at = NOW()
     WHERE id = $1 AND node_type = 'task'`,
    [taskId],
  );
  return (result.rowCount ?? 0) > 0;
}

export async function archiveTask(taskId: string): Promise<boolean> {
  const result = await query(
    `UPDATE brain_nodes SET status = 'archived', updated_at = NOW()
     WHERE id = $1 AND node_type = 'task'`,
    [taskId],
  );
  return (result.rowCount ?? 0) > 0;
}

// ─── Assignment ──────────────────────────────────────────────────────

export async function assignItemToProject(inboxId: string, projectId: string): Promise<void> {
  await query(
    `UPDATE inbox_items SET project_id = $1, updated_at = NOW() WHERE id = $2`,
    [projectId, inboxId],
  );
}

export async function assignBrainNodeToProject(nodeId: string, projectId: string): Promise<void> {
  await query(
    `UPDATE brain_nodes SET project_id = $1, updated_at = NOW() WHERE id = $2`,
    [projectId, nodeId],
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────

export async function getProjectDashboard(projectId: string): Promise<ProjectDashboard | null> {
  const project = await getProjectById(projectId);
  if (!project) return null;

  const [taskCountsRes, recentItemsRes, recentNodesRes] = await Promise.all([
    query<any>(
      `SELECT status, count(*) as cnt FROM brain_nodes
       WHERE project_id = $1 AND node_type = 'task'
       GROUP BY status`,
      [projectId],
    ),
    query<any>(
      `SELECT id, raw_text, classified_as, created_at FROM inbox_items
       WHERE project_id = $1 ORDER BY created_at DESC LIMIT 5`,
      [projectId],
    ),
    query<any>(
      `SELECT id, title, node_type, created_at FROM brain_nodes
       WHERE project_id = $1 AND node_type != 'task'
       ORDER BY created_at DESC LIMIT 5`,
      [projectId],
    ),
  ]);

  const taskCounts = { active: 0, completed: 0, archived: 0 };
  for (const row of taskCountsRes.rows) {
    if (row.status === 'active') taskCounts.active = parseInt(row.cnt);
    else if (row.status === 'completed') taskCounts.completed = parseInt(row.cnt);
    else if (row.status === 'archived') taskCounts.archived = parseInt(row.cnt);
  }

  return {
    project,
    taskCounts,
    recentItems: recentItemsRes.rows.map((r: any) => ({
      id: r.id,
      rawText: r.raw_text,
      classifiedAs: r.classified_as,
      createdAt: r.created_at,
    })),
    recentNodes: recentNodesRes.rows.map((r: any) => ({
      id: r.id,
      title: r.title,
      nodeType: r.node_type,
      createdAt: r.created_at,
    })),
  };
}

// ─── Brain Graph: Similar Nodes & Edges ──────────────────────────────

export async function findSimilarNodes(
  embedding: number[],
  limit: number = 5,
  excludeId?: string,
): Promise<SimilarNode[]> {
  const embeddingStr = `[${embedding.join(',')}]`;
  const excludeClause = excludeId ? `AND id != $3` : '';
  const params: any[] = [embeddingStr, limit];
  if (excludeId) params.push(excludeId);

  const result = await query<any>(
    `SELECT id, title, 1 - (embedding <=> $1::vector) as similarity
     FROM brain_nodes
     WHERE embedding IS NOT NULL ${excludeClause}
     ORDER BY embedding <=> $1::vector
     LIMIT $2`,
    params,
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    title: r.title,
    similarity: parseFloat(r.similarity),
  }));
}

export async function createEdge(
  sourceId: string,
  targetId: string,
  relationType: string,
  weight: number = 1.0,
): Promise<string> {
  const result = await query<{ id: string }>(
    `INSERT INTO brain_edges (source_id, target_id, relation_type, weight)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (source_id, target_id, relation_type)
     DO UPDATE SET weight = EXCLUDED.weight, metadata = brain_edges.metadata
     RETURNING id`,
    [sourceId, targetId, relationType, weight],
  );
  return result.rows[0].id;
}

// ─── Stale Items ─────────────────────────────────────────────────────

export async function getStaleItems(daysOld: number): Promise<StaleItem[]> {
  const result = await query<any>(
    `SELECT bn.id, bn.title, bn.node_type, bn.updated_at, p.name as project_name
     FROM brain_nodes bn
     LEFT JOIN projects p ON bn.project_id = p.id
     WHERE bn.status = 'active'
       AND bn.updated_at < NOW() - make_interval(days => $1)
     ORDER BY bn.updated_at ASC
     LIMIT 20`,
    [daysOld],
  );
  return result.rows.map((r: any) => ({
    id: r.id,
    title: r.title,
    nodeType: r.node_type,
    updatedAt: r.updated_at,
    projectName: r.project_name,
  }));
}

// ─── Active Project Names (for classifier) ───────────────────────────

export async function getActiveProjectNames(): Promise<string[]> {
  const result = await query<any>(
    `SELECT name FROM projects WHERE is_active = true ORDER BY name`,
  );
  return result.rows.map((r: any) => r.name);
}

// ─── Mappers ─────────────────────────────────────────────────────────

function mapProject(r: any): Project {
  return {
    id: r.id,
    name: r.name,
    description: r.description,
    githubRepo: r.github_repo,
    localPath: r.local_path,
    isActive: r.is_active,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}

function mapTask(r: any): TaskNode {
  return {
    id: r.id,
    title: r.title,
    content: r.content,
    priority: r.priority,
    status: r.status,
    nextAction: r.next_action,
    projectId: r.project_id,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
}
