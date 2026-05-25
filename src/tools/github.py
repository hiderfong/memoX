import asyncio
import os
from typing import Any

from github import Auth, Github

from src.agents.base_agent import BaseTool


class GitHubCreateIssueTool(BaseTool):
    """在指定 GitHub 仓库中创建 Issue 的工具。"""

    @property
    def name(self) -> str:
        return "github_create_issue"

    @property
    def description(self) -> str:
        return "使用 GitHub API 在指定的仓库中创建新的 Issue (需要环境变量 GITHUB_TOKEN)。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub 仓库名称，例如 'owner/repo'"
                },
                "title": {
                    "type": "string",
                    "description": "Issue 标题"
                },
                "body": {
                    "type": "string",
                    "description": "Issue 正文内容"
                }
            },
            "required": ["repo", "title"]
        }

    async def execute(self, arguments: dict) -> Any:
        repo_name = arguments["repo"]
        title = arguments["title"]
        body = arguments.get("body", "")

        token = os.getenv("GITHUB_TOKEN")
        if not token:
            return "Error: GITHUB_TOKEN environment variable is not set."

        def _execute():
            g = Github(auth=Auth.Token(token))
            repo_obj = g.get_repo(repo_name)
            issue = repo_obj.create_issue(title=title, body=body)
            return f"Successfully created issue #{issue.number}: {issue.html_url}"

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _execute)
        except Exception as e:
            return f"GitHub API failed: {e}"


class GitHubSearchTool(BaseTool):
    """搜索 GitHub 资源的工具。"""

    @property
    def name(self) -> str:
        return "github_search"

    @property
    def description(self) -> str:
        return "使用 GitHub API 搜索仓库、Issue 或用户 (需要环境变量 GITHUB_TOKEN)。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询关键字"
                },
                "type": {
                    "type": "string",
                    "description": "搜索类型: repositories, issues, users",
                    "default": "repositories"
                },
                "limit": {
                    "type": "integer",
                    "description": "最大返回结果数",
                    "default": 5
                }
            },
            "required": ["query"]
        }

    async def execute(self, arguments: dict) -> Any:
        query = arguments["query"]
        search_type = arguments.get("type", "repositories")
        limit = arguments.get("limit", 5)

        token = os.getenv("GITHUB_TOKEN")
        if not token:
            return "Error: GITHUB_TOKEN environment variable is not set."

        def _execute():
            g = Github(auth=Auth.Token(token))
            results = []
            if search_type == "repositories":
                for repo in g.search_repositories(query)[:limit]:
                    results.append(f"- {repo.full_name}: {repo.description} ({repo.stargazers_count} stars) -> {repo.html_url}")
            elif search_type == "issues":
                for issue in g.search_issues(query)[:limit]:
                    results.append(f"- [{issue.state}] {issue.title} ({issue.repository.full_name}#{issue.number}) -> {issue.html_url}")
            else:
                for user in g.search_users(query)[:limit]:
                    results.append(f"- {user.login}: {user.name} -> {user.html_url}")

            if not results:
                return f"No {search_type} found for query '{query}'."
            return "\n".join(results)

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _execute)
        except Exception as e:
            return f"GitHub API failed: {e}"
