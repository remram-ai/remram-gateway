# moltbox-operator-runbook.md

This checklist takes a fresh Ubuntu machine to the first OpenClaw chat in Moltbox.

Prerequisite: Moltbox requires a working NVIDIA driver on the host before deployment.

```bash
nvidia-smi
```

`nvidia-smi` must succeed. If it fails, fix NVIDIA driver installation before continuing.

1. Power on the machine, log in, and verify the OS is Ubuntu.

```bash
cat /etc/os-release
```

`cat /etc/os-release` confirms the host OS; `scripts/10-install.sh` requires Ubuntu.

2. Install Git so you can clone the repositories.

```bash
sudo apt-get update
sudo apt-get install -y git
```

`sudo apt-get update` refreshes package indexes.  
`sudo apt-get install -y git` installs Git non-interactively.

3. Clone the Moltbox repository and enter the Moltbox directory.

```bash
cd ~
git clone https://github.com/Remram-AI/remram-gateway.git
cd ~/remram-gateway/moltbox
```

`cd ~` moves to your home directory.  
`git clone ...` retrieves the project.  
`cd ~/remram-gateway/moltbox` enters the Moltbox profile folder.

4. Run the Moltbox host installer script.

```bash
bash ./scripts/10-install.sh
```

`bash ./scripts/10-install.sh` installs/configures Docker, Docker Compose, `curl`, NVIDIA Container Toolkit, enforces GPU readiness, ensures Docker daemon availability, and sets required OpenSearch kernel settings.

If Docker commands fail without `sudo` after this step, either:
- log out and log back in, or
- run `newgrp docker`

This applies docker-group membership changes from the installer.

5. Confirm GPU driver visibility on the host.

```bash
nvidia-smi
```

`nvidia-smi` must succeed before continuing, because the Ollama service is configured with `gpus: all`.

6. Build the required local OpenClaw image (`openclaw:local`).

```bash
cd ~
git clone https://github.com/openclaw/openclaw.git
cd ~/openclaw
sudo docker build -t openclaw:local .
```

`git clone ...openclaw...` gets the OpenClaw source.  
`sudo docker build -t openclaw:local .` creates the exact image name Moltbox expects by default (`OPENCLAW_IMAGE=openclaw:local`).

7. Bootstrap the Moltbox stack.

```bash
cd ~/remram-gateway/moltbox
bash ./scripts/20-bootstrap.sh
```

`bash ./scripts/20-bootstrap.sh` creates `config/.env` and `config/container.env` (if missing), starts the stack, pre-pulls the local routing model, and waits for gateway readiness.

If bootstrap times out on a slower machine, rerun with longer waits:

```bash
BOOTSTRAP_OLLAMA_WAIT_SECONDS=180 BOOTSTRAP_GATEWAY_WAIT_SECONDS=180 bash ./scripts/20-bootstrap.sh
```

These environment variables are read by the bootstrap script to extend readiness wait windows.

8. Run the Moltbox validation script.

```bash
bash ./scripts/30-validate.sh
```

`bash ./scripts/30-validate.sh` verifies container health, gateway endpoints, internal service connectivity, and internal-only port exposure policy.

Signal integration is optional for this checklist and does not block first web chat.

9. Verify health endpoints directly from the host.

```bash
curl -fsS http://127.0.0.1:18789/healthz
curl -fsS http://127.0.0.1:18789/readyz
```

These commands confirm the OpenClaw gateway is live and ready on the published host port.

10. Get the gateway token and the host LAN IP.

```bash
grep '^OPENCLAW_GATEWAY_TOKEN=' ./config/.env | cut -d= -f2-
hostname -I
```

`grep ...OPENCLAW_GATEWAY_TOKEN... | cut ...` prints only the login token value.  
`hostname -I` may print multiple addresses; use the LAN IP (for example `192.168.x.x`), not loopback (`127.0.0.1`) or Docker bridge addresses.

11. Open OpenClaw and send the first chat message.

In a browser:
- On the Moltbox machine: `http://127.0.0.1:18789`
- From another LAN device: `http://<MOLTBOX_LAN_IP>:18789`

When prompted, enter the token from Step 10, then send your first message (example: `Hello Moltbox`).
