#!/bin/bash
# Script to retrieve secrets from GitHub and create .env file

set -e

echo "Setting up .env file from GitHub secrets..."

if ! command -v gh &> /dev/null; then
    echo "Error: GitHub CLI (gh) is not installed."
    echo "Install it from: https://cli.github.com/"
    exit 1
fi

# Check if user is authenticated
if ! gh auth status &> /dev/null; then
    echo "Error: Not authenticated with GitHub CLI."
    echo "Run: gh auth login"
    exit 1
fi

# Retrieve secrets and create .env file
cat > .env << EOF
# INVENTREE URL AND TOKEN

INVENTREE_URL=$(gh secret list | grep INVENTREE_URL > /dev/null && echo "https://10.72.3.68:8443" || echo "")
INVENTREE_TOKEN=$(gh secret list | grep INVENTREE_TOKEN > /dev/null && echo "# Secret stored in GitHub - contact admin" || echo "")

# =============================================================================
# WERO (EPC QR) PAYMENT INTEGRATION
# =============================================================================
VITE_PAYMENT_NAME=$(gh secret list | grep VITE_PAYMENT_NAME > /dev/null && echo "# Secret stored in GitHub - contact admin" || echo "")
VITE_PAYMENT_IBAN=$(gh secret list | grep VITE_PAYMENT_IBAN > /dev/null && echo "# Secret stored in GitHub - contact admin" || echo "")
# Payconiq Merchant ID for Payconiq payment link (optional)
VITE_PAYCONIQ_MERCHANT_ID=$(gh secret list | grep VITE_PAYCONIQ_MERCHANT_ID > /dev/null && echo "# Secret stored in GitHub - contact admin" || echo "")
EOF

echo ""
echo "⚠️  .env file created with placeholders."
echo ""
echo "NOTE: For security reasons, GitHub secrets cannot be read via CLI."
echo "You have two options:"
echo ""
echo "1. Contact the repository admin for the actual secret values"
echo "2. If you have access, view secrets at:"
echo "   https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/settings/secrets/actions"
echo ""
echo "Then manually update the .env file with the actual values."
