#!/usr/bin/env bash

set -euo pipefail

if [[ "$#" -ne 4 ]]; then
    echo "usage: $0 <rendered-task-definition> <service-description> <cluster> <container>" >&2
    exit 64
fi

rendered_task_definition=$1
service_description=$2
cluster=$3
container_name=$4
migration_family=${MIGRATION_TASK_FAMILY:-star23-ai-migration}

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

migration_task_definition="$work_dir/migration-task-definition.json"
run_task_result="$work_dir/run-task.json"
describe_task_result="$work_dir/describe-task.json"

jq -e --arg container "$container_name" \
    '([.containerDefinitions[] | select(.name == $container)] | length) == 1' \
    "$rendered_task_definition" > /dev/null

jq \
    --arg family "$migration_family" \
    --arg container "$container_name" \
    '
        .family = $family
        | .containerDefinitions |= map(
            if .name == $container then
                .entryPoint = ["/code/.venv/bin/alembic"]
                | .command = ["upgrade", "head"]
            else
                .
            end
        )
        | del(
            .taskDefinitionArn,
            .revision,
            .status,
            .requiresAttributes,
            .compatibilities,
            .registeredAt,
            .registeredBy
        )
    ' \
    "$rendered_task_definition" > "$migration_task_definition"

migration_task_definition_arn=$(aws ecs register-task-definition \
    --cli-input-json "file://$migration_task_definition" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

jq -e '(.services | length) == 1 and .services[0].networkConfiguration != null' \
    "$service_description" > /dev/null
network_configuration=$(jq -c '.services[0].networkConfiguration' "$service_description")

aws ecs run-task \
    --cluster "$cluster" \
    --launch-type FARGATE \
    --task-definition "$migration_task_definition_arn" \
    --network-configuration "$network_configuration" > "$run_task_result"

if ! jq -e '(.failures | length) == 0 and (.tasks | length) == 1' "$run_task_result" > /dev/null; then
    jq '{failures: [.failures[]? | {arn, reason, detail}]}' "$run_task_result"
    exit 1
fi
migration_task_arn=$(jq -er '.tasks[0].taskArn' "$run_task_result")

echo "Migration task started: $migration_task_arn"
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$migration_task_arn"
aws ecs describe-tasks \
    --cluster "$cluster" \
    --tasks "$migration_task_arn" > "$describe_task_result"

jq \
    --arg container "$container_name" \
    '{
        stopCode: .tasks[0].stopCode,
        stoppedReason: .tasks[0].stoppedReason,
        container: (
            [.tasks[0].containers[] | select(.name == $container) | {name, exitCode, reason}]
            | first
        )
    }' \
    "$describe_task_result"

jq -e \
    --arg container "$container_name" \
    '
        (.failures | length) == 0
        and (.tasks | length) == 1
        and ([.tasks[0].containers[] | select(.name == $container)] | length) == 1
        and ([.tasks[0].containers[] | select(.name == $container)][0].exitCode == 0)
    ' \
    "$describe_task_result" > /dev/null

echo "Database migration completed successfully."
