import os
from unittest.mock import patch, MagicMock

import pytest

from src.tools.github import GitHubCreateIssueTool, GitHubSearchTool


@pytest.fixture
def create_issue_tool():
    return GitHubCreateIssueTool()


@pytest.fixture
def search_tool():
    return GitHubSearchTool()


def test_github_tools_properties(create_issue_tool, search_tool):
    assert create_issue_tool.name == "github_create_issue"
    assert "repo" in create_issue_tool.input_schema["properties"]
    assert "创建" in create_issue_tool.description
    
    assert search_tool.name == "github_search"
    assert "query" in search_tool.input_schema["properties"]
    assert "搜索" in search_tool.description


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_create_issue_success(mock_github, create_issue_tool):
    mock_g = MagicMock()
    mock_github.return_value = mock_g
    mock_repo = MagicMock()
    mock_g.get_repo.return_value = mock_repo
    
    mock_issue = MagicMock()
    mock_issue.number = 42
    mock_issue.html_url = "https://github.com/owner/repo/issues/42"
    mock_repo.create_issue.return_value = mock_issue
    
    result = await create_issue_tool.execute({
        "repo": "owner/repo",
        "title": "Test Issue",
        "body": "Issue body"
    })
    
    mock_repo.create_issue.assert_called_once_with(title="Test Issue", body="Issue body")
    assert "Successfully created issue #42" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {}, clear=True)
async def test_create_issue_no_token(create_issue_tool):
    result = await create_issue_tool.execute({
        "repo": "owner/repo",
        "title": "Test Issue"
    })
    assert "GITHUB_TOKEN environment variable is not set" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_search_repositories(mock_github, search_tool):
    mock_g = MagicMock()
    mock_github.return_value = mock_g
    
    mock_repo = MagicMock()
    mock_repo.full_name = "owner/repo"
    mock_repo.description = "A test repo"
    mock_repo.stargazers_count = 100
    mock_repo.html_url = "https://github.com/owner/repo"
    
    mock_g.search_repositories.return_value = [mock_repo]
    
    result = await search_tool.execute({
        "query": "test",
        "type": "repositories",
        "limit": 1
    })
    
    mock_g.search_repositories.assert_called_once_with("test")
    assert "owner/repo: A test repo (100 stars)" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_search_issues(mock_github, search_tool):
    mock_g = MagicMock()
    mock_github.return_value = mock_g
    
    mock_issue = MagicMock()
    mock_issue.state = "open"
    mock_issue.title = "Test issue"
    mock_issue.repository.full_name = "owner/repo"
    mock_issue.number = 1
    mock_issue.html_url = "https://github.com/owner/repo/issues/1"
    
    mock_g.search_issues.return_value = [mock_issue]
    
    result = await search_tool.execute({
        "query": "bug",
        "type": "issues"
    })
    
    mock_g.search_issues.assert_called_once_with("bug")
    assert "[open] Test issue (owner/repo#1)" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_search_users(mock_github, search_tool):
    mock_g = MagicMock()
    mock_github.return_value = mock_g
    
    mock_user = MagicMock()
    mock_user.login = "testuser"
    mock_user.name = "Test User"
    mock_user.html_url = "https://github.com/testuser"
    
    mock_g.search_users.return_value = [mock_user]
    
    result = await search_tool.execute({
        "query": "testuser",
        "type": "users"
    })
    
    mock_g.search_users.assert_called_once_with("testuser")
    assert "testuser: Test User -> https://github.com/testuser" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_search_no_results(mock_github, search_tool):
    mock_g = MagicMock()
    mock_github.return_value = mock_g
    mock_g.search_repositories.return_value = []
    
    result = await search_tool.execute({
        "query": "nonexistent"
    })
    
    assert "No repositories found for query 'nonexistent'" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {}, clear=True)
async def test_search_no_token(search_tool):
    result = await search_tool.execute({
        "query": "test"
    })
    assert "GITHUB_TOKEN environment variable is not set" in result


@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_search_github_api_error(mock_github, search_tool):
    mock_github.side_effect = Exception("API rate limit exceeded")
    
    result = await search_tool.execute({
        "query": "test"
    })
    
@pytest.mark.asyncio
@patch.dict(os.environ, {"GITHUB_TOKEN": "fake_token"})
@patch("src.tools.github.Github")
async def test_create_issue_error(mock_github, create_issue_tool):
    mock_github.side_effect = Exception("Create failed")
    
    result = await create_issue_tool.execute({
        "repo": "owner/repo",
        "title": "Test"
    })
    
    assert "GitHub API failed: Create failed" in result