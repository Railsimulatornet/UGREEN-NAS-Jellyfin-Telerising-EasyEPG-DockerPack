"""Offline regression checks for image identity, coverage and fail-closed scans."""

from contextlib import redirect_stdout
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("image_security", Path(__file__).with_name("image-security.py"))
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def report(image="linuxserver/jellyfin:latest", platform="linux/amd64"):
    os_name, architecture = platform.split("/")
    return {"SchemaVersion": 2, "ArtifactName": image, "ArtifactType": "container_image",
            "Metadata": {"ImageID": "sha256:" + "a" * 64,
                         "ImageConfig": {"architecture": architecture, "os": os_name},
                         "OS": {"Family": "alpine", "Name": "3.23"}},
            "Results": [{"Class": "os-pkgs", "Target": "alpine", "Packages": [{"Name": "example", "Version": "1.0"}]}]}


class ImageScanTests(unittest.TestCase):
    def evaluate(self, value):
        return scanner.evaluate_report(value, "linuxserver/jellyfin:latest", "linux/amd64")

    def test_clean_complete_inventory_passes(self):
        self.assertEqual(self.evaluate(report())["exit_code"], 0)

    def test_fixable_high_and_critical_block(self):
        for severity in ("HIGH", "CRITICAL"):
            with self.subTest(severity=severity):
                value = report()
                value["Results"][0]["Vulnerabilities"] = [{"Severity": severity, "FixedVersion": "1.1"}]
                self.assertEqual(self.evaluate(value)["exit_code"], 1)

    def test_unfixed_findings_remain_counted(self):
        value = report()
        value["Results"][0]["Vulnerabilities"] = [{"Severity": "HIGH", "FixedVersion": ""}, {"Severity": "CRITICAL"}]
        result = self.evaluate(value)
        self.assertEqual((result["exit_code"], result["findings"]), (0, 2))
        self.assertEqual(len(value["Results"][0]["Vulnerabilities"]), 2)

    def test_lower_severity_does_not_fake_high_severity_failure(self):
        value = report()
        value["Results"][0]["Vulnerabilities"] = [{"Severity": "MEDIUM", "FixedVersion": "1.1"}]
        self.assertEqual(self.evaluate(value)["exit_code"], 0)

    def test_end_of_life_blocks_even_without_findings(self):
        value = report()
        value["Metadata"]["OS"]["EOSL"] = True
        self.assertEqual(self.evaluate(value)["exit_code"], 1)

    def test_wrong_image_or_platform_cannot_pass(self):
        for value in (report(image="unreviewed/image:latest"), report(platform="linux/arm64")):
            with self.subTest(report=value):
                with self.assertRaises(ValueError):
                    self.evaluate(value)

    def test_missing_identity_schema_or_inventory_fails_closed(self):
        examples = [None, {}, report(), report(), report(), report()]
        examples[2]["Metadata"].pop("ImageID")
        examples[3]["Metadata"].pop("OS")
        examples[4]["Results"] = []
        examples[5]["Results"][0].pop("Packages")
        for value in examples:
            with self.subTest(report=value):
                with self.assertRaises(ValueError):
                    self.evaluate(value)

    def test_invalid_findings_cannot_be_silently_ignored(self):
        for finding in (None, {"Severity": "high"}, {"Severity": "HIGH", "FixedVersion": ["1.1"]}):
            value = report()
            value["Results"][0]["Vulnerabilities"] = [finding]
            with self.subTest(finding=finding), self.assertRaises(ValueError):
                self.evaluate(value)

    def make_compose(self, root, pack="jellyfin"):
        for name in scanner.PACKS[pack]["compose"]:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("services:\n" + "".join(f"  {key}:\n    image: {image}\n" for key, image in scanner.PACKS[pack]["images"].items()))

    def test_all_languages_and_variants_require_same_reviewed_images(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_compose(root)
            self.assertEqual(len(scanner.checked_images(root, "jellyfin", "linux/arm64")), 4)
            path = root / scanner.PACKS["jellyfin"]["compose"][-1]
            path.write_text(path.read_text().replace("starfoxfs/telerising:latest", "private.example/new:latest"))
            with self.assertRaises(ValueError):
                scanner.checked_images(root, "jellyfin", "linux/arm64")

    def test_uncovered_compose_and_unsupported_platform_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_compose(root, "emby")
            with self.assertRaises(ValueError):
                scanner.checked_images(root, "emby", "linux/arm64")
            (root / "docker-compose.yaml").write_text("services: {}\n")
            with self.assertRaises(ValueError):
                scanner.checked_images(root, "emby", "linux/amd64")

    def test_flow_quoted_and_external_service_bypasses_fail_closed(self):
        suffixes = (
            "  extra: {image: unreviewed/example:latest}\n",
            "  extra:\n    'image': unreviewed/example:latest\n",
            "  extra:\n    build: .\n",
            "  extra:\n    extends: {file: other.yaml, service: example}\n",
            "include: [other.yaml]\n",
            "services: {}\n",
        )
        for suffix in suffixes:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.make_compose(root)
                path = root / scanner.PACKS["jellyfin"]["compose"][0]
                path.write_text(path.read_text() + suffix)
                with self.assertRaises(ValueError):
                    scanner.checked_images(root, "jellyfin", "linux/amd64")

    def test_standard_compose_files_cannot_escape_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_compose(root)
            workflow = root / ".github/workflows/compose-validation.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: Existing workflow\n")
            self.assertEqual(len(scanner.checked_images(root, "jellyfin", "linux/amd64")), 4)
            for name in ("compose.yaml", "compose.yml", "compose.override.yaml", "compose.override.yml"):
                with self.subTest(name=name):
                    extra = root / "v1/de/JellyfinServer" / name
                    extra.write_text("services: {extra: {image: unreviewed/example:latest}}\n")
                    with self.assertRaises(ValueError):
                        scanner.checked_images(root, "jellyfin", "linux/amd64")
                    extra.unlink()

    def test_safe_yaml_handles_legitimate_flow_quotes_and_merge_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_compose(root)
            for name in scanner.PACKS["jellyfin"]["compose"]:
                path = root / name
                path.write_text(
                    "x-environment: &environment {TZ: UTC}\nservices:\n" +
                    "".join(f"  {key}:\n    'image': {image}\n    environment:\n      <<: *environment\n      TZ: Europe/Berlin\n" for key, image in scanner.PACKS["jellyfin"]["images"].items())
                )
            self.assertEqual(len(scanner.checked_images(root, "jellyfin", "linux/amd64")), 4)

    def run_simulated_scan(self, failure=None):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            root.mkdir()
            self.make_compose(root)
            output = Path(temp) / "reports"

            def fake_run(command, log, cwd, environment, timeout=1000):
                calls.append(command)
                log.write_text("offline fixture\n")
                if command[1] == "--version":
                    return 0
                destination = Path(command[command.index("--output") + 1])
                if command[1] == "image":
                    if failure == "scanner" and command[-1] == "linuxserver/swag:latest":
                        return 1
                    value = report(image=command[-1])
                    if failure == "actionable":
                        value["Results"][0]["Vulnerabilities"] = [{"Severity": "HIGH", "FixedVersion": "1.1"}]
                    destination.write_text(json.dumps(value))
                else:
                    if failure == "conversion":
                        return 1
                    destination.write_text(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Trivy"}}, "results": []}]}))
                return 0

            with patch.object(scanner, "run_logged", side_effect=fake_run), redirect_stdout(io.StringIO()):
                code = scanner.scan_package(root, "jellyfin", "linux/amd64", output)
            summary = json.loads((output / "summary.json").read_text())
            reports = sorted(p.name for p in output.glob("*.json"))
        return code, summary, calls, reports

    def test_scan_reads_each_image_once_and_converts_same_report(self):
        code, summary, calls, reports = self.run_simulated_scan()
        images = [c for c in calls if c[1] == "image"]
        conversions = [c for c in calls if c[1] == "convert"]
        self.assertEqual(code, 0)
        self.assertEqual(len(images), 4)
        self.assertEqual(len(conversions), 4)
        self.assertEqual(len(summary["images"]), 4)
        for command in images:
            self.assertEqual(command[command.index("--image-src") + 1], "remote")
            self.assertIn("--ignore-unfixed=false", command)
            self.assertEqual(command[command.index("--scanners") + 1], "vuln")
        self.assertTrue(all(Path(command[-1]).name in reports for command in conversions))

    def test_scanner_error_is_technical_failure_and_other_images_still_scan(self):
        code, summary, calls, _ = self.run_simulated_scan("scanner")
        self.assertEqual(code, 2)
        self.assertEqual(len(summary["images"]), 4)
        self.assertEqual(len([c for c in calls if c[1] == "image"]), 4)

    def test_conversion_error_cannot_return_success(self):
        code, _, _, _ = self.run_simulated_scan("conversion")
        self.assertEqual(code, 2)

    def test_actionable_gate_keeps_all_reports(self):
        code, summary, _, reports = self.run_simulated_scan("actionable")
        self.assertEqual(code, 1)
        self.assertEqual(len(summary["images"]), 4)
        self.assertEqual(len(reports), 5)


if __name__ == "__main__":
    unittest.main()
