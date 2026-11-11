# PyInstaller spec: one folder holding agent-core.exe (daemon) and agent-chat.exe (terminal), sharing one copy of the libraries.
# The desktop UI is the Electron app in electron-ui/; scripts/build.ps1 builds both and puts the UI into this folder's ui\ subfolder.
# Run from the repo root.
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH.rsplit("\\", 1)[0] if "\\" in SPECPATH else SPECPATH.rsplit("/", 1)[0]

hidden = []
datas = [(f"{ROOT}/config", "config"), (f"{ROOT}/docker", "docker")]
binaries = []
# Lazily imported (so static analysis does not see them): providers, optional features, plugin-style libraries.
for pkg in ("personal_agent", "langchain_anthropic", "langchain_openai", "langchain_google_genai", "langgraph.checkpoint.sqlite",
            "uvicorn", "keyring.backends", "edge_tts", "pywinauto.controls", "mcp", "langchain_mcp_adapters"):
    hidden += collect_submodules(pkg, filter=lambda n: ".cli" not in n and ".tests" not in n and "__main__" not in n)  # some CLIs call sys.exit at import
for pkg in ("sqlite_vec", "ddgs", "comtypes", "langfuse", "imagehash", "recurring_ical_events", "icalendar", "x_wr_timezone"):
    d, b, h = collect_all(pkg)
    datas, binaries, hidden = datas + d, binaries + b, hidden + h
hidden += ["aiosqlite", "websockets.asyncio.client", "PIL.ImageDraw", "psutil", "mss.windows", "winreg", "google.genai", "openai", "anthropic"]

EXCLUDES = ["tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff", "playwright", "fastembed", "faster_whisper", "torch", "tensorflow", "PySide6"]


def analysis(script):
    return Analysis([f"{ROOT}/packaging/{script}"], pathex=[f"{ROOT}/src"], binaries=binaries, datas=datas, hiddenimports=hidden,
                    hookspath=[], runtime_hooks=[], excludes=EXCLUDES, noarchive=False)


core, chat = analysis("launch_core.py"), analysis("launch_chat.py")
MERGE((core, "agent-core", "agent-core"), (chat, "agent-chat", "agent-chat"))

exes = []
for a, name, console in ((core, "agent-core", True), (chat, "agent-chat", True)):
    pyz = PYZ(a.pure)
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, icon=None, upx=False))
COLLECT(*exes, *[x for a in (core, chat) for x in (a.binaries, a.datas)], strip=False, upx=False, name="personal-agent")
