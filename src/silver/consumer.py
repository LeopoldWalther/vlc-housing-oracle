"""Lambda consumer of the Silver queue: keep at most one Silver task running, then wait for it.

The SQS FIFO queue has one message group, so this handler never runs twice at once. That makes
"adopt or start" race-free: a Silver task that is already running, because an earlier invocation
timed out or an operator started a backfill, is waited for instead of joined by a second one.
The message body is never read; the task finds its work by the set difference itself.

Packaged as a plain zip: the standard library and boto3 (provided by the Lambda runtime) only.
"""

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

POLL_SECONDS = 15
# Raise this long before the Lambda deadline, so the failure is ours and the batch is redelivered
# rather than lost with a killed invocation.
DEADLINE_MARGIN_MS = 60_000


class TaskFailed(Exception):
    """The Silver task stopped with a non-zero exit code, or never started."""


class TaskStillRunning(Exception):
    """The Lambda deadline is near and the Silver task still runs; the redelivery adopts it."""


@dataclass(frozen=True)
class ConsumerConfig:
    cluster: str
    task_definition: str
    task_family: str
    subnets: tuple[str, ...]
    security_group: str
    assign_public_ip: str

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "ConsumerConfig":
        return cls(
            cluster=environ["SILVER_CLUSTER"],
            task_definition=environ["SILVER_TASK_DEFINITION"],
            task_family=environ["SILVER_TASK_FAMILY"],
            subnets=tuple(s for s in environ["SILVER_SUBNETS"].split(",") if s),
            security_group=environ["SILVER_SECURITY_GROUP"],
            assign_public_ip=environ.get("SILVER_ASSIGN_PUBLIC_IP", "ENABLED"),
        )


def handler(
    event: Mapping,
    context,
    *,
    ecs=None,
    environ: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Adopt the running Silver task or start one, wait for it, and fail loudly unless it succeeds."""
    config = ConsumerConfig.from_environ(os.environ if environ is None else environ)
    if ecs is None:
        import boto3

        ecs = boto3.client("ecs")
    task_arn = _running_task(ecs, config) or _start_task(ecs, config)
    while True:
        task = ecs.describe_tasks(cluster=config.cluster, tasks=[task_arn])["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            return _outcome(task_arn, task)
        if context.get_remaining_time_in_millis() < DEADLINE_MARGIN_MS:
            raise TaskStillRunning(f"{task_arn} still runs at the Lambda deadline")
        sleep(POLL_SECONDS)


def _running_task(ecs, config: ConsumerConfig) -> str | None:
    # desiredStatus RUNNING includes tasks still being provisioned, so a task this consumer just
    # started in a timed-out invocation is found too.
    arns = ecs.list_tasks(
        cluster=config.cluster, family=config.task_family, desiredStatus="RUNNING"
    )["taskArns"]
    return arns[0] if arns else None


def _start_task(ecs, config: ConsumerConfig) -> str:
    response = ecs.run_task(
        cluster=config.cluster,
        taskDefinition=config.task_definition,
        launchType="FARGATE",
        count=1,
        startedBy="silver-queue-consumer",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": list(config.subnets),
                "securityGroups": [config.security_group],
                "assignPublicIp": config.assign_public_ip,
            }
        },
    )
    if response.get("failures") or not response.get("tasks"):
        raise TaskFailed(f"RunTask started no task: {response.get('failures')}")
    return response["tasks"][0]["taskArn"]


def _outcome(task_arn: str, task: Mapping) -> dict:
    exit_code = task["containers"][0].get("exitCode") if task.get("containers") else None
    if exit_code is None:
        raise TaskFailed(f"{task_arn} stopped without running: {task.get('stoppedReason')}")
    if exit_code != 0:
        raise TaskFailed(f"{task_arn} stopped with exit code {exit_code}")
    return {"task": task_arn, "exit_code": 0}
