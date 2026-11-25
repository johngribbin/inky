#!/bin/zsh
# inky setup: checks your Mac, downloads the model, and installs a command to start it.
#
# What this script does NOT do: use sudo, send any data anywhere, or install anything
# without asking. Its only network access is the model download from Ollama's registry
# (through Ollama itself). Every change it makes is listed at the end and removed by ./uninstall.sh.
#
# Usage: ./setup.sh          interactive
#        ./setup.sh --yes    accept defaults (download model, command "inky", add to PATH, no login item)

DIR=${0:A:h}
MODEL=inkling-2-mini:latest
MIN_OLLAMA=0.40.17
MIN_MEM_GB=32
NEED_DISK_GB=25
BIN=$HOME/.local/bin
LABEL=io.github.inky
PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
LOGS=${XDG_STATE_HOME:-$HOME/.local/state}/inky
YES=0; [[ $1 == --yes || $1 == -y ]] && YES=1
CHANGES=()

say()  { print -P "%B$1%b"; }
ok()   { print -P "  %F{green}✓%f $1"; }
warn() { print -P "  %F{yellow}!%f $1"; }
die()  { print -P "  %F{red}✗%f $1"; exit 1; }
ask() {  # ask "question" default(y/n)
  local def=$2 ans
  if (( YES )); then [[ $def == y ]]; return; fi
  read "ans?  $1 [$([[ $def == y ]] && echo Y/n || echo y/N)] "
  ans=${ans:-$def}; [[ $ans == [yY]* ]]
}

say "inky setup"
echo "Runs Inkling 2 Mini locally. Nothing leaves your Mac except the model download."
echo

# 1. Machine checks -----------------------------------------------------------
say "1. Checking your Mac"
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || die "Needs a Mac with Apple Silicon (M1 or newer)."
ok "Apple Silicon ($(/usr/sbin/sysctl -n machdep.cpu.brand_string))"
mem_gb=$(( $(/usr/sbin/sysctl -n hw.memsize) / 1073741824 ))
(( mem_gb >= MIN_MEM_GB )) || die "Needs at least ${MIN_MEM_GB} GB of memory; this Mac has ${mem_gb} GB."
ok "${mem_gb} GB memory"
python3 -c 'pass' 2>/dev/null || die "Python 3 is missing. Install Apple's command line tools with:  xcode-select --install"
ok "Python 3"

# 2. Ollama --------------------------------------------------------------------
echo; say "2. Ollama"
if ! command -v ollama >/dev/null; then
  warn "Ollama is not installed. inky uses it to run the model (free, open source)."
  echo "    Download page: https://ollama.com/download"
  if (( YES )); then die "Install Ollama, then re-run ./setup.sh"; fi
  ask "Open the download page now?" y && open "https://ollama.com/download"
  echo "    Install it, open the Ollama app once (it adds the 'ollama' command), then press Enter."
  read "?"
  command -v ollama >/dev/null || die "Still can't find 'ollama'. Re-run ./setup.sh after installing."
fi
ver=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | tail -1)
python3 -c "import sys; v=lambda s: tuple(map(int,s.split('.'))); sys.exit(0 if v('$ver')>=v('$MIN_OLLAMA') else 1)" \
  || die "Ollama $ver is too old; Inkling needs $MIN_OLLAMA or newer. Update it from https://ollama.com/download"
ok "Ollama $ver"

# 3. Model ---------------------------------------------------------------------
echo; say "3. Model"
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}/inky/model
if (( ! YES )); then
  echo "  1) Inkling 2 Mini, official build  (recommended: ~20 GB, fastest on Mac)"
  echo "  2) Something else: an Ollama model name, or a Hugging Face GGUF as hf.co/<user>/<repo>:<quant>"
  echo "     (community GGUF builds run on Ollama's llama.cpp engine, so they're slower than option 1)"
  read "choice?  Choose [1]: "
  if [[ $choice == 2 ]]; then
    read "MODEL?  Model name: "
    [[ -n $MODEL ]] || die "No model name given."
    [[ $MODEL == *:* ]] || MODEL=$MODEL:latest
  fi
fi
echo "  Using $MODEL"
TMP_PORT=11439
OLLAMA_HOST=127.0.0.1:$TMP_PORT OLLAMA_NOPRUNE=1 OLLAMA_NO_CLOUD=1 ollama serve >/dev/null 2>&1 &
TMP_PID=$!
trap "kill $TMP_PID 2>/dev/null" EXIT
until curl -s "http://127.0.0.1:$TMP_PORT/api/version" >/dev/null; do
  kill -0 $TMP_PID 2>/dev/null || die "Couldn't start Ollama to check for the model."
  sleep 0.5
