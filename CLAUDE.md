# CLAUDE.md

GitOps repository for the home lab, rendered by
[ATLAS](https://github.com/max06/atlas) and delivered by Argo CD.

**Use the `atlas:consumer-repo` skill** for anything that touches `apps/` or
`clusters/`. It covers the deployment model, the `bin/atlas` commands, how to
make and review a change, the Go-template pitfalls and the secret rules. This
file only adds what is specific to this repository.
- The skill comes from the `atlas` plugin, enabled in `.claude/settings.json`.
- If it isn't loaded, install it once per clone with `claude plugin install atlas@atlas --scope project`.
- Or read it directly: https://github.com/max06/atlas/blob/main/contrib/claude-plugin/skills/consumer-repo/SKILL.md

## Layout

- `atlas.yaml`: the engine pin (`spec.engine`, also the `bin/atlas` image). App templates live in `apps/`, the cluster tree in `clusters/`.
- `clusters/global.values.sops.yaml`: `primary_domain`, the only global value. Encrypted.
- `clusters/cyrannus/` is the group of both clusters:
  - `helios`: management cluster. Argo CD (namespace `argocd`) runs here and deploys to every cluster, helios included.
  - `picon`: workloads. Kestra, Radiance (GPU node `srv-gpu01`), Home Assistant, Pi-hole, traefik, and more.
- `apps/<name>/helmfile.yaml.gotmpl`: one app template per directory.
  - Plain manifests go through the `bedag/raw` chart, with the manifests in a `.gotmpl` next to it. See `apps/radiance` or `apps/kestra`.
- `apps/atlas/appSet.yaml.gotmpl`: the Argo CD ApplicationSet. One Application per (cluster, deployment), named `<cluster>-<deployment>`.

## Conventions

- **`deployment.yaml` `settings`**, read by the ApplicationSet:
  - `branch`: the target revision, `main` everywhere.
  - `autoSync`: `false` everywhere. After a merge the user syncs by hand.
  - `namespace`: the destination namespace; Argo CD creates it.
  - `ignoreDifferences`: passed to the Application as is.
- **Ingress is internal only** unless the user decides otherwise. An internal ingress has:
  - the annotation `traefik.ingress.kubernetes.io/router.entrypoints: webinternal`, the LAN-only entrypoint;
  - the annotation `external-dns.alpha.kubernetes.io/target: 192.168.30.241`, traefik's internal LoadBalancer;
  - the host `<app>.{{ .Values.primary_domain }}`.

  **Never write the domain** in a plain file, comment, commit message or PR.
- **Secrets** go in the deployment's `values.sops.yaml` (age + PGP, see `.sops.yaml`).
  - The user fills in values with `sops edit`. Name the key path and leave the value to them.
  - Flow secrets for Kestra: `kestra.secrets.<NAME>` in `clusters/cyrannus/picon/apps/kestra/values.sops.yaml`.
- **Kestra flows** live in `apps/kestra/flows/<sub>/` as plain YAML, not helmfile, so Pebble's `{{ }}` is safe there.
  - Kestra syncs them itself from `main` every 5 minutes (`homelab.system.sync_flows_from_git`). Argo CD is not involved.
- **Commits and PRs:**
  - Commit subject `<app>: <what changed>`, for example `radiance: add Ling-3.0-tiny for the mail classifier`.
  - One concern per PR, against `main`.
  - CI runs the ATLAS review (`atlas-review.yml`) and chart tests.
- **Before opening a PR:** run `bin/atlas render -c <cluster> -d <deployment>`, then `git fetch && bin/atlas review diff`.

## Cluster access (read-only)

On the user's workstation, `switcher <cluster>` prints a temporary kubeconfig for that cluster, connected through Rancher:

```bash
cfg=$(switcher picon | sed -E 's/^__ //; s/,.*$//')   # output: "__ <path>,<cluster>"
kubectl --kubeconfig "$cfg" -n radiance get pods
```

- `kubectl` may not be installed; use `nix run nixpkgs#kubectl --`. The devcontainer has it, but not `switcher`.
- **Argo CD state:** the Applications on helios, namespace `argocd`, for example `kubectl -n argocd get applications.argoproj.io picon-kestra`. There is no `argocd` CLI on the workstation.

These kubeconfigs carry **full admin rights**. Use them read-only:
- `get`, `describe`, `logs`, `events`, `top`, `auth can-i` only.
- No `apply`, `edit`, `patch`, `delete`, `scale`, `rollout restart`, `exec`, `port-forward`, and no Argo CD sync or refresh. Changes go through a PR. Sync and anything else that changes the cluster are the user's call.
- **Never read Secrets:** no `get secret`, no `-o yaml` on them, nothing that prints their data. ConfigMaps, pods, events and logs are fine.

## Tooling notes

- On the workstation, some tools are not on `PATH`: `python3`, `jq`, `yq`, `kubectl`. Use `nix run nixpkgs#<tool> --` (`yq` is `nixpkgs#yq-go`), or `perl`.
- `bin/atlas` needs Docker; the first run pulls the image.
