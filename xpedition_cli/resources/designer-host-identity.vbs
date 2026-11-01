Option Explicit
Dim hostApplication, identityBroker
On Error Resume Next
Set hostApplication = Application
If Err.Number = 0 Then
    Set identityBroker = GetObject("__BROKER_OBJREF__")
    If Err.Number = 0 Then Call identityBroker.Bind(hostApplication)
End If
Set identityBroker = Nothing
Set hostApplication = Nothing
On Error GoTo 0
