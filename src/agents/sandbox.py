"""沙箱目录管理 - 为每个任务和 Agent 创建隔离工作区"""

import shutil
from pathlib import Path


class SandboxManager:
    """为多 Agent 任务创建和管理隔离的文件系统工作区"""

    def __init__(self, base_workspace: str | Path):
        self.base_workspace = Path(base_workspace)

    def create_task_workspace(self, task_id: str) -> Path:
        """创建任务工作区（coordinator/ 和 shared/ 子目录）"""
        workspace = self.base_workspace / task_id
        (workspace / "coordinator").mkdir(parents=True, exist_ok=True)
        (workspace / "shared").mkdir(parents=True, exist_ok=True)
        return workspace

    def get_agent_sandbox(self, task_id: str, agent_name: str) -> Path:
        """获取 Agent 的专属沙箱目录（不存在则自动创建）"""
        sandbox = self.base_workspace / task_id / f"agent_{agent_name}"
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def get_shared_dir(self, task_id: str) -> Path:
        """获取任务的共享输出目录"""
        shared = self.base_workspace / task_id / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        return shared

    def cleanup(self, task_id: str) -> None:
        """删除整个任务工作区"""
        workspace = self.base_workspace / task_id
        if workspace.exists():
            shutil.rmtree(workspace)
