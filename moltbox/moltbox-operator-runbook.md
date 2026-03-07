# moltbox-operator-runbook.md

This checklist takes a fresh Ubuntu machine to the first OpenClaw chat in Moltbox.

## Architecture Rule

Moltbox is an installer plus template repository.

Repository:

```text
~/git/remram-gateway
```

Live runtime configuration and state:

```text
~/.openclaw
```

Containers must read runtime configuration from `~/.openclaw`. The repository must remain stateless templates and scripts.
Containers must never read live runtime configuration from `~/git/remram-gateway`.

## 0. Prerequisite: NVIDIA Driver Health

Moltbox requires a working NVIDIA driver on the host before deployment.

```bash
nvidia-smi
```

`nvidia-smi` must succeed. If it fails, fix NVIDIA driver installation before continuing.

## 1. Verify Ubuntu

```bash
cat /etc/os-release
```

`scripts/10-install.sh` requires Ubuntu.

## 2. Add the Non-Interactive Shell Guard

Before using VS Code Remote-SSH or other remote tooling, ensure `~/.bashrc` does not print output during non-interactive shells.

Add this block at the top of `~/.bashrc`:

```bash
# Prevent non-interactive shells from emitting output (breaks VSCode Remote-SSH)
case $- in
    *i*) ;;
      *) return;;
esac
```

This prevents Remote-SSH handshake failures caused by shell startup output.

## 3. Install Git and Clone the Repository

```bash
sudo apt-get update
sudo apt-get install -y git
cd ~
mkdir -p ~/git
git clone https://github.com/Remram-AI/remram-gateway.git ~/git/remram-gateway
cd ~/git/remram-gateway/moltbox
```

## 4. Run the Host Installer

```bash
bash ./scripts/10-install.sh
```

`scripts/10-install.sh` installs or validates:

- Docker Engine
- Docker Compose plugin
- `curl`
- NVIDIA Container Toolkit
- host GPU readiness via `nvidia-smi`
- Docker daemon availability
- `vm.max_map_count=262144` for OpenSearch
- `~/git` workspace existence for repository checkout hygiene

If Docker commands fail without `sudo` after this step, either log out and back in or run `newgrp docker`.

## 5. Confirm GPU Driver Visibility

```bash
nvidia-smi
```

`nvidia-smi` must succeed before continuing because the Ollama service is configured with `gpus: all`.

## 6. Build the Required Local OpenClaw Image

```bash
cd ~
git clone https://github.com/openclaw/openclaw.git
cd ~/openclaw
sudo docker build -t openclaw:local .
```

This creates the image name Moltbox expects by default: `OPENCLAW_IMAGE=openclaw:local`.

## 7. Provide the Together API Key

The local routing model is Ollama, but Moltbox still requires a Together API key for the configured cloud fallback and provider probe.

Bootstrap now handles this in one of two ways:

- interactive run: if `~/.openclaw/container.env` has an empty `TOGETHER_API_KEY`, `scripts/20-bootstrap.sh` prompts once and saves the key to `~/.openclaw/container.env`
- non-interactive run: set `MOLTBOX_TOGETHER_API_KEY` before running bootstrap

Interactive example:

```bash
cd ~/git/remram-gateway/moltbox
bash ./scripts/20-bootstrap.sh
```

Non-interactive example:

```bash
cd ~/git/remram-gateway/moltbox
export MOLTBOX_TOGETHER_API_KEY='YOUR_TOGETHER_KEY'
bash ./scripts/20-bootstrap.sh
unset MOLTBOX_TOGETHER_API_KEY
```

Manual fallback if needed:

```bash
sed -i "s|^TOGETHER_API_KEY=.*$|TOGETHER_API_KEY=YOUR_TOGETHER_KEY|" ~/.openclaw/container.env
```

The key is runtime state and must live only in `~/.openclaw/container.env`, not in the repository.

## 8. Bootstrap the Moltbox Runtime

```bash
cd ~/git/remram-gateway/moltbox
bash ./scripts/20-bootstrap.sh
```

Bootstrap performs the following:

- creates `~/.openclaw`
- creates `~/.openclaw/agents/main/agent`
- copies repository templates into `~/.openclaw` only when the runtime files are missing
- preserves existing runtime files on subsequent runs
- prompts for `TOGETHER_API_KEY` if needed and saves it to `~/.openclaw/container.env`
- ensures `OPENSEARCH_JAVA_OPTS="-Xms2g -Xmx2g"` exists in `~/.openclaw/container.env`
- detects the host LAN IP and hostname and writes required `gateway.controlUi.allowedOrigins` to `~/.openclaw/openclaw.json`
- starts the container stack
- pre-pulls the local routing model
- configures the OpenClaw Ollama provider and default/fallback models before gateway readiness checks
- verifies OpenClaw can reach `http://ollama:11434/api/tags`
- primes the Ollama model registry with `openclaw models list --all --provider ollama`
- waits for gateway readiness

