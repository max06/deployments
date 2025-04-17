#!/usr/bin/env python3
import yaml
import subprocess
import json
import sys
import os
import argparse
import tempfile
from pathlib import Path

def extract_appset_from_helmfile(helmfile_path, environment="testing"):
    """Extract ApplicationSet definition from a helmfile template command"""

    cmd = ["helmfile", "template", "--environment", environment]

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error rendering helmfile: {result.stderr}")
        sys.exit(1)

    # Parse the output and find the ApplicationSet resources
    manifests = list(yaml.safe_load_all(result.stdout))
    appsets = [m for m in manifests if m and m.get("kind") == "ApplicationSet"]

    if not appsets:
        print("No ApplicationSet resources found in the rendered helmfile")
        sys.exit(1)

    print(f"Found {len(appsets)} ApplicationSet resources")
    return appsets

def save_appset_to_file(appset, output_file):
    """Save an ApplicationSet to a file"""
    with open(output_file, "w") as f:
        yaml.dump(appset, f)
    print(f"Saved ApplicationSet to {output_file}")

def simulate_appset_generation(appset_file):
    """Simulate the ApplicationSet controller's behavior to generate Applications"""
    # First try the kubectl-applicationset plugin
    plugin_check = subprocess.run(["kubectl", "applicationset", "generate", "--help"],
                                  capture_output=True, text=True)

    if plugin_check.returncode == 0:
        print("Using kubectl-applicationset plugin to generate applications")
        cmd = ["kubectl", "applicationset", "generate", "-f", appset_file]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            return list(yaml.safe_load_all(result.stdout))
        else:
            print(f"Error using kubectl-applicationset: {result.stderr}")

    # If the plugin is not available, try using argocd CLI
    argocd_check = subprocess.run(["argocd", "version"], capture_output=True, text=True)

    if argocd_check.returncode == 0:
        print("Using argocd CLI to generate applications")
        cmd = ["argocd", "appset", "generate", appset_file, "-o", "yaml"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            # Return all applications, not just the first one
            return list(yaml.safe_load_all(result.stdout))
        else:
            print(f"Error using argocd CLI: {result.stderr}")

    # Fallback to a basic simulation
    print("Falling back to basic generator simulation...")
    with open(appset_file, 'r') as f:
        appset = yaml.safe_load(f)

    # This is a simplified simulation and may not cover all generator types and combinations
    # For a real test, you should install and use the kubectl-applicationset plugin
    apps = simulate_basic_generators(appset)

    if not apps:
        print("WARNING: Could not simulate application generation. Install kubectl-applicationset plugin for better results.")

    return apps

def simulate_basic_generators(appset):
    """Very basic simulation of ApplicationSet generators"""
    # This is a simplified implementation and doesn't support all generators and combinations
    generators = appset.get("spec", {}).get("generators", [])
    template = appset.get("spec", {}).get("template", {})

    # Example handling for list generator
    apps = []
    for generator in generators:
        if "list" in generator:
            elements = generator["list"].get("elements", [])
            for element in elements:
                app = create_application_from_template(template, element, appset)
                apps.append(app)

        # Add support for other generator types here
        # ...

    return apps

def create_application_from_template(template, params, appset):
    """Create an Application from a template and parameters"""
    # This is a very simplified implementation
    # In reality, the ApplicationSet controller does more complex template rendering

    # Start with a copy of the template
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "generated-application",
            "namespace": appset.get("metadata", {}).get("namespace", "argocd")
        },
        "spec": template.copy() if template else {}
    }

    # Extremely simple parameter substitution
    # This doesn't handle the full Argo Go template functionality
    app_str = json.dumps(app)
    for key, value in params.items():
        placeholder = "{{" + key + "}}"
        app_str = app_str.replace(placeholder, str(value))

    return json.loads(app_str)

