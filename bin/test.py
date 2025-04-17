#!/usr/bin/env python3
import yaml
import subprocess
import json
import sys
import os
import argparse
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

@dataclass
class ApplicationSet:
    """Represents an ArgoCD ApplicationSet with its metadata and content."""
    name: str
    content: Dict[str, Any]
    file_path: str

class ApplicationSetExtractor:
    """Handles extraction of ApplicationSets from helmfile."""

    @staticmethod
    def extract_from_helmfile(helmfile_path: str, environment: str = "testing") -> List[Dict[str, Any]]:
        """Extract ApplicationSet definition from a helmfile template command."""
        cmd = ["helmfile", "template", "--environment", environment]
        print(f"Executing: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error rendering helmfile: {result.stderr}")
            sys.exit(1)

        manifests = list(yaml.safe_load_all(result.stdout))
        appsets = [m for m in manifests if m and m.get("kind") == "ApplicationSet"]

        if not appsets:
            print("No ApplicationSet resources found in the rendered helmfile")
            sys.exit(1)

        print(f"Found {len(appsets)} ApplicationSet resources")
        return appsets

class ApplicationSetGenerator:
    """Handles generation of Applications from ApplicationSets using ArgoCD CLI."""

    @staticmethod
    def generate_applications(appset_file: str) -> List[Dict[str, Any]]:
        """Generate Applications from an ApplicationSet using ArgoCD CLI."""
        print("Generating applications using ArgoCD CLI")
        cmd = ["argocd", "appset", "generate", appset_file, "-o", "yaml"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error generating applications: {result.stderr}")
            return []

        return list(yaml.safe_load_all(result.stdout))

class ApplicationValidator:
    """Handles validation of Applications and ApplicationSets."""

    @staticmethod
    def validate_applications(apps: List[Dict[str, Any]], validation_rules: Optional[Dict[str, Callable]] = None) -> bool:
        """Validate the generated applications against predefined rules."""
        apps = [app for app in apps if app is not None]
        if not apps:
            print("No valid applications to validate")
            return False

        print(f"Found {len(apps)} generated applications")
        valid = True

        for i, app in enumerate(apps):
            print(f"Application {i+1}: {app.get('metadata', {}).get('name', 'unknown')}")
            valid = valid and ApplicationValidator._validate_single_application(app, i)

        if validation_rules:
            for rule_name, rule_func in validation_rules.items():
                print(f"Applying validation rule: {rule_name}")
                rule_passed = rule_func(apps)
                print(f"  {'PASSED' if rule_passed else 'FAILED'}")
                valid = valid and rule_passed

        return valid

    @staticmethod
    def _validate_single_application(app: Dict[str, Any], index: int) -> bool:
        valid = True

        if app.get("kind") != "Application":
            print(f"  WARNING: Application {index+1} has incorrect kind: {app.get('kind')}")

        if not app.get("spec"):
            print(f"  ERROR: Application {index+1} missing spec section")
            valid = False

        if not app["spec"].get("destination"):
            print(f"  ERROR: Application {index+1} missing destination")
            valid = False

        if not app["spec"].get("sources"):
            print(f"  ERROR: Application {index+1} missing source")
            valid = False

        return valid

    @staticmethod
    def validate_appset_structure(appset: Dict[str, Any]) -> bool:
        """Validate the ApplicationSet structure itself."""
        valid = True

        if appset.get("apiVersion") != "argoproj.io/v1alpha1":
            print(f"  ERROR: ApplicationSet has incorrect apiVersion: {appset.get('apiVersion')}")
            valid = False

        if appset.get("kind") != "ApplicationSet":
            print(f"  ERROR: ApplicationSet has incorrect kind: {appset.get('kind')}")
            valid = False

        if not appset.get("metadata"):
            print("  ERROR: ApplicationSet missing metadata section")
            valid = False
        elif not appset["metadata"].get("name"):
            print("  ERROR: ApplicationSet missing name in metadata")
            valid = False

        if not appset.get("spec"):
            print("  ERROR: ApplicationSet missing spec section")
            valid = False
        else:
            if not appset["spec"].get("generators"):
                print("  ERROR: ApplicationSet missing generators in spec")
                valid = False
            if not appset["spec"].get("template"):
                print("  ERROR: ApplicationSet missing template in spec")
                valid = False

        return valid

class ApplicationExporter:
    """Handles exporting of Applications and ApplicationSets to files."""

    @staticmethod
    def export_applications(apps: List[Dict[str, Any]], output_dir: str) -> None:
        """Export generated applications to individual YAML files."""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        apps = [app for app in apps if app is not None]
        if not apps:
            print("No valid applications to export")
            return

        for i, app in enumerate(apps):
            name = app.get("metadata", {}).get("name", f"app-{i+1}")
            output_file = os.path.join(output_dir, f"{name}.yaml")
            with open(output_file, "w") as f:
                yaml.dump(app, f)
            print(f"Exported application to {output_file}")

    @staticmethod
    def export_appset(appset: Dict[str, Any], output_dir: str) -> str:
        """Export an ApplicationSet to a YAML file."""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        name = appset.get("metadata", {}).get("name", "unknown-appset")
        output_file = os.path.join(output_dir, f"{name}.yaml")

        with open(output_file, "w") as f:
            yaml.dump(appset, f)

        print(f"Exported ApplicationSet to {output_file}")
        return output_file

def validate_plain_appset_sources(apps: List[Dict[str, Any]]) -> bool:
    """Validate that the plain ApplicationSet generates one sources entry pointing to the directory in the repo."""
    valid = True

    for app in apps:
        if not app.get("metadata", {}).get("name", "").startswith("in-cluster-"):
            continue

        sources = app.get("spec", {}).get("sources", [])
        if not sources:
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} missing sources")
            valid = False
            continue

        if len(sources) != 1:
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} has {len(sources)} sources, expected 1")
            valid = False
            continue

        source = sources[0]
        if source.get("repoURL") != "https://github.com/max06/deployments":
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} source repoURL is {source.get('repoURL')}, expected https://github.com/max06/deployments")
            valid = False

        path = source.get("path", "")
        if not (path.startswith("clusters/") or path.startswith("testing/")):
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} source path is {path}, expected to start with clusters/ or testing/")
            valid = False

    return valid

