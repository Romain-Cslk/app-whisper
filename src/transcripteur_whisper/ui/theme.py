"""Native Qt theme derived from the former airy web UI."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_theme(dark: bool) -> None:
    app = QApplication.instance()
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#102B4A" if dark else "#EAF4F5",
        QPalette.ColorRole.WindowText: "#E6ECF2" if dark else "#0B1220",
        QPalette.ColorRole.Base: "#18283B" if dark else "#FFFFFF",
        QPalette.ColorRole.AlternateBase: "#1D3448" if dark else "#F4F8FB",
        QPalette.ColorRole.Text: "#E6ECF2" if dark else "#0B1220",
        QPalette.ColorRole.Button: "#18364A" if dark else "#FFFFFF",
        QPalette.ColorRole.ButtonText: "#E6ECF2" if dark else "#0B1220",
        QPalette.ColorRole.Highlight: "#22C1C3" if dark else "#0B1C48",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.ToolTipBase: "#173047" if dark else "#FFFFFF",
        QPalette.ColorRole.ToolTipText: "#E6ECF2" if dark else "#0B1220",
        QPalette.ColorRole.PlaceholderText: "#AAB4C0" if dark else "#667085",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    disabled = QColor("#718096" if dark else "#98A2B3")
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    app.setPalette(palette)

    text = "#E6ECF2" if dark else "#0B1220"
    muted = "#AAB4C0" if dark else "#4B5563"
    panel = "rgba(18, 24, 38, 190)" if dark else "rgba(255, 255, 255, 215)"
    panel_soft = "rgba(18, 35, 52, 170)" if dark else "rgba(255, 255, 255, 178)"
    border = "rgba(255,255,255,28)" if dark else "rgba(15,23,42,28)"
    input_bg = "rgba(18, 24, 38, 215)" if dark else "rgba(255,255,255,235)"
    accent = "#22C1C3" if dark else "#0B1C48"
    accent2 = "#00796B" if dark else "#009688"
    hover = "#2DD4D6" if dark else "#123068"
    root_gradient = (
        "qradialgradient(cx:0.50, cy:0.00, radius:1.25, fx:0.50, fy:0.00, "
        "stop:0 #0A7779, stop:0.48 #102B4A, stop:1 #0B1422)"
        if dark else
        "qradialgradient(cx:0.50, cy:0.00, radius:1.25, fx:0.50, fy:0.00, "
        "stop:0 #B2E6EB, stop:0.55 #EAF4F5, stop:1 #DCE5FF)"
    )

    app.setStyleSheet(f"""
        QWidget {{ font-family: 'Segoe UI'; font-size: 10pt; color: {text}; }}
        QWidget#appRoot {{ background: {root_gradient}; }}
        QLabel#appTitle {{ font-size: 23pt; font-weight: 700; letter-spacing: 0.3px; }}
        QLabel#appSubtitle, QLabel#mutedLabel {{ color: {muted}; }}

        QTabWidget::pane {{
            background: {panel}; border: 1px solid {border}; border-radius: 16px;
            margin-top: 6px; padding: 8px;
        }}
        QTabBar::tab {{
            background: transparent; color: {muted}; border: 1px solid transparent;
            padding: 10px 16px; margin: 0 3px 0 0; border-radius: 10px; font-weight: 600;
        }}
        QTabBar::tab:hover {{ background: {panel_soft}; color: {text}; }}
        QTabBar::tab:selected {{
            background: {panel_soft}; color: {text}; border: 1px solid {border};
        }}

        QGroupBox {{
            background: {panel_soft}; border: 1px solid {border}; border-radius: 14px;
            margin-top: 13px; padding: 18px 14px 14px 14px; font-weight: 650;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 7px; }}

        QLineEdit, QComboBox, QPlainTextEdit, QListWidget, QTableWidget {{
            background: {input_bg}; border: 1px solid {border}; border-radius: 10px;
            selection-background-color: {accent2}; selection-color: white;
        }}
        QLineEdit, QComboBox {{ padding: 8px 10px; min-height: 22px; }}
        QComboBox::drop-down {{ border: 0; width: 26px; }}
        QPlainTextEdit {{ padding: 9px; }}
        QTableWidget {{ gridline-color: {border}; }}
        QHeaderView::section {{
            background: {panel_soft}; color: {muted}; border: 0; border-bottom: 1px solid {border};
            padding: 7px 8px; font-weight: 600;
        }}
        QTableWidget::item, QListWidget::item {{ padding: 7px; }}
        QTableWidget::item:selected, QListWidget::item:selected {{
            background: {accent2}; color: white; border-radius: 6px;
        }}

        QPushButton {{
            background: {panel_soft}; color: {text}; border: 1px solid {border};
            border-radius: 10px; padding: 8px 13px; font-weight: 600;
        }}
        QPushButton:hover {{ border-color: {accent}; background: {input_bg}; }}
        QPushButton:pressed {{ padding-top: 9px; padding-bottom: 7px; }}
        QPushButton:disabled {{ color: {muted}; background: {panel_soft}; border-color: {border}; }}
        QPushButton#startButton {{
            background: {accent}; color: white; border-color: {accent}; padding: 10px 18px;
        }}
        QPushButton#startButton:hover {{ background: {hover}; }}
        QPushButton#themeButton {{ padding: 8px 12px; }}

        QProgressBar {{
            background: transparent; border: 1px solid {border}; border-radius: 7px;
            text-align: center; min-height: 16px;
        }}
        QProgressBar::chunk {{
            border-radius: 6px;
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2});
        }}
        QCheckBox {{ spacing: 8px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; }}

        QScrollArea, QAbstractScrollArea {{ border: 0; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 28px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; }}
        QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 28px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QToolTip {{ background: {input_bg}; color: {text}; border: 1px solid {border}; padding: 6px; }}
    """)