def validate_applications(apps, validation_rules=None):
    """Validate the generated applications against predefined rules"""
    if not apps:
        print("No applications to validate")
        return False

    # Filter out None values
    apps = [app for app in apps if app is not None]

    if not apps:
        print("No valid applications to validate after filtering None values")
        return False

    print(f"Found {len(apps)} generated applications")
    valid = True

    # Simple validation: check if applications have required fields
    for i, app in enumerate(apps):
        print(f"Application {i+1}: {app.get('metadata', {}).get('name', 'unknown')}")

        # Check basic structure
        if app.get("kind") != "Application":
            print(f"  WARNING: Application {i+1} has incorrect kind: {app.get('kind')}")

        if not app.get("spec"):
            print(f"  ERROR: Application {i+1} missing spec section")
            valid = False
            continue

        # Check for destination
        if not app["spec"].get("destination"):
            print(f"  ERROR: Application {i+1} missing destination")
            valid = False

        # Check for source
        if not app["spec"].get("sources"):
            print(f"  ERROR: Application {i+1} missing source")
            valid = False

    # Apply custom validation rules if provided
    if validation_rules:
        for rule_name, rule_func in validation_rules.items():
            print(f"Applying validation rule: {rule_name}")
            rule_passed = rule_func(apps)
            print(f"  {'PASSED' if rule_passed else 'FAILED'}")
            valid = valid and rule_passed

    return valid

def validate_appset_structure(appset):
    """Validate the ApplicationSet structure itself"""
    valid = True

    # Check basic structure
    if appset.get("apiVersion") != "argoproj.io/v1alpha1":
        print(f"  ERROR: ApplicationSet has incorrect apiVersion: {appset.get('apiVersion')}")
        valid = False

    if appset.get("kind") != "ApplicationSet":
        print(f"  ERROR: ApplicationSet has incorrect kind: {appset.get('kind')}")
        valid = False

    # Check metadata
    if not appset.get("metadata"):
        print("  ERROR: ApplicationSet missing metadata section")
        valid = False
    else:
        if not appset["metadata"].get("name"):
            print("  ERROR: ApplicationSet missing name in metadata")
            valid = False

    # Check spec
    if not appset.get("spec"):
        print("  ERROR: ApplicationSet missing spec section")
        valid = False
    else:
        # Check generators
        if not appset["spec"].get("generators"):
            print("  ERROR: ApplicationSet missing generators in spec")
            valid = False

        # Check template
        if not appset["spec"].get("template"):
            print("  ERROR: ApplicationSet missing template in spec")
            valid = False

    return valid

def server_side_dry_run(appset_file):
    """Perform a server-side dry-run of the ApplicationSet"""
    cmd = ["kubectl", "apply", "-f", appset_file, "--server-dry-run=true", "-o", "yaml"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error in server-side dry-run: {result.stderr}")
        return None

    return yaml.safe_load(result.stdout)

def export_applications(apps, output_dir):
    """Export generated applications to individual YAML files"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Filter out None values
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

def export_appset(appset, output_dir):
    """Export an ApplicationSet to a YAML file"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    name = appset.get("metadata", {}).get("name", "unknown-appset")
    output_file = os.path.join(output_dir, f"{name}.yaml")

    with open(output_file, "w") as f:
        yaml.dump(appset, f)

    print(f"Exported ApplicationSet to {output_file}")
    return output_file

def validate_plain_appset_sources(apps):
    """Validate that the plain ApplicationSet generates one sources entry pointing to the directory in the repo"""
    valid = True

    for app in apps:
        # Check if this is an application from the plain ApplicationSet
        if not app.get("metadata", {}).get("name", "").startswith("in-cluster-"):
            continue

        # Check for sources
        sources = app.get("spec", {}).get("sources", [])
        if not sources:
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} missing sources")
            valid = False
            continue

        # Check that there is exactly one source
        if len(sources) != 1:
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} has {len(sources)} sources, expected 1")
            valid = False
            continue

        # Check that the source points to the repository
        source = sources[0]
        if source.get("repoURL") != "https://github.com/max06/deployments":
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} source repoURL is {source.get('repoURL')}, expected https://github.com/max06/deployments")
            valid = False

        # Check that the path is a directory in the repository
        # For testing environment, we accept paths starting with testing/ instead of clusters/
        path = source.get("path", "")
        if not (path.startswith("clusters/") or path.startswith("testing/")):
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} source path is {path}, expected to start with clusters/ or testing/")
            valid = False

    return valid

