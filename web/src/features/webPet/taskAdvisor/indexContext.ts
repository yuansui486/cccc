import type { Task } from "../../../types";
import type { TaskAdvisorContext, TaskAdvisorInput } from "./types";

function flattenTasks(tasks: Task[] | undefined | null): Task[] {
  const result: Task[] = [];
  const queue = Array.isArray(tasks) ? [...tasks] : [];
  while (queue.length > 0) {
    const task = queue.shift();
    if (!task) continue;
    result.push(task);
    if (Array.isArray(task.children) && task.children.length > 0) {
      queue.push(...task.children);
    }
  }
  return result;
}

export function indexTaskAdvisorContext(input: TaskAdvisorInput): TaskAdvisorContext | null {
  const groupId = String(input.groupId || "").trim();
  const groupContext = input.groupContext;
  if (!groupId || !groupContext) return null;

  const agentStates = Array.isArray(groupContext.agent_states) ? groupContext.agent_states : [];
  const tasks = flattenTasks(groupContext.coordination?.tasks);
  const taskEntries: Array<[string, Task]> = [];
  for (const task of tasks) {
    const taskId = String(task.id || "").trim();
    if (!taskId) continue;
    taskEntries.push([taskId, task]);
  }

  return {
    groupId,
    groupContext,
    agentStates,
    tasks,
    taskById: new Map<string, Task>(taskEntries),
  };
}
