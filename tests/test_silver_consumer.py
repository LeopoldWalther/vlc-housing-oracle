"""The queue consumer keeps at most one Silver task running: it adopts a running one or starts one."""

import ast
import unittest
from pathlib import Path

from src.silver.consumer import TaskFailed, TaskStillRunning, handler

CONSUMER = Path(__file__).resolve().parents[1] / "src" / "silver" / "consumer.py"
ENV = {
    "SILVER_CLUSTER": "arn:syn:cluster/vlc-housing-silver",
    "SILVER_TASK_DEFINITION": "arn:syn:task-definition/vlc-housing-silver:7",
    "SILVER_TASK_FAMILY": "vlc-housing-silver",
    "SILVER_SUBNETS": "subnet-a,subnet-b",
    "SILVER_SECURITY_GROUP": "sg-silver",
    "SILVER_ASSIGN_PUBLIC_IP": "ENABLED",
}


class FakeEcs:
    """Answers ListTasks, RunTask and DescribeTasks from a script of task states."""

    def __init__(self, running: list[str], states: list[dict]) -> None:
        self.running, self.states = running, list(states)
        self.started: list[dict] = []

    def list_tasks(self, **kwargs):
        assert kwargs == {
            "cluster": ENV["SILVER_CLUSTER"],
            "family": "vlc-housing-silver",
            "desiredStatus": "RUNNING",
        }
        return {"taskArns": self.running}

    def run_task(self, **kwargs):
        self.started.append(kwargs)
        return {"tasks": [{"taskArn": "arn:syn:task/new"}], "failures": []}

    def describe_tasks(self, **kwargs):
        return {"tasks": [self.states.pop(0) if len(self.states) > 1 else self.states[0]]}


class FakeContext:
    def __init__(self, remaining_ms: list[int]) -> None:
        self.remaining = list(remaining_ms)

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]


RUNNING = {"lastStatus": "RUNNING", "containers": [{}]}


def stopped(exit_code: int | None, reason: str = "Essential container in task exited") -> dict:
    return {
        "lastStatus": "STOPPED",
        "stoppedReason": reason,
        "containers": [{"exitCode": exit_code} if exit_code is not None else {}],
    }


def run(ecs: FakeEcs, remaining_ms=(600_000,), records: int = 1):
    event = {"Records": [{"body": "{}"} for _ in range(records)]}
    return handler(
        event, FakeContext(list(remaining_ms)), ecs=ecs, environ=ENV, sleep=lambda _s: None
    )


class TestConsumer(unittest.TestCase):
    def test_with_no_task_running_it_starts_one_with_the_network_configuration(self) -> None:
        ecs = FakeEcs(running=[], states=[RUNNING, stopped(0)])

        run(ecs)

        self.assertEqual(1, len(ecs.started))
        started = ecs.started[0]
        self.assertEqual(ENV["SILVER_TASK_DEFINITION"], started["taskDefinition"])
        self.assertEqual("FARGATE", started["launchType"])
        self.assertEqual(
            {
                "subnets": ["subnet-a", "subnet-b"],
                "securityGroups": ["sg-silver"],
                "assignPublicIp": "ENABLED",
            },
            started["networkConfiguration"]["awsvpcConfiguration"],
        )

    def test_with_a_task_running_it_adopts_it_and_starts_none(self) -> None:
        ecs = FakeEcs(running=["arn:syn:task/earlier"], states=[RUNNING, stopped(0)])

        run(ecs)

        self.assertEqual([], ecs.started)

    def test_a_batch_of_ten_starts_at_most_one_task(self) -> None:
        ecs = FakeEcs(running=[], states=[stopped(0)])

        run(ecs, records=10)

        self.assertEqual(1, len(ecs.started))

    def test_a_non_zero_exit_raises_so_the_batch_is_redelivered(self) -> None:
        with self.assertRaisesRegex(TaskFailed, "exit code 1"):
            run(FakeEcs(running=[], states=[stopped(1)]))

    def test_a_task_that_never_started_raises(self) -> None:
        with self.assertRaisesRegex(TaskFailed, "CannotPullContainerError"):
            run(
                FakeEcs(running=[], states=[stopped(None, "CannotPullContainerError: pull failed")])
            )

    def test_near_the_deadline_it_raises_and_leaves_the_task_running(self) -> None:
        ecs = FakeEcs(running=[], states=[RUNNING])

        with self.assertRaises(TaskStillRunning):
            run(ecs, remaining_ms=(600_000, 30_000))

        self.assertEqual(1, len(ecs.started))
        self.assertFalse(hasattr(ecs, "stop_task_called"))

    def test_it_imports_only_the_standard_library_and_boto3(self) -> None:
        tree = ast.parse(CONSUMER.read_text(encoding="utf-8"))
        imported = {
            (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        self.assertFalse(imported & {"duckdb", "pyiceberg", "pyarrow", "src"}, imported)


if __name__ == "__main__":
    unittest.main()
