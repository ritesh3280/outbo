"""Browser Use Cloud SDK wrapper.

Uses the browser-use-sdk package to run AI-powered browser tasks.
When BROWSER_USE_API_KEY is not set, returns mock data for local development.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Type

from pydantic import BaseModel

from backend.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BrowserTaskResult:
    """Result from a Browser Use Cloud task."""

    task_id: str = ""
    output: str = ""
    parsed_output: Any = None
    status: str = ""
    success: bool = True
    error: str = ""


@dataclass
class BrowserTool:
    """Wrapper around Browser Use Cloud SDK with stub fallback."""

    _is_stub: bool = field(default=False, init=False)
    _client: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._is_stub = not settings.browser_use_api_key
        if not self._is_stub:
            from browser_use_sdk import AsyncBrowserUse

            self._client = AsyncBrowserUse(api_key=settings.browser_use_api_key)

    async def run_task(
        self,
        task: str,
        schema: Type[BaseModel] | None = None,
        start_url: str | None = None,
        max_steps: int = 50,
    ) -> BrowserTaskResult:
        """Run a browser task via Browser Use Cloud.

        Args:
            task: Natural language description of what the browser agent should do.
            schema: Optional Pydantic model for structured output.
            start_url: Optional URL to start the task from.
            max_steps: Maximum number of steps the agent can take.

        Returns:
            BrowserTaskResult with the task output.
        """
        if self._is_stub:
            return self._mock_task(task, schema)

        try:
            create_kwargs: dict[str, Any] = {
                "task": task,
                "max_steps": max_steps,
            }
            if start_url:
                create_kwargs["start_url"] = start_url
            if schema:
                create_kwargs["structured_output"] = json.dumps(
                    schema.model_json_schema()
                )

            logger.info("Starting Browser Use task: %s", task[:100])
            task_obj = await self._client.tasks.create_task(**create_kwargs)

            result = await task_obj.complete()

            output = getattr(result, "output", "") or ""
            parsed = None
            if schema and hasattr(result, "parsed_output") and result.parsed_output:
                parsed = result.parsed_output
            status = getattr(result, "status", "completed")

            logger.info(
                "Browser Use task completed — status: %s, output length: %d",
                status,
                len(output),
            )

            return BrowserTaskResult(
                task_id=getattr(task_obj, "id", ""),
                output=output,
                parsed_output=parsed,
                status=str(status),
                success=True,
            )

        except Exception as e:
            logger.error("Browser Use task failed: %s", e)
            return BrowserTaskResult(
                success=False,
                error=str(e),
                status="failed",
            )

    def _mock_task(
        self, task: str, schema: Type[BaseModel] | None
    ) -> BrowserTaskResult:
        """Return mock data for stub mode."""
        logger.info("Stub browser task: %s", task[:100])

        mock_output = (
            "Mock browser task completed. "
            "Set BROWSER_USE_API_KEY in .env to run real browser tasks. "
            f"Task was: {task[:200]}"
        )

        return BrowserTaskResult(
            task_id="stub-task-001",
            output=mock_output,
            parsed_output=None,
            status="completed",
            success=True,
        )
