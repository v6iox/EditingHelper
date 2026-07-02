# Code signing & notarization setup

One-time setup after getting an Apple Developer account. When the five
GitHub secrets below exist, every macOS build is automatically signed
with your Developer ID, notarized by Apple, and stapled — users just
double-click the app, no security warnings. Without the secrets, builds
stay unsigned (nothing breaks).

## The fast way (recommended)

On your Mac, from the repository root:

```sh
./packaging/setup_signing.sh
```

The script creates the key and certificate request, opens the right
Apple pages, converts the downloaded certificate, extracts your Team ID,
and uploads all five secrets (automatically with the `gh` CLI, or by
staging each value into your clipboard). You only upload one file on
Apple's site, click Download, and create one app-specific password.

The manual equivalent is documented below.

You need a Mac for steps 1–2 (they create the private key).

## 1. Create the Developer ID certificate (~3 min)

1. On your Mac, open **Keychain Access** → menu **Keychain Access →
   Certificate Assistant → Request a Certificate From a Certificate
   Authority…**
2. Enter your email, leave CA Email empty, choose **Saved to disk**,
   save the `.certSigningRequest` file.
3. Go to [developer.apple.com/account/resources/certificates](https://developer.apple.com/account/resources/certificates)
   → **+** → select **Developer ID Application** (under "Software") →
   upload the request file → **Download** the certificate.
   *(Developer ID certificates can only be created by the Account
   Holder — that's you, since you bought the membership.)*
4. Double-click the downloaded `.cer` — it lands in Keychain Access
   under **My Certificates**, paired with your private key.

## 2. Export the certificate as .p12 (~1 min)

1. In Keychain Access → **My Certificates**, right-click
   **Developer ID Application: Your Name (TEAMID)** → **Export…**
2. Format **Personal Information Exchange (.p12)**, save it, and set a
   password when prompted — you'll store this password as a secret.
3. In Terminal, copy the base64 of the file to your clipboard:

   ```sh
   base64 -i ~/Desktop/EditSync.p12 | pbcopy
   ```

## 3. Get your notarization credentials (~2 min)

- **Apple ID** — the email you sign in to Apple with.
- **Team ID** — 10 characters, shown at
  [developer.apple.com/account](https://developer.apple.com/account)
  under Membership details (also in the certificate name).
- **App-specific password** — go to
  [account.apple.com](https://account.apple.com) → **Sign-In and
  Security → App-Specific Passwords** → **+**, name it "EditSync
  notarization", copy the generated `xxxx-xxxx-xxxx-xxxx` password.

## 4. Add the five GitHub secrets (~2 min)

Repo → **Settings → Secrets and variables → Actions → New repository
secret**, five times:

| Secret name | Value |
|---|---|
| `MACOS_CERT_P12_BASE64` | the base64 you copied in step 2 |
| `MACOS_CERT_PASSWORD` | the password you set when exporting the .p12 |
| `APPLE_ID` | your Apple ID email |
| `APPLE_TEAM_ID` | your 10-character Team ID |
| `APPLE_APP_PASSWORD` | the app-specific password from step 3 |

## 5. Cut a signed release

Actions → **Build desktop app** → **Run workflow** → set
`release_tag` to the next version (e.g. `v1.0.1`) → **Run**. The macOS
job imports the certificate, signs every binary in the app with
hardened runtime, notarizes the DMG with Apple (adds ~1–5 min), and
staples the ticket so the app opens instantly even offline.

Verify a downloaded build with:

```sh
spctl -a -vv /Applications/EditSync.app   # → "accepted", "Notarized Developer ID"
```

## Housekeeping

- The certificate is valid for 5 years; the app-specific password
  doesn't expire but can be revoked at account.apple.com.
- If a notarization is rejected, get the details with:
  `xcrun notarytool log <submission-id> --apple-id ... --team-id ... --password ...`
- Never commit the .p12 or any of these values to the repository.
