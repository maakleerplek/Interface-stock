#!/bin/bash
# Auto-activate virtual environment script
# This script should be added to .bashrc or .zshrc

VENV_PATH="$HOME/Interface-stock/.venv"

# Only activate if:
# 1. Virtual environment exists
# 2. We're not already in a virtual environment
# 3. We're in an interactive shell
if [ -d "$VENV_PATH" ] && [ -z "$VIRTUAL_ENV" ] && [ -n "$PS1" ]; then
    echo "🔧 Activating Interface-stock virtual environment..."
    source "$VENV_PATH/bin/activate"
    
    # Optional: Change to the project directory
    # cd "$HOME/Interface-stock"
fi