def main():
    parser = argparse.ArgumentParser(description="Test ArgoCD ApplicationSet generator combinations and templates")
    parser.add_argument("--output-dir", default="./testing-output",
                       help="Directory to export generated applications (default: ./testing-output)")
    args = parser.parse_args()

    # Clean and create output directory
    if args.output_dir and os.path.exists(args.output_dir):
        print(f"Cleaning output directory: {args.output_dir}")
        import shutil
        shutil.rmtree(args.output_dir)
    if args.output_dir:
        os.makedirs(args.output_dir)
        print(f"Created output directory: {args.output_dir}")

    # Process ApplicationSets
    all_apps = []
    appset_files = []
    temp_files = []

    # Extract ApplicationSets from helmfile
    appsets = ApplicationSetExtractor.extract_from_helmfile("helmfile.yaml", "testing")
    if not appsets:
        sys.exit(1)

    # Create ApplicationSet output directory
    appset_output_dir = os.path.join(args.output_dir, "applicationsets") if args.output_dir else None
    if appset_output_dir:
        os.makedirs(appset_output_dir)
        print(f"Created ApplicationSet output directory: {appset_output_dir}")

    # Process each ApplicationSet
    for appset in appsets:
        tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=True)
        temp_files.append(tmp)
        appset_file = tmp.name

        with open(appset_file, "w") as f:
            yaml.dump(appset, f)

        if appset_output_dir:
            ApplicationExporter.export_appset(appset, appset_output_dir)

        appset_files.append((appset.get("metadata", {}).get("name", "unknown"), appset_file, appset))

    # Process each ApplicationSet
    for appset_name, appset_file, appset in appset_files:
        print(f"\n=== Processing ApplicationSet: {appset_name} ===")

        # Validate ApplicationSet structure
        print(f"\n=== Validating ApplicationSet structure for {appset_name} ===")
        structure_valid = ApplicationValidator.validate_appset_structure(appset)
        print(f"Structure validation: {'PASSED' if structure_valid else 'FAILED'}")

        # Generate applications
        print(f"\n=== Generating applications for {appset_name} ===")
        apps = ApplicationSetGenerator.generate_applications(appset_file)

        if not apps:
            print(f"WARNING: No applications generated for ApplicationSet {appset_name}")
            continue

        all_apps.extend(apps)

        # Validate applications
        print(f"\n=== Validating applications for {appset_name} ===")
        validation_rules = {"plain_appset_sources": validate_plain_appset_sources} if appset_name == "plain" else {}
        valid = ApplicationValidator.validate_applications(apps, validation_rules)

        # Export applications if requested
        if apps and args.output_dir:
            appset_output_dir = os.path.join(args.output_dir, appset_name)
            print(f"\n=== Exporting applications for {appset_name} ===")
            ApplicationExporter.export_applications(apps, appset_output_dir)

        print(f"\n=== Summary for {appset_name} ===")
        print(f"Structure validation: {'PASSED' if structure_valid else 'FAILED'}")
        print(f"Applications generated: {len(apps)}")
        print(f"Application validation: {'PASSED' if valid else 'FAILED'}")

        if not valid:
            print(f"Application validation failed for ApplicationSet {appset_name}")

    # Final summary
    print("\n=== Final Summary ===")
    print(f"Total ApplicationSets processed: {len(appset_files)}")
    print(f"Total applications generated: {len(all_apps)}")

    if not all_apps:
        print("ERROR: No applications were generated from any ApplicationSet")
        sys.exit(1)

if __name__ == "__main__":
    main()
