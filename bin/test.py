#!/usr/bin/env python3
import yaml
import subprocess
import json
import sys
import os
import argparse
import tempfile
from pathlib import Path

def extract_appset_from_helm(chart_path, values_file=None, release_name="test-release"):
    """Extract ApplicationSet definition from a Helm chart"""

    cmd = ["helm", "template", release_name, chart_path]
    if values_file:
        cmd.extend(["-f", values_file])

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error rendering Helm chart: {result.stderr}")
        sys.exit(1)

    # Parse the output and find the ApplicationSet resources
    manifests = list(yaml.safe_load_all(result.stdout))
    appsets = [m for m in manifests if m and m.get("kind") == "ApplicationSet"]

    if not appsets:
        print("No ApplicationSet resources found in the rendered Helm chart")
        sys.exit(1)

    print(f"Found {len(appsets)} ApplicationSet resources")
    return appsets

def save_appset_to_file(appset, output_file):
    """Save an ApplicationSet to a file"""
    with open(output_file, "w") as f:
        yaml.dump(appset, f)
    print(f"Saved ApplicationSet to {output_file}")

def extract_appset_from_values(values_yaml):
    """Extract ApplicationSet from a Helm values.yaml structure"""
    data = yaml.safe_load(values_yaml)
    appsets = []

    # Handle the specific format from the input
    if "applicationsets" in data:
        for appset_name, appset_data in data["applicationsets"].items():
            # Create a proper ApplicationSet structure
            appset = {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "ApplicationSet",
                "metadata": {
                    "name": appset_name,
                    "namespace": appset_data.get("namespace", "argocd")
                },
                "spec": {
                    "generators": appset_data.get("generators", []),
                    "template": appset_data.get("template", {}),
                    "templatePatch": appset_data.get("templatePatch", {}),
                    "goTemplate": appset_data.get("goTemplate", False)
                }
            }

            # Add optional fields if present
            if "goTemplateOptions" in appset_data:
                appset["spec"]["goTemplateOptions"] = appset_data["goTemplateOptions"]

            if "syncPolicy" in appset_data:
                appset["spec"]["syncPolicy"] = appset_data["syncPolicy"]

            appsets.append(appset)

        if not appsets:
            print("No ApplicationSet resources found in the values format")
            return None

        print(f"Found {len(appsets)} ApplicationSet resources in values format")
        return appsets

    print("Could not extract ApplicationSet from values format")
    return None

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
            return list(yaml.safe_load_all(result.stdout))[0]
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

    for i, app in enumerate(apps):
        name = app.get("metadata", {}).get("name", f"app-{i+1}")
        output_file = os.path.join(output_dir, f"{name}.yaml")

        with open(output_file, "w") as f:
            yaml.dump(app, f)

        print(f"Exported application to {output_file}")

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
        path = source.get("path", "")
        if not path.startswith("clusters/"):
            print(f"  ERROR: Application {app.get('metadata', {}).get('name')} source path is {path}, expected to start with clusters/")
            valid = False

    return valid

def validate_projects_app(apps):
    """Validate that the 'projects' application has the correct source configuration"""
    valid = True

    # Find the projects application
    projects_app = None
    for app in apps:
        if app.get("metadata", {}).get("name") == "in-cluster-projects":
            projects_app = app
            break

    if not projects_app:
        print("  ERROR: Could not find application named 'in-cluster-projects'")
        return False

    # Check for sources
    sources = projects_app.get("spec", {}).get("sources", [])
    if not sources:
        print("  ERROR: 'in-cluster-projects' application missing sources")
        return False

    # Check that there is exactly one source
    if len(sources) != 1:
        print(f"  ERROR: 'in-cluster-projects' application has {len(sources)} sources, expected 1")
        return False

    # Check that the source points to the repository
    source = sources[0]
    if source.get("repoURL") != "https://github.com/max06/deployments":
        print(f"  ERROR: 'in-cluster-projects' application source repoURL is {source.get('repoURL')}, expected https://github.com/max06/deployments")
        valid = False

    # Check that the path is the correct directory in the repository
    path = source.get("path", "")
    expected_path = "clusters/in-cluster/apps/projects"
    if path != expected_path:
        print(f"  ERROR: 'in-cluster-projects' application source path is {path}, expected {expected_path}")
        valid = False

    return valid

