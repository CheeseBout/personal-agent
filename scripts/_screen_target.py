"""A tiny window for scripts/screen_check.py to operate on (separate process: UI Automation must not query its own thread)."""

import sys

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

app = QApplication(sys.argv)
title = sys.argv[1] if len(sys.argv) > 1 else "PA Screen Test"
w = QWidget()
w.setWindowTitle(title)
lay = QVBoxLayout(w)
lay.addWidget(QLabel("Save the report"))
btn = QPushButton("Save")
btn.setObjectName("btnSave")
notes, pw = QLineEdit(), QLineEdit()
notes.setAccessibleName("Notes")
pw.setAccessibleName("Password")
pw.setEchoMode(QLineEdit.EchoMode.Password)
for x in (btn, notes, pw):
    lay.addWidget(x)
btn.clicked.connect(lambda: print("CLICKED", flush=True))
notes.textChanged.connect(lambda t: print("TEXT:" + t, flush=True))
pw.textChanged.connect(lambda t: print("PASSWORD:" + t, flush=True))
w.resize(420, 260)
w.move(int(sys.argv[2]) if len(sys.argv) > 2 else 120, int(sys.argv[3]) if len(sys.argv) > 3 else 120)
w.show()
print("READY", flush=True)
app.exec()
