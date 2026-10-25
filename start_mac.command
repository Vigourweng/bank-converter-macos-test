#!/bin/sh
# Keep this file beside bank_merge.py and the other tool files.
APP_DIR=$(dirname "$0") || exit 1
CDPATH= cd "$APP_DIR" || exit 1
APP_DIR=$(pwd -P) || exit 1
DOWNLOAD_URL='https://www.python.org/downloads/macos/'

offer_python_download() {
  if [ "$1" = 'tkinter' ]; then
    message='检测到 Python，但缺少 tkinter / Tcl-Tk，或组件无法加载。银行流水工具需要它显示窗口。Python 官方 macOS 安装包已包含 tkinter，无需单独 pip 安装。点击下方按钮打开官方下载页，安装完成后重新双击 start_mac.command。'
  else
    message='没有找到可用的 Python 3.10 或更新版本。请安装 Python 官方 macOS 安装包，它包含本工具需要的 tkinter。点击下方按钮打开官方下载页，安装完成后重新双击 start_mac.command。'
  fi
  printf '%s\n' "$message" "$DOWNLOAD_URL"
  if [ -x /usr/bin/osascript ]; then
    choice=$(/usr/bin/osascript \
      -e 'on run argv' \
      -e 'set promptText to item 1 of argv' \
      -e 'set response to display dialog promptText with title "银行流水工具：安装运行组件" buttons {"暂不下载", "打开官方下载页"} default button "打开官方下载页" cancel button "暂不下载" with icon caution' \
      -e 'return button returned of response' \
      -e 'end run' "$message" 2>/dev/null)
    if [ "$choice" = '打开官方下载页' ]; then
      /usr/bin/open "$DOWNLOAD_URL"
      printf '%s\n' '安装完成后，请重新双击 start_mac.command。'
    fi
  fi
}

# Prefer an already usable Python over opening a download page unnecessarily.
# Skip Apple's developer-tools stub so it does not trigger an Xcode install prompt.
PYTHON_BIN=''
COMPATIBLE_PYTHON_FOUND=0
PATH_PYTHON=$(command -v python3 2>/dev/null)
for candidate in /usr/local/bin/python3 /opt/homebrew/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/*/bin/python3 "$PATH_PYTHON"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  [ "$candidate" = '/usr/bin/python3' ] && continue
  if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    COMPATIBLE_PYTHON_FOUND=1
    if "$candidate" -c 'import tkinter; root = tkinter.Tk(); root.withdraw(); root.update(); root.destroy()' >/dev/null 2>&1; then
      PYTHON_BIN=$candidate
      break
    fi
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  if [ "$COMPATIBLE_PYTHON_FOUND" -eq 1 ]; then
    offer_python_download tkinter
  else
    offer_python_download python
  fi
  exit 0
fi

"$PYTHON_BIN" -X utf8 -B "$APP_DIR/bank_merge.py" "$@"
result=$?
if [ "$result" -ne 0 ]; then
  printf '%s\n' 'Unable to start. Check that Python includes tkinter and the whole folder was extracted.'
  read -r unused
fi
exit "$result"
