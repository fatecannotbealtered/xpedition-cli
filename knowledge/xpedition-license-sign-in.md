---
id: xpedition-license-sign-in
title: The licence sign-in dialog and licence token failures
kind: troubleshooting
applies_to: [session open, session get, session list, doc open, doctor, part create, part list, forward-annotation run, pcb get]
tags: [license, sign-in, dialog, startup]
lang: en
based_on: xpedition-cli 1.0.3 live record, docs/E2E.md (runs of 2026-09-26 and of 2026-09-29 to 30) and docs/NATIVE_ADAPTER.md of npm @fateforge/xpedition-cli@1.0.3; old implementation (commit 4a054a3), native_com_adapter.py (_is_license_call_missing, _com_error) and win_dialogs.py (answer_prompts); capability survey docs/APPLICATION.md (Licensing; Automation Interfaces)
updated_on: 2026-11-05
---

## The sign-in dialog

When Designer starts, it may stop at the Siemens licence sign-in dialog. In the live runs it did so after a reboot. Only a person can complete it.

- session get may show the session as blocked, with the window title in `blocker`. A command may return `E_HUMAN_REQUIRED`.
- Tell the person to sign in. Do not click, type into or read the dialog. xpedition-cli 1.0.3 never inspected or answered a window whose title spoke of a licence, a sign-in or a login (including 许可证, 授权 and 登录).
- After the person has signed in, run session get until the session is ready.
- File-mode commands do not need Designer. In the live runs, project create and the library steps ran on Xpedition's converters while Designer waited at the sign-in.

## The Layout licence token

Layout automation must take a licence token for each document: `Validate(0)`, then `GetToken` from `MGCPCBAutomationLicensing.Application`, then `Validate(token)`. The tool does this itself.

When the step is missed, the error carries product code 10279 and scode 0x8004022D. Its message is localized. On a Chinese installation it reads "自动化代码不包含身份验证所需的许可调用。", which contains neither "license" nor "token". xpedition-cli 1.0.3 reported it as `E_AUTH`.

`E_AUTH` is not retryable. Do not repeat the command in a loop. Report the error and its details to the person.

## Licence settings

- Xpedition finds its licence through `MGLS_LICENSE_FILE`, `SALT_LICENSE_SERVER` and `LM_LICENSE_FILE`. A process without them takes them from the registry (HKCU or HKLM).
- Never print, copy or report the values of these settings.
- Which licence feature automation needs is not known. Ask the person.
- The library converters behind part list, part get and part create show a message box on a licence problem and return no exit code.
