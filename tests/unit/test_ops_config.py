"""Operations config is code: alerts link to runbook sections that exist, SLO rules are generated, routing covers
every severity, workloads run with probes and least privilege (T25.5, T25.8, T24.1, T24.6)."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/runbook.md"


def _anchor(heading: str) -> str:  # GitHub's rule: lower case, drop punctuation, spaces to hyphens
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


ANCHORS = {_anchor(h) for h in re.findall(r"^#{2,3} (.+)$", RUNBOOK.read_text(), re.M)}
ALERTS = [
    r
    for f in sorted((ROOT / "ops/alerts").glob("*.yml"))
    for g in yaml.safe_load(f.read_text())["groups"]
    for r in g["rules"]
    if "alert" in r
]


def test_alert_files_are_found() -> None:
    assert len(ALERTS) >= 20


@pytest.mark.parametrize("rule", ALERTS, ids=lambda r: r["alert"])
def test_every_alert_routes_and_links_to_an_existing_runbook_section(rule: dict) -> None:
    url = rule["annotations"]["runbook_url"]
    assert url.startswith("https://github.com/Sha-Hifzan-Razzakh/disputes-service/blob/main/docs/runbook.md#")
    assert url.split("#", 1)[1] in ANCHORS, f"{rule['alert']}: no section #{url.split('#', 1)[1]} in docs/runbook.md"
    assert rule["labels"]["severity"] in {"page", "ticket"}
    assert rule["annotations"]["summary"]


def test_alertmanager_routes_every_severity() -> None:
    am = yaml.safe_load((ROOT / "ops/alertmanager/alertmanager.yml").read_text())
    matchers = " ".join(m for r in am["route"]["routes"] for m in r["matchers"])
    assert 'severity="page"' in matchers and "ticket" in matchers
    receivers = {r["name"] for r in am["receivers"]}
    assert {r["receiver"] for r in am["route"]["routes"]} <= receivers
    assert "runbook_url" in yaml.safe_dump(am["receivers"])


def test_slo_rules_are_generated_from_the_single_source() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("slos", ROOT / "ops/slo/slos.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["slos"] = mod  # dataclasses resolve their module while the file executes
    spec.loader.exec_module(mod)
    assert (ROOT / "ops/alerts/slo.rules.yml").read_text() == mod.render(), "run: python ops/slo/slos.py"


def _containers() -> list[tuple[str, dict]]:
    out = []
    for f in sorted((ROOT / "deploy/k8s").rglob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not doc or doc.get("kind") not in {"Deployment", "Job", "CronJob"}:
                continue
            spec = doc["spec"]["jobTemplate"]["spec"] if doc["kind"] == "CronJob" else doc["spec"]
            pod = spec["template"]["spec"]
            for c in pod["containers"]:
                out.append((f"{doc['kind']}/{doc['metadata']['name']}", {"pod": pod, "c": c, "kind": doc["kind"]}))
    return out


@pytest.mark.parametrize(("name", "w"), _containers(), ids=lambda x: x if isinstance(x, str) else "")
def test_workloads_run_least_privileged_with_probes(name: str, w: dict) -> None:
    pod, c = w["pod"], w["c"]
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["automountServiceAccountToken"] is False
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False and sc["capabilities"]["drop"] == ["ALL"]
    assert sc["readOnlyRootFilesystem"] is True and "limits" in c["resources"]
    if w["kind"] == "Deployment":
        assert "readinessProbe" in c and "livenessProbe" in c, name


def test_cost_report_sums_per_tenant_and_day() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("cost_report", ROOT / "scripts/cost_report.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params["query"]
        value = "1.25" if "cost" in q else ("1000" if '"in"' in q else "200")
        return httpx.Response(
            200, json={"data": {"result": [{"metric": {"tenant": "northwind"}, "values": [[1793836800, value]]}]}}
        )

    table = mod.report("http://prom", 1, httpx.Client(transport=httpx.MockTransport(handler)))
    assert "| northwind | 1.25 | 1000 | 200 |" in table
