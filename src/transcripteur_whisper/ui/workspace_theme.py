"""Opaque compact surfaces: no page effects, no inherited transparency."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_workspace_theme(dark: bool) -> None:
    app = QApplication.instance()
    if app is None:
        return
    c = ({"bg": "#0c1726", "panel": "#132335", "field": "#0f1d2e", "text": "#edf4f8",
          "muted": "#a6bdca", "border": "#314557", "accent": "#19b8ba", "hover": "#203c4d",
          "selected": "#126e78", "disabled": "#6e8595"} if dark else
         {"bg": "#edf4f7", "panel": "#ffffff", "field": "#f5f8fa", "text": "#152d41",
          "muted": "#4c6677", "border": "#c9d8e0", "accent": "#087d87", "hover": "#e4f2f2",
          "selected": "#116d78", "disabled": "#70838c"})
    palette = QPalette()
    for role, value in ((QPalette.ColorRole.Window, c["bg"]),
                        (QPalette.ColorRole.Base, c["field"]),
                        (QPalette.ColorRole.AlternateBase, c["panel"]),
                        (QPalette.ColorRole.WindowText, c["text"]),
                        (QPalette.ColorRole.Text, c["text"]),
                        (QPalette.ColorRole.ButtonText, c["text"]),
                        (QPalette.ColorRole.Button, c["panel"]),
                        (QPalette.ColorRole.Highlight, c["selected"]),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"),
                        (QPalette.ColorRole.PlaceholderText, c["muted"])):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setStyleSheet(f'''
        QWidget {{ color: {c['text']}; font-family: "Segoe UI", sans-serif; font-size: 10pt; }}
        QMainWindow, QDialog {{ background: {c['bg']}; }}
        QWidget#workspaceRoot, QWidget#workspaceHeader {{ background: {c['bg']}; }}
        QWidget[workspacePage="true"], QStackedWidget {{ background: {c['panel']}; }}
        QLabel {{ background: transparent; }}
        QLabel#workspaceTitle {{ font-size: 14pt; font-weight: 700; }}
        QLabel[secondary="true"] {{ color: {c['muted']}; }}
        QTabWidget::pane {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
        QTabBar::tab {{ background: {c['bg']}; color: {c['muted']}; padding: 7px 13px; margin: 2px 3px 5px 0; border-radius: 8px; }}
        QTabBar::tab:selected {{ color: {c['text']}; background: {c['panel']}; border: 1px solid {c['border']}; }}
        QTabBar::tab:hover:!selected {{ background: {c['hover']}; }}
        QGroupBox {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px;
                     margin-top: 10px; padding: 12px 8px 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
        QLineEdit, QComboBox {{ background: {c['field']}; border: 1px solid {c['border']};
                              border-radius: 6px; padding: 5px 8px; min-height: 20px; }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {c['accent']}; }}
        QComboBox::drop-down {{ border: 0; width: 20px; }}
        QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']}; selection-background-color: {c['selected']}; }}
        QPlainTextEdit, QTextEdit, QListWidget, QTableWidget {{ background: {c['field']}; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: 6px; selection-background-color: {c['selected']}; selection-color: white; }}
        QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: 0;
                              border-bottom: 1px solid {c['border']}; padding: 5px; }}
        QTableWidget {{ gridline-color: {c['border']}; }}
        QTableWidget::item, QListWidget::item {{ padding: 3px 5px; }}
        QTableWidget::item:selected, QListWidget::item:selected {{ background: {c['selected']}; color: white; }}
        QPushButton, QToolButton {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
                                  border-radius: 7px; padding: 6px 10px; min-height: 18px; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
        QPushButton:focus, QToolButton:focus {{ border: 2px solid {c['accent']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['disabled']}; }}
        QPushButton#startButton, QPushButton[primary="true"] {{ background: {c['accent']}; color: white; font-weight: 600; }}
        QPushButton#recordButton {{ background: #bb304b; color: white; border-color: #e3697a; font-weight: 700; min-height: 28px; }}
        QPushButton#recordButton:disabled {{ background: {c['panel']}; color: {c['disabled']}; }}
        QProgressBar {{ border: 1px solid {c['border']}; background: {c['field']}; border-radius: 4px; text-align: center; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
        QCheckBox {{ spacing: 6px; min-height: 22px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; }}
        QScrollArea {{ background: {c['panel']}; border: 0; }}
        QScrollBar:vertical {{ background: {c['panel']}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 24px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{ background: {c['panel']}; height: 10px; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; min-width: 24px; border-radius: 4px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QSplitter::handle {{ background: {c['border']}; }}
        QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
    ''')
