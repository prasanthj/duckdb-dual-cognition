Output "docs/images/terminal-demo.gif"

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 22
Set Width 1900
Set Height 1200
Set Padding 34
Set Framerate 30
Set PlaybackSpeed 1
Set Theme "Dracula"
Set TypingSpeed 7ms

Hide
Type "export PS1='$ '; clear"
Enter
Sleep 500ms
Show
Type "uv run python examples/live_terminal_demo.py"
Enter
Wait+Screen /DUCKDB DUAL COGNITION · LIVE/
Wait+Screen /RESULT/
Wait+Screen /PROVENANCE · ESCALATED ROWS/
Wait+Screen /LIVE_DEMO_COMPLETE/
Sleep 10s
