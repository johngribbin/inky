#!/bin/zsh
# Remove everything setup.sh added. Asks before deleting the model.
DIR=${0:A:h}
BIN=$HOME/.local/bin
LABEL=io.github.inky
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
LOGS=${XDG_STATE_HOME:-$HOME/.local/state}/inky
MODEL=inkling-2-mini:latest

ok() { print -P "  %F{green}✓%f $1"; }

if [[ -f $PLIST ]]; then
  launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null
  rm -f "$PLIST"; ok "Removed login item"
fi

for f in $BIN/*(N@); do
  [[ ${f:A} == $DIR/inky ]] && rm -f "$f" && ok "Removed command $f"
done

for rc in $HOME/.zshrc $HOME/.bash_profile; do
  if grep -q '# >>> inky >>>' "$rc" 2>/dev/null; then
    sed -i '' '/# >>> inky >>>/,/# <<< inky <<</d' "$rc"; ok "Removed PATH block from $rc"
  fi
done

CONFIG_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/inky
[[ -d $CONFIG_DIR ]] && rm -rf "$CONFIG_DIR" && ok "Removed settings ($CONFIG_DIR)"
[[ -d $LOGS ]] && rm -rf "$LOGS" && ok "Removed logs ($LOGS)"

read "ans?  Also delete the Inkling 2 Mini model (~20 GB)? Other Ollama apps can use it too. [y/N] "
if [[ $ans == [yY]* ]]; then
  OLLAMA_HOST=127.0.0.1:11439 OLLAMA_NOPRUNE=1 OLLAMA_NO_CLOUD=1 ollama serve >/dev/null 2>&1 & TMP_PID=$!
  until curl -s http://127.0.0.1:11439/api/version >/dev/null; do kill -0 $TMP_PID 2>/dev/null || break; sleep 0.5; done
  OLLAMA_HOST=127.0.0.1:11439 ollama rm $MODEL >/dev/null 2>&1 && ok "Deleted model" || echo "  Couldn't delete it. Try: ollama rm $MODEL"
  kill $TMP_PID 2>/dev/null
fi
echo "Any other models you added with 'use' are still in Ollama: see 'ollama list', remove with 'ollama rm <name>'."
echo "Done. Ollama itself was left installed; remove the Ollama app from Applications if you no longer want it."
