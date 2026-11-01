# PyInstaller spec: one folder holding agent-core.exe (daemon), agent-ui.exe (desktop, no console) and agent-chat.exe (terminal),
# sharing one copy of the libraries. Build with scripts/build.ps1. Run from the repo root.
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

EXCLUDES = ["tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff", "playwright", "fastembed", "faster_whisper", "torch", "tensorflow",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtNfc"]


def analysis(script):
    return Analysis([f"{ROOT}/packaging/{script}"], pathex=[f"{ROOT}/src"], binaries=binaries, datas=datas, hiddenimports=hidden,
                    hookspath=[], runtime_hooks=[], excludes=EXCLUDES, noarchive=False)


core, ui, chat = analysis("launch_core.py"), analysis("launch_ui.py"), analysis("launch_chat.py")
MERGE((core, "agent-core", "agent-core"), (ui, "agent-ui", "agent-ui"), (chat, "agent-chat", "agent-chat"))

exes = []
for a, name, console in ((core, "agent-core", True), (ui, "agent-ui", False), (chat, "agent-chat", True)):
    pyz = PYZ(a.pure)
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, icon=None, upx=False))
COLLECT(*exes, *[x for a in (core, ui, chat) for x in (a.binaries, a.datas)], strip=False, upx=False, name="personal-agent")
