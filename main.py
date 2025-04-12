#!/usr/bin/env python3
from constructs import Construct
from cdk8s import App, Chart, ApiObjectMetadata, YamlOutputType
import os
import yaml
import sys
import json
import logging
from typing import Annotated, Dict, List, Literal, Optional, Any, Sequence, Tuple
from imports.io import argoproj
from pydantic import DirectoryPath, Field, HttpUrl, Strict, UrlConstraints, field_serializer
from pydantic_settings import BaseSettings, SettingsConfigDict

# Define application types
APP_TYPE_PLAIN = "plain"
APP_TYPE_LOCAL_HELM = "local-helm"
APP_TYPE_REMOTE_HELM = "remote-helm"
APP_TYPE_LOCAL_CDK8S = "local-cdk8s"

# Define instance types
APP_INSTANCE_PLAIN = "plain"
APP_INSTANCE_CDK8S = "cdk8s"

# Set up logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    validate environment variables
    """
    model_config = SettingsConfigDict(extra='ignore')

    clusterName: Annotated[str, Field(alias="CLUSTER_NAME"), Strict()]
    clusterGroup: Annotated[str, Field(alias="CLUSTER_GROUP", default="")]
    appInstanceName: Annotated[str, Field(alias="APP_INSTANCE_NAME")]
    appInstancePath: Annotated[DirectoryPath, Field(alias="APP_INSTANCE_PATH")]

    # Set additional variables with defaults
    operationMode: Annotated[Literal["meta", "full"], Field(
        alias="OPERATION_MODE", default="meta")]
    repoUrl: Annotated[
        HttpUrl,
        UrlConstraints(
            allowed_schemes=["https"],
            host_required=True,
        ),
        Field(alias="REPO_URL", default="https://github.com/max06/deployments")
    ]

    @property
    def cluster(self) -> str:
        if self.clusterGroup:
            return f"{self.clusterGroup}/{self.clusterName}"
        return self.clusterName

    @field_serializer("repoUrl")
    def serialize_url(self, repo_url: HttpUrl, _info):
        return str(repo_url)


settings = Settings()  # type: ignore


class AppInstanceSettings(BaseSettings):
    """
    Settings specific to an app instance, loaded from the instance directory
    """
    model_config = SettingsConfigDict(extra='allow')

    # Optional settings with defaults
    name: str = settings.appInstanceName
    namespace: str = settings.appInstanceName
    project: str = "default"
    repoUrl: HttpUrl = settings.repoUrl
    path: str = f"clusters/{settings.cluster}/{settings.appInstanceName}"

    @classmethod
    def load(cls) -> "AppInstanceSettings":
        """Load settings from .cdk8s.yaml if it exists"""
        config_path = os.path.join(settings.appInstancePath, ".cdk8s.yaml")
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                yaml_config = yaml.safe_load(f)
                return cls(**yaml_config)
        return cls()

    @field_serializer("repoUrl")
    def serialize_url(self, repo_url: HttpUrl, _info):
        return str(repo_url)


class ArgoApplicationChart(Chart):
    def __init__(self, scope: Construct, id: str, disable_resource_name_hashes: bool = False):
        super().__init__(scope, id, disable_resource_name_hashes=disable_resource_name_hashes)

        # Load instance-specific settings
        self.instance_settings = AppInstanceSettings.load()

        name = f"{settings.clusterName}-{self.instance_settings.name}"
        argo_namespace = "argocd"
        app_namespace = self.instance_settings.namespace
        project = self.instance_settings.project
        sources: List[argoproj.ApplicationSpecSources] = []

        logger.info(
            f"Processing app: {settings.appInstanceName} in {settings.cluster}")

        # Determine the app instance type
        app_instance_type = self._determine_app_instance_type()

        if app_instance_type == APP_INSTANCE_PLAIN:
            logger.info(
                f"App instance type: {app_instance_type}, adding source to manifest")
            sources.append(argoproj.ApplicationSpecSources(
                repo_url=str(settings.repoUrl),
                path=f"clusters/{settings.cluster}/{settings.appInstanceName}",
            ))
        else:
            raise ValueError(f"Unknown app instance type: {app_instance_type}")

        logger.info(
            f"Mode: {settings.operationMode}, generating...")

        argoproj.Application(
            self,
            id=name,
            metadata=ApiObjectMetadata(
                namespace=argo_namespace,
                name=name,
            ),
            spec=argoproj.ApplicationSpec(
                project=project,
                sources=sources,
                destination=argoproj.ApplicationSpecDestination(
                    name=settings.cluster,
                    namespace=app_namespace,
                ),
            ),
        )

        # Generate resources based on operation mode
        if settings.operationMode == "meta":
            # self._generate_meta_resources()
            pass
        elif settings.operationMode == "full":
            self._generate_full_resources()
        else:
            logger.error(f"Unknown operation mode: {settings.operationMode}")
            sys.exit(1)

    def _read_instance_config(self) -> Tuple[str, Dict[str, Any]]:
        """
        Read instance configuration to determine base app name and other settings
        """
        # Check for CDK8s config file
        cdk8s_config_path = os.path.join(
            settings.appInstancePath, ".cdk8s.yaml")
        if os.path.exists(cdk8s_config_path):
            try:
                with open(cdk8s_config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and isinstance(config, dict):
                        base_app = config.get("baseApp")
                        if base_app:
                            return base_app, config
            except Exception as e:
                logger.error(f"Error reading CDK8s config: {e}")

        # Check for Helm values file that might indicate base app
        values_path = os.path.join(settings.appInstancePath, "values.yaml")
        if os.path.exists(values_path):
            try:
                with open(values_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and isinstance(config, dict):
                        # Some Helm values files might have metadata about the base app
                        base_app = config.get("baseApp") or config.get(
                            "app") or config.get("chart")
                        if base_app:
                            return base_app, config
            except Exception as e:
                logger.error(f"Error reading values.yaml: {e}")

        # Check for kustomization.yaml
        kustomization_path = os.path.join(
            settings.appInstancePath, "kustomization.yaml")
        if os.path.exists(kustomization_path):
            try:
                with open(kustomization_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and isinstance(config, dict):
                        # Extract base app from resources or bases
                        resources = config.get("resources", [])
                        bases = config.get("bases", [])
                        all_refs = resources + bases
                        for ref in all_refs:
                            # Look for references to the apps directory
                            if "apps/" in ref:
                                parts = ref.split("/")
                                app_index = parts.index("apps")
                                if len(parts) > app_index + 1:
                                    return parts[app_index + 1], config
            except Exception as e:
                logger.error(f"Error reading kustomization.yaml: {e}")

        # If no config found, use instance name as a fallback
        logger.warning(
            f"No configuration found for {settings.appInstanceName}, using instance name as base app")
        return settings.appInstanceName, {}

    def _determine_app_type(self) -> str:
        """
        Determine the application type by inspecting the base app directory or config
        """
        # First, check if app type is specified in the config
        app_type = self.app_config.get(
            "appType") or self.app_config.get("type")
        if app_type:
            if app_type.lower() in ["helm", "helm-chart", "helmchart"]:
                # Check if it's local or remote
                if self.app_config.get("helmRepo") or self.app_config.get("chartRepo"):
                    return APP_TYPE_REMOTE_HELM
                return APP_TYPE_LOCAL_HELM

            if app_type.lower() in ["cdk8s", "cdk"]:
                return APP_TYPE_LOCAL_CDK8S

            if app_type.lower() in ["plain", "kubernetes", "k8s"]:
                return APP_TYPE_PLAIN

        # If not specified in config, try to infer from base app directory structure
        base_app_path = f"apps/{self.base_app_name}"

        # Check for Chart.yaml to identify Helm chart
        if os.path.exists(os.path.join(base_app_path, "Chart.yaml")):
            return APP_TYPE_LOCAL_HELM

        # Check for cdk8s.yaml to identify CDK8s app
        if os.path.exists(os.path.join(base_app_path, "cdk8s.yaml")):
            return APP_TYPE_LOCAL_CDK8S

        # Check for a charts directory in the repo
        chart_path = f"charts/{self.base_app_name}"
        if os.path.exists(chart_path):
            return APP_TYPE_LOCAL_HELM

        # Default to plain app
        return APP_TYPE_PLAIN

    def _generate_meta_resources(self):
        """
        Generate ArgoCD Application manifest for Meta mode using CDK8s constructs
        """
        # Create metadata
        metadata = ApiObjectMetadata(
            namespace="argocd",
        )

        # Create the ArgoCD Application using ApiObject
        argoproj.Application(
            self,
            self.app_instance.name,
            metadata=metadata,
            spec=argoproj.ApplicationSpec(
                project="default",
                sources=self.app_instance.sources,
                destination=argoproj.ApplicationSpecDestination(
                    name=f"{settings.clusterGroup}/{settings.clusterName}",
                    namespace="default",
                ),
                sync_policy=argoproj.ApplicationSpecSyncPolicy(
                    sync_options=[
                        "CreateNamespace=true"
                    ]
                )
            )
        )

    def _generate_sync_policy(self) -> Dict[str, Any]:
        """
        Generate sync policy for the ArgoCD Application
        """
        sync_policy = {
            "automated": {
                "prune": self.app_config.get("prune", True),
                "selfHeal": self.app_config.get("selfHeal", True),
            },
            "syncOptions": [
                "CreateNamespace=true"
            ]
        }

        # Add retry options if specified
        retry = self.app_config.get("retry")
        if retry:
            sync_policy["retry"] = retry

        # Add additional sync options if specified
        additional_sync_options = self.app_config.get("syncOptions", [])
        for option in additional_sync_options:
            if option not in sync_policy["syncOptions"]:
                sync_policy["syncOptions"].append(option)

        return sync_policy

    def _generate_sources(self) -> List[Dict[str, Any]]:
        """
        Generate sources for the ArgoCD Application based on app type
        """
        sources = []

        # Base repository source
        base_source = {
            # settings.repoUrl),
            "repoURL": self.app_config.get("repoURL", "foo"),
            "targetRevision": self.app_config.get("targetRevision", "HEAD"),
        }

        # Add sources based on app type
        if self.app_type == APP_TYPE_PLAIN:
            # Plain app - point to the base app directory
            base_source["path"] = f"apps/{self.base_app_name}"
            sources.append(base_source)

            # Add instance-specific overlay if it exists
            overlay_path = os.path.join(settings.appInstancePath, "overlays")
            kustomization_path = os.path.join(
                settings.appInstancePath, "kustomization.yaml")
            if os.path.exists(overlay_path) or os.path.exists(kustomization_path):
                instance_source = base_source.copy()
                instance_source["path"] = settings.appInstancePath
                sources.append(instance_source)

        elif self.app_type == APP_TYPE_LOCAL_HELM:
            # Local helm app - point to the chart directory
            chart_path = self.app_config.get(
                "chartPath", f"charts/{self.base_app_name}")
            base_source["path"] = chart_path

            # Add Helm configuration
            helm_config = {"valueFiles": []}

            # Check for values files
            values_path = os.path.join(settings.appInstancePath, "values.yaml")
            if os.path.exists(values_path):
                helm_config["valueFiles"].append(
                    f"{settings.appInstancePath}/values.yaml")

            # Add additional value files from config
            additional_values = self.app_config.get("valueFiles", [])
            for value_file in additional_values:
                if value_file not in helm_config["valueFiles"]:
                    helm_config["valueFiles"].append(value_file)

            # Add Helm parameters if specified
            parameters = self.app_config.get("parameters", [])
            if parameters:
                helm_config["parameters"] = parameters

            # Add values if specified inline
            values = self.app_config.get("values")
            if values:
                helm_config["values"] = json.dumps(values)

            # Add release name if specified
            release_name = self.app_config.get("releaseName")
            if release_name:
                helm_config["releaseName"] = release_name

            # Add Helm configuration to source
            base_source["helm"] = helm_config
            sources.append(base_source)

        elif self.app_type == APP_TYPE_REMOTE_HELM:
            # Remote helm app - use the Helm repo URL from config
            helm_repo = self.app_config.get(
                "helmRepo") or self.app_config.get("chartRepo")
            if not helm_repo:
                logger.error(
                    f"Remote Helm chart specified but no repo URL provided for {settings.appInstanceName}")
                helm_repo = "https://charts.example.com"  # Fallback

            chart_name = self.app_config.get("chartName", self.base_app_name)
            chart_version = self.app_config.get("chartVersion")

            # Configure the Helm source
            base_source["repoURL"] = helm_repo
            base_source["chart"] = chart_name
            if chart_version:
                base_source["targetRevision"] = chart_version

            # Configure Helm values
            helm_config = {"valueFiles": []}

            # Check for values files
            values_path = os.path.join(settings.appInstancePath, "values.yaml")
            if os.path.exists(values_path):
                helm_config["valueFiles"].append(
                    f"{settings.appInstancePath}/values.yaml")

            # Add additional value files from config
            additional_values = self.app_config.get("valueFiles", [])
            for value_file in additional_values:
                if value_file not in helm_config["valueFiles"]:
                    helm_config["valueFiles"].append(value_file)

            # Add Helm parameters if specified
            parameters = self.app_config.get("parameters", [])
            if parameters:
                helm_config["parameters"] = parameters

            # Add values if specified inline
            values = self.app_config.get("values")
            if values:
                helm_config["values"] = json.dumps(values)

            # Add Helm configuration to source
            base_source["helm"] = helm_config
            sources.append(base_source)

        elif self.app_type == APP_TYPE_LOCAL_CDK8S:
            # Local CDK8s app - point to the base app directory
            base_source["path"] = f"apps/{self.base_app_name}"

            # Configure plugin
            plugin_config = {
                "name": "cdk8s",
                "env": [
                    # Use full mode for the base app
                    {"name": "OPERATION_MODE", "value": "full"},
                ]
            }

            # Add configuration path if .cdk8s.yaml exists
            cdk8s_config_path = os.path.join(
                settings.appInstancePath, ".cdk8s.yaml")
            if os.path.exists(cdk8s_config_path):
                plugin_config["env"].append(
                    {"name": "CONFIG_PATH", "value": f"{settings.appInstancePath}/.cdk8s.yaml"})

            # Add cluster context to plugin environment
            plugin_config["env"].extend([
                {"name": "CLUSTER_NAME", "value": settings.clusterName},
                {"name": "CLUSTER_GROUP", "value": settings.clusterGroup},
                {"name": "INSTANCE_NAME", "value": settings.appInstanceName},
                {"name": "INSTANCE_PATH", "value": settings.appInstancePath}
            ])

            # Add additional environment variables from config
            env_vars = self.app_config.get("env", [])
            for env_var in env_vars:
                if isinstance(env_var, dict) and "name" in env_var and "value" in env_var:
                    plugin_config["env"].append(env_var)

            # Add plugin configuration to source
            base_source["plugin"] = plugin_config
            sources.append(base_source)

        return sources

    def _generate_full_resources(self):
        """
        Generate full Kubernetes manifests (placeholder for Full mode)
        """
        logger.info("Full mode is not implemented yet")
        # This is a placeholder as requested in the instructions
        # Full implementation would generate actual Kubernetes manifests

    def _determine_app_instance_type(self) -> str:
        """
        Determine if this is a plain or CDK8s instance
        """
        # Check for CDK8s config file
        if os.path.exists(os.path.join(settings.appInstancePath, ".cdk8s.yaml")):
            return APP_INSTANCE_CDK8S
        return APP_INSTANCE_PLAIN


def main():
    # Read environment variables
    logger.info("Settings: %s", settings.model_dump())

    # Create CDK8s app
    app = App(yaml_output_type=YamlOutputType.FILE_PER_RESOURCE)
    ArgoApplicationChart(app, "argoApplication",
                         disable_resource_name_hashes=True)
    app.synth()


if __name__ == "__main__":
    main()
