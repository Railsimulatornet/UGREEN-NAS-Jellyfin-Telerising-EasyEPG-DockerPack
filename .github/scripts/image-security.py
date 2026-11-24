"""Scan the package's public images without starting containers.

Exit 0: complete scan, no fixable HIGH/CRITICAL findings and no end-of-life OS.
Exit 1: actionable findings. Exit 2: incomplete or invalid scan evidence.
All findings, including currently unfixed findings, remain in the reports.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import yaml


PACKS = {
    "jellyfin": {
        "images": {
            "jellyfin": "linuxserver/jellyfin:latest",
            "swag": "linuxserver/swag:latest",
            "easyepg": "railsimulatornet/new-easyepg:latest",
            "telerising": "starfoxfs/telerising:latest",
        },
        "compose": tuple(
            f"v1/{language}/JellyfinServer/docker-compose{variant}.yaml"
            for language in ("de", "en") for variant in ("DXP", "DH")
        ),
        "platforms": ("linux/amd64", "linux/arm64"),
    },
    "emby": {
        "images": {
            "emby": "emby/embyserver:latest",
            "swag": "linuxserver/swag:latest",
            "easyepg": "railsimulatornet/new-easyepg:latest",
            "telerising": "starfoxfs/telerising:latest",
        },
        "compose": ("de/embyserver/docker-compose.yaml", "en/embyserver/docker-compose.yaml"),
        "platforms": ("linux/amd64",),
    },
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ComposeLoader(yaml.SafeLoader):
    """Safe YAML with ordinary duplicate keys rejected before merge expansion."""

    def construct_mapping(self, node, deep=False):
        explicit_keys = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, (str, int, float, bool)) or key in explicit_keys:
                raise ValueError("Unsupported or duplicate Compose mapping key")
            explicit_keys.add(key)
        self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)


def checked_images(root, pack, platform):
    spec = PACKS[pack]
    if platform not in spec["platforms"]:
        raise ValueError("Unsupported package platform")
    actual_paths = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.suffix in (".yml", ".yaml")
        and (p.name.startswith("docker-compose") or p.name in ("compose.yaml", "compose.yml") or p.name.startswith("compose."))
        and not {".git", ".github"}.intersection(p.relative_to(root).parts)
    }
    if actual_paths != set(spec["compose"]):
        raise ValueError("Compose file inventory changed; review image coverage")
    expected = set(spec["images"].values())
    for name in spec["compose"]:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Compose input is not a regular file")
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Compose input exceeds the reviewed size limit")
        try:
            document = yaml.load(path.read_text(), Loader=ComposeLoader)
        except yaml.YAMLError as error:
            raise ValueError("Compose YAML could not be safely parsed") from error
        if not isinstance(document, dict) or "include" in document:
            raise ValueError("External Compose includes require a coverage review")
        services = document.get("services")
        if not isinstance(services, dict):
            raise ValueError("Missing Compose services")
        references = []
        for service in services.values():
            if not isinstance(service, dict) or any(key in service for key in ("build", "extends", "provider")):
                raise ValueError("Only direct public image services are covered")
            image = service.get("image")
            if not isinstance(image, str):
                raise ValueError("Every Compose service requires a reviewed image")
            if service.get("platform", platform) not in spec["platforms"]:
                raise ValueError("Compose requests an unreviewed image platform")
            references.append(image)
        if len(references) != len(expected) or set(references) != expected:
            raise ValueError("Compose images differ from the reviewed public image list")
    return spec["images"]


def normalize_image(value):
    if not isinstance(value, str):
        return None
    return value.removeprefix("docker.io/")


def evaluate_report(report, image, platform):
    if not isinstance(report, dict) or report.get("SchemaVersion") != 2:
        raise ValueError("Missing or unsupported Trivy report schema")
    if report.get("ArtifactType") != "container_image":
        raise ValueError("Report is not a container-image scan")
    if normalize_image(report.get("ArtifactName")) != normalize_image(image):
        raise ValueError("Report belongs to another image")
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict) or not DIGEST.fullmatch(metadata.get("ImageID", "")):
        raise ValueError("Missing image identity")
    config = metadata.get("ImageConfig")
    operating_system, architecture = platform.split("/")
    if not isinstance(config, dict) or config.get("architecture") != architecture or config.get("os") != operating_system:
        raise ValueError("Report belongs to another platform")
    system = metadata.get("OS")
    if not isinstance(system, dict) or not system.get("Family") or not system.get("Name"):
        raise ValueError("Operating-system coverage is unknown")
    if not isinstance(system.get("EOSL", False), bool):
        raise ValueError("Invalid operating-system support evidence")
    results = report.get("Results")
    if not isinstance(results, list) or not results:
        raise ValueError("Missing package scan results")
    fixable = 0
    findings = 0
    os_packages = False
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Invalid package result")
        if result.get("Class") == "os-pkgs" and isinstance(result.get("Packages"), list) and result["Packages"]:
            os_packages = True
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise ValueError("Invalid vulnerability list")
        for finding in vulnerabilities:
            if not isinstance(finding, dict) or finding.get("Severity") not in ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"):
                raise ValueError("Invalid vulnerability evidence")
            fixed = finding.get("FixedVersion", "")
            if not isinstance(fixed, str):
                raise ValueError("Invalid fix availability")
            findings += 1
            if finding["Severity"] in ("HIGH", "CRITICAL") and fixed.strip():
                fixable += 1
    if not os_packages:
        raise ValueError("No operating-system package inventory was scanned")
    return {
        "image": image,
        "platform": platform,
        "image_id": metadata["ImageID"],
        "repo_digests": metadata.get("RepoDigests", []),
        "findings": findings,
        "fixable_high_critical": fixable,
        "end_of_life_os": system.get("EOSL", False),
        "exit_code": 1 if fixable or system.get("EOSL", False) else 0,
    }


def run_logged(command, log, cwd, environment, timeout=1000):
    with log.open("w") as output:
        try:
            return subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=False).returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            output.write(f"Scanner command failed: {error}\n")
            return 2


def scan_package(root, pack, platform, report_dir):
    images = checked_images(root, pack, platform)
    report_dir.mkdir(parents=True, exist_ok=True)
    # Reports are created in a fresh runner directory, not in the checked-out tree.
    if any(report_dir.iterdir()):
        raise ValueError("The report directory must be empty")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TRIVY_") and key not in ("GITHUB_TOKEN", "GH_TOKEN", "GIT_ASKPASS", "SSH_AUTH_SOCK")}
    summary = []
    result_code = 0
    with tempfile.TemporaryDirectory(prefix="public-image-scan-") as work:
        work = Path(work)
        (work / "config.yaml").write_text("{}\n")
        (work / "ignore").write_text("")
        (work / "docker").mkdir()
        environment["DOCKER_CONFIG"] = str(work / "docker")
        common = ["--config", str(work / "config.yaml"), "--cache-dir", str(work / "cache")]
        if run_logged(["trivy", "--version"], report_dir / "version.txt", work, environment, timeout=30) != 0:
            return 2
        for name, image in images.items():
            report_path = report_dir / f"{name}.json"
            command = ["trivy", "image", *common, "--image-src", "remote", "--platform", platform,
                       "--scanners", "vuln", "--pkg-types", "os,library", "--list-all-pkgs",
                       "--ignorefile", str(work / "ignore"), "--ignore-unfixed=false",
                       "--severity", "UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL", "--timeout", "15m",
                       "--format", "json", "--exit-code", "0", "--output", str(report_path), image]
            code = run_logged(command, report_dir / f"{name}.log", work, environment)
            try:
                if code != 0:
                    raise ValueError("The image scan did not complete")
                evaluation = evaluate_report(json.loads(report_path.read_text()), image, platform)
                sarif_path = report_dir / f"{name}.sarif"
                convert = ["trivy", "convert", *common, "--format", "sarif", "--output", str(sarif_path), str(report_path)]
                if run_logged(convert, report_dir / f"{name}-convert.log", work, environment, timeout=120) != 0:
                    raise ValueError("SARIF conversion did not complete")
                sarif = json.loads(sarif_path.read_text())
                if not isinstance(sarif, dict) or sarif.get("version") != "2.1.0" or not isinstance(sarif.get("runs"), list) or not sarif["runs"]:
                    raise ValueError("SARIF evidence is incomplete")
                result_code = max(result_code, evaluation["exit_code"])
                summary.append(evaluation)
                print(f"{name}: complete scan; {evaluation['fixable_high_critical']} fixable high/critical findings; end-of-life OS: {evaluation['end_of_life_os']}")
            except (OSError, ValueError, TypeError) as error:
                result_code = 2
                summary.append({"image": image, "platform": platform, "exit_code": 2, "error": str(error)})
                print(f"::error::{name}: {error}")
    (report_dir / "summary.json").write_text(json.dumps({"images": summary, "exit_code": result_code}, indent=2) + "\n")
    return result_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", choices=PACKS)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        return scan_package(Path(__file__).resolve().parents[2], arguments.pack, arguments.platform, arguments.report_dir.resolve())
    except (OSError, ValueError, TypeError) as error:
        print(f"::error::Image scan could not be verified: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