done
if OLLAMA_HOST=127.0.0.1:$TMP_PORT ollama show $MODEL >/dev/null 2>&1; then
  ok "Already downloaded"
else
  free_gb=$(( $(df -k "$HOME" | awk 'NR==2{print $4}') / 1048576 ))
  (( free_gb >= NEED_DISK_GB )) || die "Needs about ${NEED_DISK_GB} GB free disk space; you have ${free_gb} GB."
  [[ $MODEL == inkling-2-mini:latest ]] && echo "  The model is about 20 GB (you have ${free_gb} GB free)."
  if ask "Download it now?" y; then
    OLLAMA_HOST=127.0.0.1:$TMP_PORT ollama pull ${MODEL%:latest} || die "Download failed. Re-run ./setup.sh to resume."
    ok "Downloaded"; CHANGES+=("Downloaded the model (~20 GB, stored by Ollama in ~/.ollama/models)")
  else
    warn "Skipped. Download later with:  ollama pull ${MODEL%:latest}"
  fi
fi
kill $TMP_PID 2>/dev/null; trap - EXIT
if [[ $MODEL != inkling-2-mini:latest ]]; then
  mkdir -p "${CONFIG:h}"; echo "$MODEL" > "$CONFIG"
  ok "Saved as the default model"; CHANGES+=("Saved your model choice in $CONFIG")
fi

# 4. Command -------------------------------------------------------------------
echo; say "4. Start command"
name=inky
if (( ! YES )); then
  read "name?  What do you want to type to start it? [inky] "; name=${name:-inky}
fi
[[ $name =~ '^[A-Za-z0-9_-]+$' ]] || die "Use only letters, numbers, - and _ in the command name."
existing=$(command -v $name 2>/dev/null)
if [[ -n $existing && ${existing:A} != $DIR/inky ]]; then
  warn "'$name' already exists ($existing)."
  ask "Use '$name' anyway? (yours would take priority only if $BIN comes first on PATH)" n || die "Re-run ./setup.sh and pick another name."
fi
chmod +x "$DIR/inky"
mkdir -p "$BIN"
if [[ -e $BIN/$name && ! -L $BIN/$name ]]; then die "$BIN/$name is an existing file; not overwriting it."; fi
ln -sf "$DIR/inky" "$BIN/$name"
ok "Created $BIN/$name -> $DIR/inky"; CHANGES+=("Created the command $BIN/$name (a link to this folder)")

if [[ ":$PATH:" != *":$BIN:"* ]]; then
  case ${SHELL:t} in bash) rc=$HOME/.bash_profile;; *) rc=$HOME/.zshrc;; esac
  if grep -q '# >>> inky >>>' "$rc" 2>/dev/null; then
    ok "PATH line already in $rc"
  elif ask "$BIN isn't on your PATH. Add one line to $rc so '$name' works in new terminals?" y; then
    printf '\n# >>> inky >>>\nexport PATH="$HOME/.local/bin:$PATH"\n# <<< inky <<<\n' >> "$rc"
    ok "Added to $rc"; CHANGES+=("Added a marked PATH block to $rc")
  else
    warn "Skipped. Run it with the full path: $BIN/$name"
  fi
fi

# 5. Start at login (opt-in) ---------------------------------------------------
echo; say "5. Start at login (optional)"
echo "  Keeps Inkling running in the background so coding agents can always reach it."
echo "  Cost: about 22 GB of memory stays in use while you're logged in."
if [[ -f $PLIST ]]; then
  ok "Already set up ($PLIST)"
elif (( ! YES )) && ask "Start Inkling automatically when you log in?" n; then
  mkdir -p "${PLIST:h}" "$LOGS"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/inky</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>INKY_OPEN</key><string>0</string>
    <key>PATH</key><string>${$(command -v ollama):h}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGS/login-item.log</string>
  <key>StandardErrorPath</key><string>$LOGS/login-item.log</string>
</dict>
</plist>
PL
  launchctl bootstrap gui/$(id -u) "$PLIST" 2>/dev/null
  ok "Will start at login (and is starting now)"; CHANGES+=("Added login item $PLIST")
else
  ok "Skipped (start it yourself with '$name')"
fi

# Summary ---------------------------------------------------------------------
echo; say "Done"
if (( ${#CHANGES} )); then echo "Changes made:"; printf '  - %s\n' "${CHANGES[@]}"; fi
cat <<MSG

Start it:   open a new terminal and type  $name
Chat UI:    http://127.0.0.1:8089/
For coding agents:  base URL http://127.0.0.1:8089/v1, model $MODEL, any API key

Switch models later:  $name use <model>    (e.g. $name use hf.co/<user>/<repo>:Q8_0)
Undo everything:  ./uninstall.sh
MSG
