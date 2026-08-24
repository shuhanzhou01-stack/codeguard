class PullRequest:
    def __init__(
        self,
        repo_name: str,
        pr_number: int,
        title: str,
        author: str
    ):
        self.repo_name = repo_name
        self.pr_number = pr_number
        self.title = title
        self.author = author
        self.status = "pending"

    def start_analysis(self) -> None:
        self.status = "running"

    def finish_analysis(self) -> None:
        self.status = "completed"

    def get_summary(self) -> str:
        return (
            f"PR #{self.pr_number}: {self.title}\n"
            f"Repository: {self.repo_name}\n"
            f"Author: {self.author}\n"
            f"Status: {self.status}"
        )


class TestResult:
    def __init__(
        self,
        test_name: str,
        passed: bool,
        execution_time: float,
        error_message: str = ""
    ):
        self.test_name = test_name
        self.passed = passed
        self.execution_time = execution_time
        self.error_message = error_message

    def get_summary(self) -> str:
        if self.passed:
            status = "PASSED"
        else:
            status = "FAILED"

        if self.error_message:
            error = self.error_message
        else:
            error = "None"

        return (
            f"Test: {self.test_name}\n"
            f"Status: {status}\n"
            f"Execution time: {self.execution_time}s\n"
            f"Error: {error}"
        )