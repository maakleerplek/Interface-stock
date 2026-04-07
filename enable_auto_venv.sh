#!/bin/bash
# Enable auto-activation of virtual environment for Interface-stock
# Run this if you want to enable auto-venv without running full install.sh

set -e

BASHRC="$HOME/.bashrc"
VENV_MARKER="# Interface-stock auto-venv"

echo "Setting up auto-activation of virtual environment..."

if grep -q "$VENV_MARKER" "$BASHRC" 2>/dev/null; then
    echo "✓ Auto-activation is already configured in $BASHRC"
    exit 0
fi

# Add to .bashrc
cat >> "$BASHRC" << 'EOF'

# Interface-stock auto-venv
# Auto-activate virtual environment for Interface-stock project
if [ -d "$HOME/Interface-stock/.venv" ] && [ -z "$VIRTUAL_ENV" ]; then
    source "$HOME/Interface-stock/.venv/bin/activate"
    echo "✓ Interface-stock virtual environment activated"
fi
EOF

echo ""
echo "✓ Auto-activation added to $BASHRC"
echo ""
echo "To activate now, run:"
echo "  source ~/.bashrc"
echo ""
echo "Or simply open a new terminal."
echo ""
echo "To disable later, edit ~/.bashrc and remove the 'Interface-stock auto-venv' section."
