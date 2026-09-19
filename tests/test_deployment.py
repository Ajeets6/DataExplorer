from pathlib import Path

import hcl2


def test_both_uis_route_api_calls_through_private_network():
    path = Path(__file__).parents[1] / "infra/terraform/modules/platform/main.tf"
    config = hcl2.loads(path.read_text())
    resources = {}
    for resource in config["resource"]:
        resources.update(resource.get("google_cloud_run_v2_service", {}))
    assert resources["api"]["ingress"] == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    for name in ("ui", "admin_ui"):
        template = resources[name]["template"][0]
        vpc = template["vpc_access"][0]
        assert vpc["egress"] == "ALL_TRAFFIC"
        assert "google_compute_subnetwork.main.name" in vpc["network_interfaces"][0]["subnetwork"]
        environment = {entry["name"]: entry.get("value") for entry in template["containers"][0]["env"]}
        assert environment["DATAEXPLORER_AUTH_MODE"] == "jwt"