If bootstrap times out on a slower machine, rerun with longer waits:

```bash
BOOTSTRAP_OLLAMA_WAIT_SECONDS=180 BOOTSTRAP_GATEWAY_WAIT_SECONDS=180 bash ./scripts/20-bootstrap.sh
```

## 9. Validate the Stack

```bash
bash ./scripts/30-validate.sh
```

`scripts/30-validate.sh` verifies:

- container health
- gateway endpoints
- OpenClaw to Ollama connectivity
- OpenClaw to OpenSearch connectivity
- internal-only port exposure policy

Signal integration is optional for this checklist and does not block first web chat.

## 10. Collect a Diagnostics Bundle Before Troubleshooting

When the appliance is unhealthy but still has enough state to inspect, collect the official debug bundle before making manual changes:

```bash
bash ./scripts/99-diagnostics.sh
```

This writes a timestamped archive to `/tmp/moltbox-debug-YYYYMMDD-HHMMSS.tar.gz` and prints an `scp` command for downloading it to your workstation.

Attach that archive to the bug report or troubleshooting thread before applying a runtime reset.

## 11. Manual Compose Context

For direct `docker compose` commands, export the runtime root and run from the compose directory:

```bash
export MOLTBOX_RUNTIME_ROOT="$HOME/.openclaw"
cd ~/git/remram-gateway/moltbox/config
```

## 12. Post-Install Validation Commands

Check container state:

```bash
docker compose ps
```

Check OpenClaw configuration:

```bash
docker exec moltbox-openclaw openclaw doctor
```

Check gateway endpoints:

```bash
curl http://127.0.0.1:18789/healthz
curl http://127.0.0.1:18789/readyz
```

Confirm runtime provider configuration:

```bash
grep '^TOGETHER_API_KEY=' ~/.openclaw/container.env
docker exec moltbox-openclaw openclaw config get models.providers.ollama.baseUrl
docker exec moltbox-openclaw openclaw config get agents.defaults.model.primary
docker exec moltbox-openclaw openclaw config get agents.defaults.model.fallbacks[0]
docker exec moltbox-openclaw openclaw models list --all --provider ollama
docker exec moltbox-openclaw openclaw models status
```

## 13. Verify the Gateway Token

Read the token from the runtime env file:

```bash
grep '^OPENCLAW_GATEWAY_TOKEN=' ~/.openclaw/.env | cut -d= -f2-
```

Verify the running container sees the same token:

```bash
docker exec -it moltbox-openclaw env | grep OPENCLAW_GATEWAY_TOKEN
```

If you change `~/.openclaw/.env`, restart the stack:

```bash
docker compose restart
```

If the container still reports the previous token after restart, rerun:

```bash
cd ~/git/remram-gateway/moltbox
bash ./scripts/20-bootstrap.sh
```

This forces the stack to reconcile against the current runtime files in `~/.openclaw`.

## 14. Reset Runtime State Without Reinstalling

If runtime configuration, agent state, or session data become corrupted, use the runtime reset tool instead of reinstalling the host:

```bash
cd ~/git/remram-gateway/moltbox
bash ./scripts/12-runtime-reset.sh
```

`scripts/12-runtime-reset.sh`:

- stops Moltbox containers if they exist
- removes those containers if they exist
- clears the contents of `~/.openclaw`
- preserves Docker volumes, networks, images, and repository files
- removes old `/tmp/moltbox-debug-*.tar.gz` bundles

After reset, bring the appliance back with:

```bash
bash ./scripts/20-bootstrap.sh
bash ./scripts/30-validate.sh
```

## 15. Open the Gateway and Send the First Chat

Get the host LAN IP:

```bash
hostname -I
```

Use the LAN IP, not loopback (`127.0.0.1`) or Docker bridge addresses.

Open OpenClaw:

- On the Moltbox machine: `http://127.0.0.1:18789`
- From another LAN device: `http://<MOLTBOX_LAN_IP>:18789`
- From another LAN device by hostname when local DNS works: `http://<MOLTBOX_HOSTNAME>:18789`

When prompted, enter the token from `~/.openclaw/.env` and send the first message.

## 16. Remote Development Workflow

Recommended tooling:

- VS Code
- Remote-SSH extension

Example SSH config:

```sshconfig
Host moltbox
    HostName <MOLTBOX_IP>
    User jpekovitch
```

Recommended folder to open:

```text
/home/jpekovitch/git/remram-gateway
```

For live runtime edits, also open:

```text
/home/jpekovitch/.openclaw
```

This avoids SCP and lets you edit:

- `~/.openclaw/.env`
- `~/.openclaw/container.env`
- `~/.openclaw/openclaw.json`
- `~/.openclaw/model-runtime.yml`
- `~/git/remram-gateway/moltbox/config/docker-compose.yml`
- repository templates under `~/git/remram-gateway/moltbox/`
