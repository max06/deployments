#!/usr/bin/env python3
import subprocess
import concurrent.futures
import sys
from typing import List, Union
from dataclasses import dataclass
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn


@dataclass
class Task:
    name: str
    command: Union[str, List[str]]
    dependencies: List[str] = None

    def __post_init__(self):
        self.dependencies = self.dependencies or []
        if isinstance(self.command, str):
            self.command = [self.command]


class SetupRunner:
    def __init__(self):
        self.console = Console()
        self.tasks = {}
        self.results = {}
        self.progress_tasks = {}

    def add_task(self, name: str, command: Union[str, List[str]], dependencies: List[str] = None):
        self.tasks[name] = Task(name, command, dependencies)

    def run_command(self, task: Task) -> bool:
        for cmd in task.command:
            try:
                subprocess.run(cmd, shell=True, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                self.console.print(
                    f"[red]Error in {task.name} running command '{cmd}':[/red] {e.stderr.decode()}")
                return False
        return True

    def run_tasks(self):
        with Progress(
            SpinnerColumn(finished_text="[green]\N{check mark}[/green]"),
            TextColumn("[progress.description]{task.description}"),
            console=self.console
        ) as progress:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = {}
                pending_tasks = set(self.tasks.keys())

                for task_name in self.tasks:
                    self.progress_tasks[task_name] = progress.add_task(
                        f"Waiting: {task_name}", total=1)

                while pending_tasks:
                    for task_name in list(pending_tasks):
                        task = self.tasks[task_name]
                        deps_completed = all(
                            self.results.get(dep, False) for dep in task.dependencies)

                        if deps_completed and task_name not in futures:
                            progress.update(self.progress_tasks[task_name],
                                            description=f"Running: {task_name}")
                            futures[task_name] = executor.submit(
                                self.run_command, task)

                    if not futures:
                        continue

                    done, _ = concurrent.futures.wait(
                        futures.values(),
                        timeout=0.1,
                        return_when=concurrent.futures.FIRST_COMPLETED
                    )

                    for future in done:
                        completed_task = None
                        for task_name, fut in futures.items():
                            if fut == future:
                                completed_task = task_name
                                break

                        if completed_task:
                            success = future.result()
                            self.results[completed_task] = success
                            pending_tasks.remove(completed_task)
                            progress.update(self.progress_tasks[completed_task],
                                            description=f"{completed_task}", advance=1)
                            del futures[completed_task]
                            if not success:
                                return False

        return all(self.results.values())


def main():
    runner = SetupRunner()

    runner.add_task(
        "install kubeswitch",
        [
            "sudo curl -L -o /usr/local/bin/switcher https://github.com/danielfoehrKn/kubeswitch/releases/download/0.9.2/switcher_linux_amd64",
            "sudo chmod +x /usr/local/bin/switcher"
        ]
    )
    runner.add_task(
        "add kubeswitch to profile",
        [
            "echo 'source <(switcher init bash)' >> ~/.bashrc",
            "echo alias s=\\'switch --config-path switch-config.yaml\\' >> ~/.bashrc",
            "echo 'complete -o default -F __start_switcher s' >> ~/.bashrc",
        ],
        ["install kubeswitch"]
    )
    runner.add_task(
        "install ytt", "sudo eget carvel-dev/ytt --to /usr/local/bin/ytt"
    )
    runner.add_task(
        "install vendir", "sudo eget carvel-dev/vendir --to /usr/local/bin/vendir"
    )
    runner.add_task(
        "install sops", "sudo eget getsops/sops -a '^sbom' --to /usr/local/bin/sops"
    )
    runner.add_task(
        "install age", "sudo eget FiloSottile/age --to /usr/local/bin/age"
    )
    runner.add_task(
        "install helmfile", [
            "sudo eget helmfile/helmfile --to /usr/local/bin/helmfile", "helmfile init --force"]
    )
    runner.add_task(
        "install cdk8s", "npm install -g cdk8s-cli"
    )

    runner.add_task(
        "install krew", '''
            set -x; cd "$(mktemp -d)" &&
            OS="$(uname | tr '[:upper:]' '[:lower:]' )" &&
            ARCH="$(uname -m | sed -e 's/x86_64/amd64/' -e 's/\\(arm\\)\\(64\\)\\?.*/\\1\\2/' -e 's/aarch64$/arm64/')" &&
            KREW="krew-${OS}_${ARCH}" &&
            curl -fsSLO "https://github.com/kubernetes-sigs/krew/releases/latest/download/${KREW}.tar.gz" &&
            tar zxvf "${KREW}.tar.gz" &&
            ./"${KREW}" install krew
        '''
    )

    runner.add_task(
        "install kubevirt cli", "kubectl krew install virt", ["install krew"]
    )

    runner.add_task(
        "install kapitan", "pip3 install kapitan"
    )

    success = runner.run_tasks()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