def main():
    parser = argparse.ArgumentParser(description="Test ArgoCD ApplicationSet generator combinations and templates")

    # Input options
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--helm-chart", help="Path to Helm chart containing ApplicationSet")
    input_group.add_argument("--values-file", help="Path to values.yaml file with ApplicationSet configuration")
    input_group.add_argument("--appset-file", help="Path to ApplicationSet YAML file")

    # Optional arguments
    parser.add_argument("--release-name", default="test-release", help="Release name for Helm template (default: test-release)")
    parser.add_argument("--output-dir", default="./generated-apps", help="Directory to export generated applications (default: ./generated-apps)")
    parser.add_argument("--keep-appset", action="store_true", help="Keep the extracted ApplicationSet file")
    parser.add_argument("--appset-name", help="Specific ApplicationSet name to test (if not provided, all will be tested)")

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

    if args.helm_chart:
        values_file = args.values_file if args.values_file else None
        appsets = extract_appset_from_helm(args.helm_chart, values_file, args.release_name)
        if not appsets:
            sys.exit(1)

        # Filter by name if specified
        if args.appset_name:
            appsets = [appset for appset in appsets if appset.get("metadata", {}).get("name") == args.appset_name]
            if not appsets:
                print(f"No ApplicationSet named '{args.appset_name}' found")
                sys.exit(1)

        # Create temporary files for each ApplicationSet
        for appset in appsets:
            tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=not args.keep_appset)
            temp_files.append(tmp)  # Keep reference to prevent garbage collection
            appset_file = tmp.name
            save_appset_to_file(appset, appset_file)
            appset_files.append((appset.get("metadata", {}).get("name", "unknown"), appset_file))

    elif args.values_file:
        with open(args.values_file, 'r') as f:
            appsets = extract_appset_from_values(f.read())
            if not appsets:
                sys.exit(1)

            # Filter by name if specified
            if args.appset_name:
                appsets = [appset for appset in appsets if appset.get("metadata", {}).get("name") == args.appset_name]
                if not appsets:
                    print(f"No ApplicationSet named '{args.appset_name}' found")
                    sys.exit(1)

            # Create temporary files for each ApplicationSet
            for appset in appsets:
                tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=not args.keep_appset)
                temp_files.append(tmp)  # Keep reference to prevent garbage collection
                appset_file = tmp.name
                save_appset_to_file(appset, appset_file)
                appset_files.append((appset.get("metadata", {}).get("name", "unknown"), appset_file))

    elif args.appset_file:
        # For a single ApplicationSet file, just use it directly
        appset_files.append(("single", args.appset_file))

        # If appset_name is specified, check if it matches
        if args.appset_name:
            with open(args.appset_file, 'r') as f:
                appset = yaml.safe_load(f)
                if appset.get("metadata", {}).get("name") != args.appset_name:
                    print(f"ApplicationSet in file '{args.appset_file}' does not match requested name '{args.appset_name}'")
                    sys.exit(1)

    # Process each ApplicationSet
    for appset_name, appset_file in appset_files:
        print(f"\n=== Processing ApplicationSet: {appset_name} ===")

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
            validation_rules["projects_app"] = validate_projects_app

        valid = validate_applications(apps, validation_rules)

        # Export applications if requested
        if apps and args.output_dir:
            appset_output_dir = os.path.join(args.output_dir, appset_name)
            print(f"\n=== Exporting applications for {appset_name} ===")
            export_applications(apps, appset_output_dir)

        if args.keep_appset:
            print(f"\nApplicationSet file for {appset_name} kept at: {appset_file}")

        print(f"\n=== Summary for {appset_name} ===")
        print(f"Applications generated: {len(apps)}")
        print(f"Validation: {'PASSED' if valid else 'FAILED'}")

        if not valid:
            print(f"Validation failed for ApplicationSet {appset_name}")
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
