from pathlib import Path
from typing import List


def create_project_structure() -> None:
    """Create the src/email_summarizer package scaffold for the agent project."""
    root: Path = Path(__file__).resolve().parent

    directories: List[Path] = [
        root / "src",
        root / "src" / "email_summarizer",
        root / "src" / "email_summarizer" / "agent",
        root / "src" / "email_summarizer" / "utils",
        root / "src" / "email_summarizer" / "config",
        root / "src" / "email_summarizer" / "prompts",
        root / "src" / "email_summarizer" / "tools",
        root / "src" / "email_summarizer" / "models",
        root / "src" / "email_summarizer" / "memory",
        root / "src" / "email_summarizer" / "workflows",
        root / "src" / "email_summarizer" / "state",
        root / "src" / "email_summarizer" / "evals",
        root / "src" / "email_summarizer" / "tests",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Created: {directory.relative_to(root)}")


if __name__ == "__main__":
    create_project_structure()
