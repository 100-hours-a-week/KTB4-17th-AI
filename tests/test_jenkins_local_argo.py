import copy

from test_jenkins_rollout import REVISION, applications

from scripts.jenkins import observe_rollout


def test_each_branch_uses_its_own_existing_argo_control_cluster():
    assert (
        observe_rollout.context_for_branch("dev")
        == "arn:aws:eks:ap-northeast-2:975779326240:cluster/star-EKS-v3-staging"
    )
    assert observe_rollout.context_for_branch("main") == "arn:aws:eks:ap-northeast-2:975779326240:cluster/star-EKS-v3"


def test_staging_accepts_existing_local_apps_only_with_staging_values():
    data = applications("dev")
    for item in data["items"]:
        item["metadata"]["name"] = item["metadata"]["name"].removeprefix("staging-")
        item["spec"]["destination"]["server"] = "https://kubernetes.default.svc"
        item["status"]["sync"]["comparedTo"] = copy.deepcopy(item["spec"])
    result = observe_rollout.verify_applications(data, branch="dev", cloud_commit=REVISION)
    assert result["status"] == "healthy"
    assert all(not name.startswith("staging-") for name in result["applications"])
