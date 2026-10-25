# Genuine Lark → macOS reproduction design

This is a test plan, not a completed reproduction. No Lark account, customer file,
credential, existing profile, or remote user Mac has been accessed by this task.

## What a genuine reproduction requires

Use a fresh real Mac desktop session with the official Lark macOS application.
Record the actual Lark version, distribution (website versus Mac App Store),
architecture and macOS build. A different/current client version is a versioned
comparison, not proof of the affected computer's exact behaviour.

Use a deliberately designated test-only Lark account and self-chat or test-only
workspace. The only transferred attachment is the sanitized v5.2 release ZIP:

- File SHA256: `babdc6f6e7271d50dc47a0e6a5ff250eb84927f083bcca96aa39af5d5b4dc567`
- Launcher SHA256: `e21b230a6f7c80bf224c3609dbac0b72ba6de08713334ec66211248bbcf4e41e`
- Public code commit: `0b9dd65d55b3847bf67b9138799edb68edd7d893`

Do not copy a production Lark profile, cookies, session database, password or
verification code into a CI runner. Do not publish QR login challenges, account
identifiers, raw authenticated download links or application logs containing them.

## Separate the two Lark paths

The official Lark archive FAQ says that clicking an archive can open Lark's
automatically extracted file preview. That is a different operation from
downloading the complete original ZIP and using macOS Archive Utility.

Run each applicable branch with an independent clean destination:

1. **Whole ZIP path:** receive attachment in the actual desktop client → click its
   Download action → verify downloaded ZIP hash → reveal the actual downloaded
   file in Finder → double-click ZIP so Archive Utility extracts it → inspect the
   extracted launcher → double-click the launcher in Finder.
2. **Lark archive preview path:** click ZIP attachment inside Lark → inspect its
   archive preview → download an individual extracted file/folder through Lark's
   own action → inspect byte hashes, permissions and attributes → attempt the
   exact Finder launch. This branch must be labelled separately; it may not
   preserve UNIX executable bits as a ZIP extraction would.

Do not quietly substitute `curl`, a Lark API, a bot download, browser download or
manual `xattr` assignment for either branch. Those are useful independent tests,
but do not execute Lark desktop's download/extraction code path.

## Evidence and strict stage checks

| Stage | Required evidence | Honest result if unavailable |
| --- | --- | --- |
| Runner | Real macOS version/build/CPU, interactive desktop availability, normal protections retained | Environment not ready |
| Official installer | Official page link and resolved installer URL, installer hash, app bundle identifier/version, actual signature assessment | Installer not verified; do not execute |
| App readiness | Lark launches to a stable normal or logged-out UI | Readiness only |
| Authentication | Dedicated authorized test account is actually logged in; no credential/profile copying | Login required; full reproduction blocked |
| Test attachment | Sanitized release exists in the designated self/test chat and its identity is verified | No attachment; download stage not tested |
| Native download | Lark client download action observed, actual path/version recorded, resulting ZIP or file hash matches | Native download not completed, or byte mismatch |
| Native extraction | Finder/Archive Utility action observed for whole-ZIP branch, file hash and executable metadata recorded | Extraction stage not tested |
| Attributes | Read-only `xattr`, mode and file-type observations before/after extraction; origin URLs redacted | Attributes unknown |
| Finder launch | Exact release launcher selected and opened; real dialog text/screenshot or real tool window recorded | `open` exit0 alone is insufficient |
| Functional app | Tk window, synthetic conversion and boundary checks run only after normal launch succeeds | Post-launch functionality not tested |

Before launching, compare the actual bytes to the release. A mismatch is a
download/extraction result requiring investigation; do not proceed by ignoring it.

For public evidence, record only test-file paths, app version and non-sensitive
attribute values. `kMDItemWhereFroms` or Lark download URLs can contain access
tokens; retain only the origin hostname and a hash of the unredacted value.

## UI and security boundaries

- Preserve the Gatekeeper result. Do not remove quarantine, disable protections,
  use Open Anyway, alter TCC databases or bypass an automation/security prompt.
- Running AppleScript/System Events may require automation accessibility access.
  If the fresh runner lacks it, record that stage as blocked; do not grant it by
  modifying the permissions database or hiding the failure.
- Capturing a screen may likewise be restricted. Missing screenshots mean missing
  evidence, not that no dialog appeared.
- A login screen may include a live QR challenge. A public readiness artifact
  should contain only structured `login_required` status, or a carefully cropped
  and redacted view that excludes the challenge and identifiers.
- If LaunchServices accepts an `open` request, that does not establish that the
  target executed. Verify the actual error dialog or the bank-tool window/process.

## What can safely be prepared in GitHub Actions now

A fresh hosted-Mac readiness job can download the officially resolved installer,
verify/record its signature metadata, install the app into the temporary runner,
launch it, and record whether it reaches a logged-out state. This must be named
`Lark desktop readiness`, not `Lark download reproduction passed`.

Completing the authenticated UI path needs a secure, explicitly authorized
human/test-account login channel. Ordinary public Actions logs and artifacts are
not a safe channel for account credentials, session profiles, codes or QR login.
Until that channel and account exist, preparation cannot honestly satisfy a full
native-client reproduction. Existing synthetic quarantine CI remains a separate
simulation, not evidence that Lark itself caused the same result.

## Official sources

- Download: https://www.larksuite.com/en_us/download
- Install/account prerequisites: https://www.larksuite.com/hc/en-US/articles/002194435035-before-you-start-install-lark-and-sign-up
- Desktop login: https://www.larksuite.com/hc/en-US/articles/360048488469-log-in-and-log-out-of-your-lark-account
- Download files, original format, and Mac distribution differences: https://www.larksuite.com/hc/en-US/articles/459605932895-download-documents-or-folders-to-your-device
- Archive preview/extraction path: https://www.larksuite.com/hc/en-US/articles/295782683996-achive-files-in-chats-faqs
