#!/usr/bin/env bash
# One-time setup for the mini PC (Ubuntu Server 24.04 or newer).
# Run from the repo folder:  sudo ./deploy/setup-minipc.sh <your-linux-username>
set -euo pipefail

USER_NAME="${1:?usage: sudo $0 <linux-username>}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Installing Podman, the kiosk compositor and Chromium"
apt-get update
apt-get install -y podman podman-compose cage chromium-browser curl alsa-utils || \
apt-get install -y podman podman-compose cage chromium curl alsa-utils

echo "==> Giving $USER_NAME access to audio and keeping their containers running after logout"
usermod -aG audio,video,render "$USER_NAME"
loginctl enable-linger "$USER_NAME"

echo "==> Installing kiosk service"
CHROME_BIN="$(command -v chromium-browser || command -v chromium)"
USER_ID="$(id -u "$USER_NAME")"
sed -e "s|User=jarvis|User=$USER_NAME|" \
    -e "s|/run/user/1000|/run/user/$USER_ID|" \
    -e "s|-- chromium |-- $CHROME_BIN |" \
    "$REPO_DIR/deploy/jarvis-kiosk.service" > /etc/systemd/system/jarvis-kiosk.service
systemctl daemon-reload
systemctl enable jarvis-kiosk.service
systemctl set-default graphical.target

echo "==> Installing container auto-start (user service)"
mkdir -p "/home/$USER_NAME/.config/systemd/user"
cat > "/home/$USER_NAME/.config/systemd/user/jarvis.service" <<EOF
[Unit]
Description=Jarvis containers
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/podman compose --profile voice up -d
ExecStop=/usr/bin/podman compose --profile voice down

[Install]
WantedBy=default.target
EOF
chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME/.config"
sudo -u "$USER_NAME" XDG_RUNTIME_DIR="/run/user/$USER_ID" systemctl --user daemon-reload
sudo -u "$USER_NAME" XDG_RUNTIME_DIR="/run/user/$USER_ID" systemctl --user enable jarvis.service

echo
echo "Done. Next, as $USER_NAME:"
echo "  cd $REPO_DIR && podman compose --profile voice up -d --build"
echo "Then reboot. The dashboard opens full-screen on its own."
