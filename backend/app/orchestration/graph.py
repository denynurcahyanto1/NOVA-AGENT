from typing import Dict, List, Set
from collections import defaultdict, deque
from .models import Task, TaskStatus

class TaskGraph:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.dependencies: Dict[str, List[str]] = defaultdict(list)  # child -> [parents]
        self.dependents: Dict[str, List[str]] = defaultdict(list)   # parent -> [children]

    def add_task(self, task: Task):
        self.tasks[str(task.id)] = task

    def add_dependency(self, parent_id: str, child_id: str):
        self.dependencies[child_id].append(parent_id)
        self.dependents[parent_id].append(child_id)

    def topological_sort(self) -> List[Task]:
        """Return tasks in execution order (parents before children)."""
        in_degree = {tid: len(self.dependencies[tid]) for tid in self.tasks}
        queue = deque([tid for tid, deg in in_degree.items() if deg == 0])
        sorted_ids = []

        while queue:
            tid = queue.popleft()
            sorted_ids.append(tid)
            for child in self.dependents.get(tid, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(sorted_ids) != len(self.tasks):
            raise ValueError("Cyclic dependency detected in TaskGraph")

        return [self.tasks[tid] for tid in sorted_ids]

    def get_ready_tasks(self, completed_ids: Set[str]) -> List[Task]:
        """Return tasks whose all dependencies are in completed_ids."""
        ready = []
        for tid, task in self.tasks.items():
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in self.dependencies.get(tid, [])):
                ready.append(task)
        return ready