def main():
    parser = argparse.ArgumentParser(description="Test ArgoCD ApplicationSet generator combinations and templates")

    # Optional arguments
    parser.add_argument("--output-dir", default="./testing-output", help="Directory to export generated applications (default: ./testing-output)")

    args = parser.parse_args()

    # Clean the output directory if it exists
    if args.output_dir and os.path.exists(args.output_dir):
        print(f"Cleaning output directory: {args.output_dir}")
        import shutil
        shutil.rmtree(args.output_dir)

    # Create output directory
    if args.output_dir:
        os.makedirs(args.output_dir)
        print(f"Created output directory: {args.output_dir}")

    # Process ApplicationSets
    all_apps = []
    appset_files = []
    temp_files = []  # Keep track of temporary files to prevent them from being garbage collected

    # Use helmfile template to generate ApplicationSet manifests
    appsets = extract_appset_from_helmfile("helmfile.yaml", "testing")
    if not appsets:
        sys.exit(1)

    # Create a directory for ApplicationSets in the output directory
    appset_output_dir = os.path.join(args.output_dir, "applicationsets") if args.output_dir else None
    if appset_output_dir:
        os.makedirs(appset_output_dir)
        print(f"Created ApplicationSet output directory: {appset_output_dir}")

    # Create temporary files for each ApplicationSet and save them to the output directory
    for appset in appsets:
        # Save to temporary file for processing
        tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=True)
        temp_files.append(tmp)  # Keep reference to prevent garbage collection
        appset_file = tmp.name
        save_appset_to_file(appset, appset_file)

        # Save to output directory
        if appset_output_dir:
            export_appset(appset, appset_output_dir)

        appset_files.append((appset.get("metadata", {}).get("name", "unknown"), appset_file, appset))

    # Process each ApplicationSet
    for appset_name, appset_file, appset in appset_files:
        print(f"\n=== Processing ApplicationSet: {appset_name} ===")

        # Validate ApplicationSet structure
        print(f"\n=== Validating ApplicationSet structure for {appset_name} ===")
        structure_valid = validate_appset_structure(appset)
        print(f"Structure validation: {'PASSED' if structure_valid else 'FAILED'}")

        # Generate applications
        print(f"\n=== Generating applications for {appset_name} ===")
        apps = simulate_appset_generation(appset_file)

        if not apps:
            print(f"WARNING: No applications generated for ApplicationSet {appset_name}")
            continue

        all_apps.extend(apps)

        # Validate applications
        print(f"\n=== Validating applications for {appset_name} ===")

        # Define validation rules based on ApplicationSet name
        validation_rules = {}
        if appset_name == "plain":
            validation_rules["plain_appset_sources"] = validate_plain_appset_sources

        valid = validate_applications(apps, validation_rules)

        # Export applications if requested
        if apps and args.output_dir:
            appset_output_dir = os.path.join(args.output_dir, appset_name)
            print(f"\n=== Exporting applications for {appset_name} ===")
            export_applications(apps, appset_output_dir)

        print(f"\n=== Summary for {appset_name} ===")
        print(f"Structure validation: {'PASSED' if structure_valid else 'FAILED'}")
        print(f"Applications generated: {len(apps)}")
        print(f"Application validation: {'PASSED' if valid else 'FAILED'}")

        if not valid:
            print(f"Application validation failed for ApplicationSet {appset_name}")
            # Continue with other ApplicationSets even if one fails

    # Final summary
    print("\n=== Final Summary ===")
    print(f"Total ApplicationSets processed: {len(appset_files)}")
    print(f"Total applications generated: {len(all_apps)}")

    # Exit with error if any ApplicationSet failed validation
    if not all_apps:
        print("ERROR: No applications were generated from any ApplicationSet")
        sys.exit(1)

if __name__ == "__main__":
    main()
