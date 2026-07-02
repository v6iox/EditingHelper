#!/usr/bin/env bash
# One-command code-signing setup for EditSync. Run this on your Mac:
#
#   ./packaging/setup_signing.sh
#
# It generates your signing key + certificate request, opens the right
# Apple pages in your browser, converts the certificate Apple gives you,
# figures out your Team ID, and uploads all five GitHub secrets (via the
# `gh` CLI if you have it, otherwise it walks you through pasting them).
# You only do the parts Apple requires a human for: uploading one file,
# clicking Download, and generating one app-specific password.
set -euo pipefail

REPO="v6iox/EditingHelper"
WORKDIR="$HOME/EditSync-signing"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

mkdir -p "$WORKDIR"
cd "$WORKDIR"

# --- 1. signing key + certificate request -------------------------------
if [ ! -f editsync_signing_key.pem ]; then
    say "Step 1/4 — creating your private signing key and certificate request"
    openssl req -new -newkey rsa:2048 -nodes \
        -keyout editsync_signing_key.pem \
        -out EditSync.certSigningRequest \
        -subj "/CN=EditSync Signing/O=86 Auto Lab" 2>/dev/null
    chmod 600 editsync_signing_key.pem
else
    say "Step 1/4 — reusing the existing key in $WORKDIR"
fi

say "Your browser will open Apple's 'Create a New Certificate' page."
echo "  1. Select 'Developer ID Application' (the last option) → Continue"
echo "  2. Upload this file when asked (it's also in your clipboard path):"
echo "       $WORKDIR/EditSync.certSigningRequest"
echo "  3. Click Download — the certificate lands in ~/Downloads"
command -v pbcopy >/dev/null && printf '%s' "$WORKDIR/EditSync.certSigningRequest" | pbcopy
command -v open >/dev/null && open "https://developer.apple.com/account/resources/certificates/add"
read -r -p $'\nPress Return once the certificate has downloaded... '

# --- 2. convert Apple's certificate into the CI secret ------------------
say "Step 2/4 — converting the certificate"
CER="$(ls -t "$HOME"/Downloads/*.cer 2>/dev/null | head -1 || true)"
if [ -z "$CER" ]; then
    echo "Couldn't find a .cer file in ~/Downloads. Download it and re-run." >&2
    exit 1
fi
echo "Using: $CER"
openssl x509 -inform DER -in "$CER" -out cert.pem 2>/dev/null || cp "$CER" cert.pem
SUBJECT="$(openssl x509 -in cert.pem -noout -subject)"
if ! printf '%s' "$SUBJECT" | grep -q "Developer ID Application"; then
    echo "That certificate is not a 'Developer ID Application' certificate:" >&2
    echo "  $SUBJECT" >&2
    echo "Create the right type at developer.apple.com and re-run." >&2
    exit 1
fi
TEAM_ID="$(printf '%s' "$SUBJECT" | sed -n 's/.*OU *= *\([A-Z0-9]\{10\}\).*/\1/p')"
P12_PASSWORD="$(openssl rand -hex 12)"
openssl pkcs12 -export \
    -inkey editsync_signing_key.pem -in cert.pem \
    -name "Developer ID Application" \
    -out EditSync.p12 -passout pass:"$P12_PASSWORD"
CERT_B64="$(base64 -i EditSync.p12)"
echo "Certificate OK — Team ID: $TEAM_ID"

# --- 3. notarization credentials ----------------------------------------
say "Step 3/4 — app-specific password for notarization"
echo "Your browser will open your Apple account page:"
echo "  Sign-In and Security → App-Specific Passwords → '+' → name it"
echo "  'EditSync notarization' → copy the xxxx-xxxx-xxxx-xxxx password"
command -v open >/dev/null && open "https://account.apple.com/account/manage"
read -r -p $'\nYour Apple ID email: ' APPLE_ID
read -r -s -p "The app-specific password you just created: " APPLE_APP_PASSWORD
echo

# --- 4. store the five GitHub secrets -----------------------------------
say "Step 4/4 — saving the five GitHub secrets"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    printf '%s' "$CERT_B64"            | gh secret set MACOS_CERT_P12_BASE64 -R "$REPO"
    printf '%s' "$P12_PASSWORD"        | gh secret set MACOS_CERT_PASSWORD   -R "$REPO"
    printf '%s' "$APPLE_ID"            | gh secret set APPLE_ID              -R "$REPO"
    printf '%s' "$TEAM_ID"             | gh secret set APPLE_TEAM_ID         -R "$REPO"
    printf '%s' "$APPLE_APP_PASSWORD"  | gh secret set APPLE_APP_PASSWORD    -R "$REPO"
    say "All five secrets uploaded to $REPO. You're done."
else
    say "GitHub CLI not available — add the secrets by hand (one page, five pastes)."
    echo "Your browser will open the repo's secrets page. For each secret,"
    echo "press Return here to load the VALUE into your clipboard, then paste."
    command -v open >/dev/null && open "https://github.com/$REPO/settings/secrets/actions/new"
    stage() {
        read -r -p "Ready to copy $1 — press Return... "
        printf '%s' "$2" | pbcopy
        echo "  -> clipboard now holds the value for: $1"
    }
    stage MACOS_CERT_P12_BASE64 "$CERT_B64"
    stage MACOS_CERT_PASSWORD "$P12_PASSWORD"
    stage APPLE_ID "$APPLE_ID"
    stage APPLE_TEAM_ID "$TEAM_ID"
    stage APPLE_APP_PASSWORD "$APPLE_APP_PASSWORD"
    say "Done once all five are saved."
fi

echo
echo "Next release will be signed + notarized automatically:"
echo "  GitHub → Actions → 'Build desktop app' → Run workflow → release_tag: v1.0.1"
echo
echo "Keep $WORKDIR safe (it holds your private key and EditSync.p12);"
echo "delete it if you prefer — the copies in GitHub secrets are enough for CI."
