!include nsDialogs.nsh

!ifndef BUILD_UNINSTALLER
Var DataFolder
Var DataFolderField

!macro customPageAfterChangeDir
  Page custom ShiritaiDataPage ShiritaiDataPageLeave
!macroend

Function ShiritaiDataPage
  ReadRegStr $DataFolder HKCU "Software\ShiritaiBot\Desktop" "DataDirectory"
  ${If} $DataFolder == ""
    StrCpy $DataFolder "$APPDATA\ShiritaiBot\data"
  ${EndIf}
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 15u "Choose where to store conversations, settings, and plugins."
  Pop $0
  ${NSD_CreateDirRequest} 0 18u 75% 13u "$DataFolder"
  Pop $DataFolderField
  ${NSD_CreateBrowseButton} 77% 17u 23% 15u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 ShiritaiBrowseData
  nsDialogs::Show
FunctionEnd

Function ShiritaiBrowseData
  ${NSD_GetText} $DataFolderField $DataFolder
  nsDialogs::SelectFolderDialog "Choose the ShiritaiBot data directory" "$DataFolder"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $DataFolderField $0
  ${EndIf}
FunctionEnd

Function ShiritaiDataPageLeave
  ${NSD_GetText} $DataFolderField $DataFolder
  ${If} $DataFolder == ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "Choose a data directory."
    Abort
  ${EndIf}
  GetFullPathName $DataFolder $DataFolder
  StrLen $0 $INSTDIR
  StrCpy $1 $DataFolder $0
  StrCpy $2 $DataFolder 1 $0
  ${If} $1 == $INSTDIR
  ${AndIf} $2 == ""
  ${OrIf} $1 == $INSTDIR
  ${AndIf} $2 == "\"
    MessageBox MB_ICONEXCLAMATION|MB_OK "Choose a data directory outside the program installation folder."
    Abort
  ${EndIf}
FunctionEnd

!macro customInstall
  ${If} $DataFolder != ""
    WriteRegStr HKCU "Software\ShiritaiBot\Desktop" "DataDirectory" "$DataFolder"
  ${EndIf}
!macroend
!endif
