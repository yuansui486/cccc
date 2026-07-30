# Windows UIAutomation bindings

These files were generated on a real Windows installation with `comtypes 1.4.16`.
They are build inputs for Nuitka Windows distributions produced under Wine, whose
private UIAutomation type library does not expose the Windows client interfaces.

Do not regenerate these files under Wine. When the locked `comtypes` version is
updated, regenerate all four modules together on real Windows and update
`EXPECTED_COMTYPES_VERSION` in `scripts/stage_windows_uia_bindings.py`.
