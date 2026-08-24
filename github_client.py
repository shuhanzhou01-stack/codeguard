from __future__ import annotations

import os
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

from security.redaction import sanitize_exception

load_dotenv()

API_ROOT = "https://api.github.com"
REVIEW_MARKER = "<!-- codeguard-review -->"


@dataclass(frozen=True)
class PublishResult:
    status: str
    comment_id: int | None = None
    detail: str | None = None


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        session: requests.Session | None = None,
        api_root: str = API_ROOT,
    ) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.session = session or requests.Session()
        self.api_root = api_root.rstrip("/")

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN is not set")
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_repository(self, owner: str, repo: str) -> dict:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=self._headers(),
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_pull_request_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict]:
        files: list[dict] = []
        page = 1
        while True:
            response = self.session.get(
                f"{self.api_root}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                headers=self._headers(),
                params={"per_page": 100, "page": page},
                timeout=20,
            )
            response.raise_for_status()
            batch = response.json()
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=self._headers("application/vnd.github.v3.diff"),
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    def get_compare(self, owner: str, repo: str, base_sha: str, head_sha: str) -> dict:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_compare_diff(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> str:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
            headers=self._headers("application/vnd.github.v3.diff"),
            timeout=30,
        )
        response.raise_for_status()
        return response.text

    def get_repository_tarball(self, owner: str, repo: str, ref: str) -> bytes:
        response = self.session.get(
            f"{self.api_root}/repos/{owner}/{repo}/tarball/{ref}",
            headers=self._headers(),
            timeout=60,
        )
        response.raise_for_status()
        return response.content

    def publish_review_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> PublishResult:
        try:
            existing = None
            page = 1
            while existing is None:
                comments_response = self.session.get(
                    f"{self.api_root}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                    headers=self._headers(),
                    params={"per_page": 100, "page": page},
                    timeout=20,
                )
                comments_response.raise_for_status()
                comments = comments_response.json()
                existing = next(
                    (
                        comment
                        for comment in comments
                        if REVIEW_MARKER in (comment.get("body") or "")
                    ),
                    None,
                )
                if existing is not None or len(comments) < 100:
                    break
                page += 1
            if existing:
                response = self.session.patch(
                    f"{self.api_root}/repos/{owner}/{repo}/issues/comments/{existing['id']}",
                    headers=self._headers(),
                    json={"body": body},
                    timeout=20,
                )
                status = "updated"
            else:
                response = self.session.post(
                    f"{self.api_root}/repos/{owner}/{repo}/issues/{pr_number}/comments",
                    headers=self._headers(),
                    json={"body": body},
                    timeout=20,
                )
                status = "created"
            response.raise_for_status()
            return PublishResult(status=status, comment_id=response.json().get("id"))
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 403:
                return PublishResult(status="forbidden", detail="GitHub token is read-only")
            return PublishResult(status="failed", detail=sanitize_exception(error, max_length=1_000))
        except requests.RequestException as error:
            return PublishResult(status="failed", detail=sanitize_exception(error, max_length=1_000))


def _client() -> GitHubClient:
    return GitHubClient()


def get_repository(owner: str, repo: str) -> dict:
    return _client().get_repository(owner, repo)


def get_pull_request(owner: str, repo: str, pr_number: int) -> dict:
    return _client().get_pull_request(owner, repo, pr_number)


def get_pull_request_files(owner: str, repo: str, pr_number: int) -> list[dict]:
    return _client().get_pull_request_files(owner, repo, pr_number)


def get_pull_request_diff(owner: str, repo: str, pr_number: int) -> str:
    return _client().get_pull_request_diff(owner, repo, pr_number)


def get_repository_tarball(owner: str, repo: str, ref: str) -> bytes:
    return _client().get_repository_tarball(owner, repo, ref)


def publish_review_comment(
    owner: str, repo: str, pr_number: int, body: str
) -> PublishResult:
    return _client().publish_review_comment(owner, repo, pr_number, body)
