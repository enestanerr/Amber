# vhs tape for docs/assets/demo.gif (≤ 60 s). Install vhs: https://github.com/charmbracelet/vhs
# Run from examples/weekly-report after `make serve`-free setup; `make demo` needs the claude CLI.
Output ../../docs/assets/demo.gif
Set Shell "bash"
Set FontSize 14
Set Width 1400
Set Height 760
Set TypingSpeed 40ms
Set Theme "Catppuccin Mocha"

Type "amber record weekly-report --input week=2026-W44 --mcp mcp.json --allow 'mcp__shop__*' --allow 'mcp__slack__*' -- claude -p \"$(sed 's/{week}/2026-W44/' task.md)\""
Enter
Sleep 12s
Type "amber align weekly-report --show"
Enter
Sleep 4s
Type "amber compile weekly-report"
Enter
Sleep 10s
Type "amber run weekly-report --input week=2026-W45 --allow-writes"
Enter
Sleep 6s
Type "make drift && amber run weekly-report --input week=2026-W45 --allow-writes"
Enter
Sleep 6s
Type "amber doctor weekly-report"
Enter
Sleep 5s
Type "make heal"
Enter
Sleep 15s
