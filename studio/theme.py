STYLE = '''
* { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 12px; }
QMainWindow, QDialog { background: #0e1521; color: #edf2fa; }
QWidget { color: #dce5f3; }
QFrame#panel { background: #172131; border: 1px solid #29374c; border-radius: 10px; }
QFrame#header { background: #121c2a; border-bottom: 1px solid #29374c; }
QLabel#title { font-size: 22px; font-weight: 700; color: #f3f7ff; }
QLabel#section { font-size: 14px; font-weight: 700; color: #eef4fc; }
QLabel#muted { color: #91a1b9; }
QLabel#badge { color: #62dfbf; background: #173b36; padding: 5px 10px; border-radius: 7px; }
QLabel#templatePreview { background: #101925; border: 1px dashed #43516a; border-radius: 8px; color: #91a1b9; }
QPushButton { background: #243249; border: 1px solid #364860; border-radius: 6px; padding: 7px 11px; color: #e3edfa; }
QPushButton:hover { background: #30435d; border-color: #6281a6; }
QPushButton:pressed { background: #172638; }
QPushButton:disabled { color: #66768a; background: #1b2534; border-color: #293547; }
QPushButton#primary { background: #54d6b3; color: #102b29; border-color: #54d6b3; font-weight: 700; }
QPushButton#primary:hover { background: #74e5c6; }
QPushButton#primary:disabled { background: #2a6156; color: #82a59c; border-color: #2a6156; }
QPushButton#danger { color: #ffb4af; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #101925; border: 1px solid #34435a; border-radius: 5px; padding: 6px; selection-background-color: #326859; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #54d6b3; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #1b293d; selection-background-color: #315d59; color: #e5eefc; }
QListWidget { background: transparent; border: 0; outline: 0; }
QListWidget::item { border: 1px solid #293950; border-radius: 7px; margin: 3px 0; padding: 9px 6px; background: #1c2a3e; }
QListWidget::item:selected { background: #234640; border-color: #54d6b3; color: #e7fff7; }
QListWidget::item:hover { border-color: #7291ac; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QPlainTextEdit { background: #101925; border: 0; padding: 4px; color: #a8bad1; font-family: "Consolas", "Microsoft YaHei UI"; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #131d2b; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #40516a; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: #0e1521; width: 9px; height: 9px; }
QStatusBar { background: #111a27; color: #91a1b9; }
QToolTip { background: #24354d; border: 1px solid #627e9c; color: white; padding: 7px; }
QDialogButtonBox QPushButton { min-width: 75px; }
'''
