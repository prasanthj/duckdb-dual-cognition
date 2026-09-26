Output "docs/images/terminal-demo.gif"

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 24
Set Width 1800
Set Height 1060
Set Padding 36
Set Framerate 30
Set PlaybackSpeed 1
Set Theme "Dracula"
Set TypingSpeed 7ms

Hide
Type "export PS1='$ '; clear"
Enter
Sleep 500ms
Show
Type "uv run python examples/terminal_demo.py"
Enter
Wait+Screen /DUCKDB DUAL COGNITION/
Wait+Screen /RESULT/
Wait+Screen /PROVENANCE/
Wait+Screen /DEMO_COMPLETE/
Sleep 2s
