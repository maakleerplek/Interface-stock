# Service Quick Reference

## Auto-Start Feature

The InvenTree Shopping System automatically starts when your Raspberry Pi boots up thanks to a systemd service.

## Common Commands

### Service Control
```bash
# Start the service
sudo systemctl start inventree-scanner.service

# Stop the service
sudo systemctl stop inventree-scanner.service

# Restart the service (after code updates)
sudo systemctl restart inventree-scanner.service
```

### Status & Logs
```bash
# Check if service is running
sudo systemctl status inventree-scanner.service

# View recent logs
sudo journalctl -u inventree-scanner.service -n 50

# Follow logs in real-time
sudo journalctl -u inventree-scanner.service -f

# View logs since last boot
sudo journalctl -u inventree-scanner.service -b
```

### Enable/Disable Auto-Start
```bash
# Disable auto-start on boot (but don't stop it now)
sudo systemctl disable inventree-scanner.service

# Stop AND disable
sudo systemctl disable --now inventree-scanner.service

# Re-enable auto-start
sudo systemctl enable inventree-scanner.service

# Enable and start immediately
sudo systemctl enable --now inventree-scanner.service
```

## After Updating Code

When you pull new code from GitHub:

```bash
# Pull latest changes
git pull

# Install any new dependencies
source .venv/bin/activate
pip install -r requirements.txt

# Restart the service to use new code
sudo systemctl restart inventree-scanner.service

# Check it started correctly
sudo systemctl status inventree-scanner.service
```

## Troubleshooting

### Service won't start
```bash
# Check detailed error logs
sudo journalctl -u inventree-scanner.service -n 100 --no-pager

# Check if Python can run the script manually
source .venv/bin/activate
python barcode_inventree.py
```

### Service keeps restarting
```bash
# Watch logs to see what's failing
sudo journalctl -u inventree-scanner.service -f
```

### Need to run manually instead
```bash
# Stop and disable service
sudo systemctl stop inventree-scanner.service
sudo systemctl disable inventree-scanner.service

# Run manually
source .venv/bin/activate
python barcode_inventree.py
```

## Service Configuration

The service file is located at: `/etc/systemd/system/inventree-scanner.service`

After editing the service file, reload systemd:
```bash
sudo systemctl daemon-reload
sudo systemctl restart inventree-scanner.service
```
