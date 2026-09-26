#!/bin/bash
# Fix stale nvargus-daemon state and restart the camera pipeline.
# Run directly on the Jetson when CaptureSession fails to create.

set -e

echo "==> Restarting nvargus-daemon..."
sudo systemctl restart nvargus-daemon
sleep 3

echo "==> Restarting jetson-infer..."
sudo systemctl restart jetson-infer
sleep 5

echo "==> Log tail (last 5 lines):"
journalctl -u jetson-infer -n 5 --no-pager

echo ""
echo "==> Updating sudoers so nvargus-daemon can be restarted over SSH..."
echo 'james ALL=(ALL) NOPASSWD: /bin/systemctl restart jetson-infer, /bin/systemctl restart smollama, /bin/systemctl restart nvargus-daemon' \
    | sudo tee /etc/sudoers.d/smollama > /dev/null
echo "    Done. SSH restarts will work from now on."
