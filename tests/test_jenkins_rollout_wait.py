import base64
import copy

import pytest
from test_jenkins_rollout import ROLES, applications

from scripts.jenkins import observe_rollout as rollout

SOURCE = "a" * 40
COMMIT = "b" * 40
LATER = "c" * 40
DIGEST = "sha256:" + "d" * 64
IMAGE = "975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai"


def release():
    return {
        "source_sha": SOURCE,
        "digest": DIGEST,
        "image": IMAGE + "@" + DIGEST,
        "image_tag": "dev-" + SOURCE,
        "cloud_commit": COMMIT,
        "environment": "staging",
        "cluster": "star-EKS-v3-staging",
        "applications": list(ROLES),
        "gitops_repository": "100-hours-a-week/KTB4-17th-CLOUD",
        "base_branch": "main",
    }


class Cloud:
    repository = "100-hours-a-week/KTB4-17th-CLOUD"

    def __init__(self, digest=DIGEST):
        self.digest = digest

    def request(self, method, endpoint, payload=None):
        assert method == "GET", "Rollout observation must never write GitHub"
        if endpoint == "git/ref/heads/main":
            return {"object": {"sha": LATER}}
        if endpoint.startswith("compare/"):
            return {"status": "ahead"}
        assert endpoint.startswith("contents/manifest/helm/star23-service/values/staging/")
        text = f"image:\n  repository: {IMAGE}\n  digest: {self.digest}\n  tag: dev-{SOURCE}\n"
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class Reader:
    def __init__(self, phases=("Healthy",), image=IMAGE + "@" + DIGEST):
        self.phases = iter(phases)
        self.phase = "Healthy"
        self.image = image

    def applications(self):
        self.phase = next(self.phases, self.phase)
        data = applications("dev")
        for app in data["items"]:
            app["status"]["sync"]["revision"] = LATER
            app["status"]["health"]["status"] = self.phase
            app["status"]["summary"] = {"images": [self.image]}
            app["spec"]["syncPolicy"] = {"automated": {"enabled": True}}
            app["status"]["sync"]["comparedTo"] = {k: copy.deepcopy(app["spec"][k]) for k in ["source", "destination"]}
        return data


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def wait(reader, cloud=None, receipt=None):
    clock = Clock()
    return rollout.wait_for_release(
        reader,
        cloud or Cloud(),
        receipt or release(),
        branch="dev",
        timeout=3,
        interval=1,
        clock=clock,
        sleep=clock.sleep,
    )


def test_compatible_later_cloud_revision_can_complete_the_published_ai_release():
    result = wait(Reader())
    assert result["status"] == "deployed"
    assert result["cloud_commit"] == COMMIT
    assert result["observed_revision"] == LATER
    assert result["image"] == IMAGE + "@" + DIGEST


def test_progressing_apps_are_polled_until_all_five_are_healthy():
    result = wait(Reader(("Progressing", "Healthy")))
    assert result["status"] == "deployed"
    assert len(result["applications"]) == 5


def test_healthy_old_image_cannot_be_reported_as_deployed():
    with pytest.raises(TimeoutError):
        wait(Reader(image=IMAGE + "@sha256:" + "e" * 64))


def test_superseding_ai_digest_in_cloud_fails_instead_of_reporting_old_release_success():
    with pytest.raises(ValueError, match="superseded"):
        wait(Reader(), Cloud("sha256:" + "e" * 64))


def test_staging_release_cannot_redirect_observer_to_prod():
    receipt = release()
    receipt["cluster"] = "star-EKS-v3"
    with pytest.raises(ValueError):
        wait(Reader(), receipt=receipt)
